"""
PostgreSQL destination connector using SQLAlchemy 2.x and Psycopg 3.
Implements safe identifier quoting, pushdown aggregates, and keyset pagination.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from migration_validator.config import PostgresConfig
from migration_validator.connectors.base import (
    BaseConnector,
    ColumnMetadata,
    ConnectionTestResult,
    KeySummary,
    TableSchema,
)
from migration_validator.connectors.engine_pool import get_engine_pool
from migration_validator.logging_config import get_logger

logger = get_logger("connectors.postgres")


def _quote_ident(name: str) -> str:
    """Safely quote a SQL identifier to prevent SQL injection."""
    cleaned = name.replace('"', '""')
    return f'"{cleaned}"'


class PostgresConnector(BaseConnector):
    """Production-grade PostgreSQL connector for destination validation."""

    def __init__(self, config: PostgresConfig):
        self.config = config
        self._engine: Optional[Engine] = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = get_engine_pool().get_engine(
                self.config.connection_url,
                pool_size=5,
                max_overflow=10,
                connect_timeout=10,
            )
        return self._engine

    def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            with self.engine.connect() as conn:
                res = conn.execute(text("SELECT version();")).scalar_one()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return ConnectionTestResult(
                is_successful=True,
                service_name="PostgreSQL",
                message=f"Connected successfully ({res.split()[0]} {res.split()[1]})",
                latency_ms=round(elapsed_ms, 2),
                details={"version": res, "database": self.config.database, "host": self.config.host}
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            logger.error("PostgreSQL connection test failed: %s", str(e))
            return ConnectionTestResult(
                is_successful=False,
                service_name="PostgreSQL",
                message=f"Connection failed: {str(e)}",
                latency_ms=round(elapsed_ms, 2),
                error_code=type(e).__name__
            )

    def get_databases_or_schemas(self) -> List[str]:
        query = text("""
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            ORDER BY schema_name;
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(query).fetchall()
        return [r[0] for r in rows]

    def get_tables(self, schema_or_db: Optional[str] = "public") -> List[str]:
        schema = schema_or_db or "public"
        query = text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = :schema AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(query, {"schema": schema}).fetchall()
        return [r[0] for r in rows]

    def get_table_schema(self, table_name: str, schema_or_db: Optional[str] = "public") -> TableSchema:
        schema = schema_or_db or "public"
        query = text("""
            SELECT
                column_name,
                data_type,
                is_nullable,
                ordinal_position,
                character_maximum_length,
                numeric_precision,
                numeric_scale
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            ORDER BY ordinal_position;
        """)
        columns: Dict[str, ColumnMetadata] = {}
        with self.engine.connect() as conn:
            rows = conn.execute(query, {"schema": schema, "table": table_name}).mappings().fetchall()

        for r in rows:
            columns[r["column_name"]] = ColumnMetadata(
                name=r["column_name"],
                data_type=r["data_type"],
                is_nullable=(r["is_nullable"] == "YES"),
                ordinal_position=r["ordinal_position"],
                character_maximum_length=r["character_maximum_length"],
                numeric_precision=r["numeric_precision"],
                numeric_scale=r["numeric_scale"]
            )

        return TableSchema(
            database_name=self.config.database,
            schema_name=schema,
            table_name=table_name,
            columns=columns
        )

    def get_record_count(self, table_name: str, schema_or_db: Optional[str] = "public") -> int:
        schema = schema_or_db or "public"
        full_table = f"{_quote_ident(schema)}.{_quote_ident(table_name)}"
        query = text(f"SELECT COUNT(*) FROM {full_table};")
        with self.engine.connect() as conn:
            cnt = conn.execute(query).scalar_one()
        return int(cnt)

    def get_null_counts(
        self,
        table_name: str,
        columns: List[str],
        schema_or_db: Optional[str] = "public"
    ) -> Dict[str, int]:
        if not columns:
            return {}
        schema = schema_or_db or "public"
        full_table = f"{_quote_ident(schema)}.{_quote_ident(table_name)}"

        select_parts = [
            f'COUNT(CASE WHEN {_quote_ident(col)} IS NULL THEN 1 END) AS {_quote_ident(col + "_nulls")}'
            for col in columns
        ]
        sql = f"SELECT {', '.join(select_parts)} FROM {full_table};"

        with self.engine.connect() as conn:
            row = conn.execute(text(sql)).mappings().one()

        return {col: int(row[col + "_nulls"]) for col in columns}

    def get_aggregates(
        self,
        table_name: str,
        numeric_columns: List[str],
        schema_or_db: Optional[str] = "public"
    ) -> Dict[str, Dict[str, float]]:
        if not numeric_columns:
            return {}
        schema = schema_or_db or "public"
        full_table = f"{_quote_ident(schema)}.{_quote_ident(table_name)}"

        select_parts = []
        for col in numeric_columns:
            q_col = _quote_ident(col)
            select_parts.append(f"COALESCE(SUM({q_col}), 0) AS {_quote_ident(col + '__sum')}")
            select_parts.append(f"COALESCE(AVG({q_col}), 0) AS {_quote_ident(col + '__avg')}")
            select_parts.append(f"MIN({q_col}) AS {_quote_ident(col + '__min')}")
            select_parts.append(f"MAX({q_col}) AS {_quote_ident(col + '__max')}")

        sql = f"SELECT {', '.join(select_parts)} FROM {full_table};"
        with self.engine.connect() as conn:
            row = conn.execute(text(sql)).mappings().one()

        results: Dict[str, Dict[str, float]] = {}
        for col in numeric_columns:
            s_val = row[col + "__sum"]
            a_val = row[col + "__avg"]
            min_val = row[col + "__min"]
            max_val = row[col + "__max"]

            results[col] = {
                "sum": float(s_val) if s_val is not None else 0.0,
                "avg": float(a_val) if a_val is not None else 0.0,
                "min": float(min_val) if min_val is not None else 0.0,
                "max": float(max_val) if max_val is not None else 0.0,
            }
        return results

    def get_distinct_counts(
        self,
        table_name: str,
        columns: List[str],
        schema_or_db: Optional[str] = "public"
    ) -> Dict[str, int]:
        if not columns:
            return {}
        schema = schema_or_db or "public"
        full_table = f"{_quote_ident(schema)}.{_quote_ident(table_name)}"

        select_parts = [
            f"COUNT(DISTINCT {_quote_ident(col)}) AS {_quote_ident(col + '__distinct')}"
            for col in columns
        ]
        sql = f"SELECT {', '.join(select_parts)} FROM {full_table};"

        with self.engine.connect() as conn:
            row = conn.execute(text(sql)).mappings().one()

        return {col: int(row[col + "__distinct"]) for col in columns}

    def get_key_summary(
        self,
        table_name: str,
        key_columns: List[str],
        schema_or_db: Optional[str] = "public"
    ) -> KeySummary:
        schema = schema_or_db or "public"
        full_table = f"{_quote_ident(schema)}.{_quote_ident(table_name)}"

        if len(key_columns) == 1:
            k = _quote_ident(key_columns[0])
            sql = f"""
                SELECT
                    COUNT(*) AS total_records,
                    COUNT(DISTINCT {k}) AS distinct_keys,
                    COUNT(CASE WHEN {k} IS NULL THEN 1 END) AS null_keys,
                    MIN({k}) AS min_key,
                    MAX({k}) AS max_key
                FROM {full_table};
            """
        else:
            # Composite keys: concatenate key columns for distinct and null evaluation
            cols_concat = ", ".join([f"COALESCE({_quote_ident(c)}::text, '')" for c in key_columns])
            null_cond = " OR ".join([f"{_quote_ident(c)} IS NULL" for c in key_columns])
            sql = f"""
                SELECT
                    COUNT(*) AS total_records,
                    COUNT(DISTINCT CONCAT_WS('||', {cols_concat})) AS distinct_keys,
                    COUNT(CASE WHEN {null_cond} THEN 1 END) AS null_keys,
                    NULL AS min_key,
                    NULL AS max_key
                FROM {full_table};
            """

        with self.engine.connect() as conn:
            row = conn.execute(text(sql)).mappings().one()

        total = int(row["total_records"])
        distinct = int(row["distinct_keys"])
        nulls = int(row["null_keys"])
        duplicates = max(0, total - nulls - distinct)

        return KeySummary(
            total_records=total,
            distinct_keys=distinct,
            null_keys=nulls,
            duplicate_keys=duplicates,
            min_key=row["min_key"],
            max_key=row["max_key"]
        )

    def fetch_records_batch(
        self,
        table_name: str,
        columns: List[str],
        key_columns: List[str],
        batch_size: int,
        last_key: Optional[Any] = None,
        schema_or_db: Optional[str] = "public"
    ) -> Tuple[pd.DataFrame, Optional[Any]]:
        schema = schema_or_db or "public"
        full_table = f"{_quote_ident(schema)}.{_quote_ident(table_name)}"
        q_cols = ", ".join([_quote_ident(c) for c in columns])
        primary_key = key_columns[0]
        q_key = _quote_ident(primary_key)

        if last_key is None:
            sql = f"""
                SELECT {q_cols}
                FROM {full_table}
                ORDER BY {q_key} ASC
                LIMIT :limit;
            """
            params: Dict[str, Any] = {"limit": batch_size}
        else:
            sql = f"""
                SELECT {q_cols}
                FROM {full_table}
                WHERE {q_key} > :last_key
                ORDER BY {q_key} ASC
                LIMIT :limit;
            """
            params = {"last_key": last_key, "limit": batch_size}

        with self.engine.connect() as conn:
            df = pd.read_sql_query(text(sql), conn, params=params)

        if df.empty:
            return df, None

        next_cursor = df[primary_key].iloc[-1]
        return df, next_cursor

    def close(self) -> None:
        # Release local reference; pooled engine remains managed by EnginePool
        self._engine = None
