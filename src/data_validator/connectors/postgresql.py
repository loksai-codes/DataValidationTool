"""PostgreSQL connector using psycopg v3 with safe read-only queries and metadata discovery."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Generator

import psycopg
from psycopg.rows import dict_row

from data_validator.connectors.base import (
    BaseConnector,
    ColumnMetadata,
    ColumnStats,
    ConnectionTestResult,
    ConnectorCapabilities,
    TableMetadata,
)
from data_validator.security.identifiers import quote_identifier, quote_qualified_table


class PostgreSQLConnector(BaseConnector):
    """PostgreSQL database connector supporting batched streaming and discovery."""

    def __init__(self, connection_id: str, config: dict[str, Any]) -> None:
        super().__init__(connection_id, config)
        self.host = self.config.get("host", "localhost")
        self.port = int(self.config.get("port", 5432))
        self.database = self.config.get("database", "postgres")
        self.username = self.config.get("username", "postgres")
        self.password = self.config.get("password", "")
        self.sslmode = self.config.get("sslmode", "prefer")
        self.schema_default = self.config.get("schema", "public")
        self._conn: psycopg.Connection | None = None

    def _get_connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(
                host=self.host,
                port=self.port,
                dbname=self.database,
                user=self.username,
                password=self.password,
                sslmode=self.sslmode,
                row_factory=dict_row,
                autocommit=True,
            )
        return self._conn

    def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            conn = self._get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                row = cur.fetchone()
                version = row["version"] if row else "PostgreSQL"
            latency = (time.perf_counter() - start) * 1000.0
            return ConnectionTestResult(
                success=True,
                message="PostgreSQL connection successful.",
                latency_ms=latency,
                server_version=str(version)[:60],
            )
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message=f"PostgreSQL connection failed: {e}",
                error_details=str(e),
            )

    def list_catalogs(self) -> list[str]:
        return [self.database]

    def list_databases(self, catalog: str | None = None) -> list[str]:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;"
            )
            return [r["datname"] for r in cur.fetchall()]

    def list_schemas(
        self, database: str | None = None, catalog: str | None = None
    ) -> list[str]:
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT schema_name FROM information_schema.schemata
                WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                ORDER BY schema_name;
                """
            )
            return [r["schema_name"] for r in cur.fetchall()]

    def list_tables(
        self,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
    ) -> list[str]:
        target_schema = schema or self.schema_default
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE'
                ORDER BY table_name;
                """,
                (target_schema,),
            )
            return [r["table_name"] for r in cur.fetchall()]

    def get_table_metadata(
        self,
        table: str,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
    ) -> TableMetadata:
        target_schema = schema or self.schema_default
        conn = self._get_connection()

        # Columns and data types
        columns: list[ColumnMetadata] = []
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable, ordinal_position,
                       numeric_precision, numeric_scale
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position;
                """,
                (target_schema, table),
            )
            col_rows = cur.fetchall()

            # Primary keys
            cur.execute(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema = %s
                  AND tc.table_name = %s;
                """,
                (target_schema, table),
            )
            pk_rows = cur.fetchall()
            pks = [r["column_name"] for r in pk_rows]

            for r in col_rows:
                col_name = r["column_name"]
                columns.append(
                    ColumnMetadata(
                        name=col_name,
                        data_type=r["data_type"],
                        nullable=(r["is_nullable"].upper() == "YES"),
                        is_primary_key=(col_name in pks),
                        ordinal_position=r["ordinal_position"],
                        precision=r["numeric_precision"],
                        scale=r["numeric_scale"],
                    )
                )

            # Estimate row count from pg_class
            cur.execute(
                """
                SELECT reltuples::bigint AS estimate
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s;
                """,
                (target_schema, table),
            )
            est_row = cur.fetchone()
            estimate = est_row["estimate"] if est_row and est_row["estimate"] is not None and est_row["estimate"] >= 0 else None

        return TableMetadata(
            catalog=catalog or self.database,
            database=database or self.database,
            schema=target_schema,
            table=table,
            columns=columns,
            primary_keys=pks,
            estimated_row_count=estimate,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    def get_filtered_row_count(
        self,
        table: str,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
        filter_expr: str | None = None,
    ) -> int:
        target_schema = schema or self.schema_default
        tbl_q = quote_qualified_table(None, target_schema, table, dialect="postgres")
        query = f"SELECT COUNT(*) as cnt FROM {tbl_q}"
        if filter_expr and filter_expr.strip():
            query += f" WHERE {filter_expr}"

        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
            return row["cnt"] if row else 0

    def get_column_stats(
        self,
        table: str,
        column: str,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
        filter_expr: str | None = None,
    ) -> ColumnStats:
        target_schema = schema or self.schema_default
        tbl_q = quote_qualified_table(None, target_schema, table, dialect="postgres")
        col_q = quote_identifier(column, dialect="postgres")

        query = (
            f"SELECT MIN({col_q}) as min_val, MAX({col_q}) as max_val, "
            f"SUM(CASE WHEN {col_q} IS NULL THEN 1 ELSE 0 END) as null_cnt, "
            f"COUNT(DISTINCT {col_q}) as dist_cnt FROM {tbl_q}"
        )
        if filter_expr and filter_expr.strip():
            query += f" WHERE {filter_expr}"

        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
            if not row:
                return ColumnStats()
            return ColumnStats(
                min_value=row["min_val"],
                max_value=row["max_val"],
                null_count=row["null_cnt"] or 0,
                distinct_count=row["dist_cnt"],
            )

    def fetch_records_batch(
        self,
        table: str,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
        columns: list[str] | None = None,
        filter_expr: str | None = None,
        batch_size: int = 5000,
    ) -> Generator[list[dict[str, Any]], None, None]:
        target_schema = schema or self.schema_default
        tbl_q = quote_qualified_table(None, target_schema, table, dialect="postgres")

        cols_part = "*"
        if columns:
            cols_part = ", ".join(quote_identifier(c, dialect="postgres") for c in columns)

        query = f"SELECT {cols_part} FROM {tbl_q}"
        if filter_expr and filter_expr.strip():
            query += f" WHERE {filter_expr}"

        conn = self._get_connection()
        # Set transaction read only
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY;")
                cur.execute(query)
                while True:
                    rows = cur.fetchmany(batch_size)
                    if not rows:
                        break
                    yield [dict(r) for r in rows]

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_schemas=True,
            supports_catalogs=False,
            supports_read_only_transactions=True,
            supports_server_side_cursors=True,
            supports_cancellation=True,
            supports_estimates=True,
            notes="PostgreSQL connector with psycopg3 read-only streaming.",
        )

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
