"""Synthetic in-memory/SQLite connector for tests, benchmarks, and demo runs."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Generator

from data_validator.connectors.base import (
    BaseConnector,
    ColumnMetadata,
    ColumnStats,
    ConnectionTestResult,
    ConnectorCapabilities,
    TableMetadata,
)
from data_validator.security.identifiers import quote_identifier


class SyntheticConnector(BaseConnector):
    """Robust synthetic connector that operates on an in-memory or local SQLite database."""

    def __init__(self, connection_id: str, config: dict[str, Any] | None = None) -> None:
        super().__init__(connection_id, config or {})
        db_path = self.config.get("db_path", ":memory:")
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._fault_injections: dict[str, Any] = {}

    def set_fault_injection(self, key: str, value: Any) -> None:
        self._fault_injections[key] = value

    def clear_fault_injections(self) -> None:
        self._fault_injections.clear()

    def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            cur = self._conn.execute("SELECT 1")
            cur.fetchone()
            latency = (time.perf_counter() - start) * 1000.0
            return ConnectionTestResult(
                success=True,
                message="Synthetic in-memory connection operational.",
                latency_ms=latency,
                server_version="SQLite Synthetic 1.0",
            )
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message=f"Synthetic connection failed: {e}",
                error_details=str(e),
            )

    def list_catalogs(self) -> list[str]:
        return ["default_catalog"]

    def list_databases(self, catalog: str | None = None) -> list[str]:
        return ["default_db", "migration_validator"]

    def list_schemas(
        self, database: str | None = None, catalog: str | None = None
    ) -> list[str]:
        return ["main", "public"]

    def list_tables(
        self,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
    ) -> list[str]:
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [r["name"] for r in cur.fetchall()]

    def get_table_metadata(
        self,
        table: str,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
    ) -> TableMetadata:
        cur = self._conn.execute(f"PRAGMA table_info({quote_identifier(table)})")
        rows = cur.fetchall()
        columns: list[ColumnMetadata] = []
        pks: list[str] = []

        for r in rows:
            is_pk = bool(r["pk"])
            col = ColumnMetadata(
                name=r["name"],
                data_type=r["type"] or "TEXT",
                nullable=not bool(r["notnull"]),
                is_primary_key=is_pk,
                ordinal_position=r["cid"],
            )
            columns.append(col)
            if is_pk:
                pks.append(r["name"])

        # Row count estimate
        count_cur = self._conn.execute(f"SELECT COUNT(*) as cnt FROM {quote_identifier(table)}")
        cnt_row = count_cur.fetchone()
        row_count = cnt_row["cnt"] if cnt_row else 0

        return TableMetadata(
            catalog=catalog or "default_catalog",
            database=database or "default_db",
            schema=schema or "main",
            table=table,
            columns=columns,
            primary_keys=pks,
            estimated_row_count=row_count,
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
        query = f"SELECT COUNT(*) as cnt FROM {quote_identifier(table)}"
        if filter_expr and filter_expr.strip():
            query += f" WHERE {filter_expr}"
        cur = self._conn.execute(query)
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
        col_q = quote_identifier(column)
        query = (
            f"SELECT MIN({col_q}) as min_val, MAX({col_q}) as max_val, "
            f"SUM(CASE WHEN {col_q} IS NULL THEN 1 ELSE 0 END) as null_cnt, "
            f"COUNT(DISTINCT {col_q}) as dist_cnt FROM {quote_identifier(table)}"
        )
        if filter_expr and filter_expr.strip():
            query += f" WHERE {filter_expr}"

        cur = self._conn.execute(query)
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
        if "simulated_fetch_error" in self._fault_injections:
            raise RuntimeError(self._fault_injections["simulated_fetch_error"])

        cols_part = "*"
        if columns:
            cols_part = ", ".join(quote_identifier(c) for c in columns)

        query = f"SELECT {cols_part} FROM {quote_identifier(table)}"
        if filter_expr and filter_expr.strip():
            query += f" WHERE {filter_expr}"

        cur = self._conn.execute(query)
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            batch = [dict(r) for r in rows]
            yield batch

    def seed_table(
        self,
        table_name: str,
        records: list[dict[str, Any]],
        column_types: dict[str, str] | None = None,
        primary_keys: list[str] | None = None,
    ) -> None:
        """Helper to create and populate a synthetic table for testing."""
        if not records:
            return

        if column_types is None:
            # Infer types
            column_types = {}
            first = records[0]
            for k, v in first.items():
                if isinstance(v, int):
                    column_types[k] = "INTEGER"
                elif isinstance(v, float):
                    column_types[k] = "REAL"
                else:
                    column_types[k] = "TEXT"

        pks = set(primary_keys or [])
        col_defs = []
        for col, c_type in column_types.items():
            col_def = f"{quote_identifier(col)} {c_type}"
            col_defs.append(col_def)

        if pks:
            pk_clause = f", PRIMARY KEY ({', '.join(quote_identifier(p) for p in pks)})"
        else:
            pk_clause = ""

        create_sql = f"CREATE TABLE IF NOT EXISTS {quote_identifier(table_name)} ({', '.join(col_defs)}{pk_clause})"
        self._conn.execute(create_sql)

        cols = list(column_types.keys())
        placeholders = ", ".join(["?"] * len(cols))
        quoted_cols = ", ".join(quote_identifier(c) for c in cols)
        insert_sql = f"INSERT INTO {quote_identifier(table_name)} ({quoted_cols}) VALUES ({placeholders})"

        with self._conn:
            for r in records:
                vals = [r.get(c) for c in cols]
                self._conn.execute(insert_sql, vals)

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_schemas=True,
            supports_catalogs=True,
            supports_read_only_transactions=True,
            supports_server_side_cursors=True,
            supports_cancellation=True,
            supports_estimates=True,
            notes="In-memory synthetic connector for validation testing.",
        )

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
