"""AWS Athena connector using boto3 and PyAthena with connection-level S3 staging configuration."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Generator

import boto3
from pyathena import connect as pyathena_connect
from pyathena.cursor import DictCursor

from data_validator.connectors.base import (
    BaseConnector,
    ColumnMetadata,
    ColumnStats,
    ConnectionTestResult,
    ConnectorCapabilities,
    TableMetadata,
)
from data_validator.security.identifiers import quote_identifier, quote_qualified_table


class AWSAthenaConnector(BaseConnector):
    """AWS Athena connector with workgroup and S3 staging directory configured inside connection."""

    def __init__(self, connection_id: str, config: dict[str, Any]) -> None:
        super().__init__(connection_id, config)
        self.region_name = self.config.get("region_name", "ap-south-1")
        self.profile_name = self.config.get("profile_name")
        self.work_group = self.config.get("workgroup", "primary")
        self.s3_staging_dir = self.config.get(
            "s3_staging_dir", "s3://migration-validator-query-results/staging/"
        )
        self.catalog_default = self.config.get("catalog", "AwsDataCatalog")
        self.database_default = self.config.get("database", "default")
        self.aws_access_key_id = self.config.get("aws_access_key_id")
        self.aws_secret_access_key = self.config.get("aws_secret_access_key")

        self._session: boto3.Session | None = None
        self._athena_client: Any = None
        self._glue_client: Any = None

    def _get_session(self) -> boto3.Session:
        if self._session is None:
            kwargs: dict[str, Any] = {"region_name": self.region_name}
            if self.profile_name:
                kwargs["profile_name"] = self.profile_name
            elif self.aws_access_key_id and self.aws_secret_access_key:
                kwargs["aws_access_key_id"] = self.aws_access_key_id
                kwargs["aws_secret_access_key"] = self.aws_secret_access_key
            self._session = boto3.Session(**kwargs)
        return self._session

    def _get_athena_client(self) -> Any:
        if self._athena_client is None:
            self._athena_client = self._get_session().client("athena")
        return self._athena_client

    def _get_glue_client(self) -> Any:
        if self._glue_client is None:
            self._glue_client = self._get_session().client("glue")
        return self._glue_client

    def _get_pyathena_conn(self, schema_name: str | None = None) -> Any:
        session = self._get_session()
        creds = session.get_credentials()
        return pyathena_connect(
            s3_staging_dir=self.s3_staging_dir,
            region_name=self.region_name,
            work_group=self.work_group,
            schema_name=schema_name or self.database_default,
            aws_access_key_id=creds.access_key if creds else None,
            aws_secret_access_key=creds.secret_key if creds else None,
            aws_session_token=creds.token if creds else None,
            cursor_class=DictCursor,
        )

    def test_connection(self) -> ConnectionTestResult:
        start = time.perf_counter()
        try:
            client = self._get_athena_client()
            # Verify Athena permissions by calling list_work_groups or checking specified workgroup
            response = client.get_work_group(WorkGroup=self.work_group)
            status = response.get("WorkGroup", {}).get("State", "UNKNOWN")
            latency = (time.perf_counter() - start) * 1000.0

            return ConnectionTestResult(
                success=True,
                message=f"AWS Athena connection verified (Workgroup '{self.work_group}' is {status}).",
                latency_ms=latency,
                server_version=f"Athena Engine v3 / AWS {self.region_name}",
            )
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message=f"AWS Athena connection check failed: {e}",
                error_details=str(e),
            )

    def list_catalogs(self) -> list[str]:
        try:
            client = self._get_athena_client()
            resp = client.list_data_catalogs()
            return [c["CatalogName"] for c in resp.get("DataCatalogsSummary", [])]
        except Exception:
            return [self.catalog_default]

    def list_databases(self, catalog: str | None = None) -> list[str]:
        target_catalog = catalog or self.catalog_default
        try:
            client = self._get_athena_client()
            paginator = client.get_paginator("list_databases")
            dbs = []
            for page in paginator.paginate(CatalogName=target_catalog):
                for db in page.get("DatabaseList", []):
                    dbs.append(db["Name"])
            return sorted(dbs)
        except Exception:
            # Fallback to glue
            try:
                glue = self._get_glue_client()
                resp = glue.get_databases()
                return sorted([d["Name"] for d in resp.get("DatabaseList", [])])
            except Exception:
                return [self.database_default]

    def list_schemas(
        self, database: str | None = None, catalog: str | None = None
    ) -> list[str]:
        # In Athena / AWS Glue, database is equivalent to schema
        return ["default"]

    def list_tables(
        self,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
    ) -> list[str]:
        target_db = database or self.database_default
        try:
            glue = self._get_glue_client()
            paginator = glue.get_paginator("get_tables")
            tables = []
            for page in paginator.paginate(DatabaseName=target_db):
                for t in page.get("TableList", []):
                    tables.append(t["Name"])
            return sorted(tables)
        except Exception:
            # Query pyathena
            conn = self._get_pyathena_conn(target_db)
            with conn.cursor() as cur:
                cur.execute(f"SHOW TABLES IN {quote_identifier(target_db, dialect='athena')}")
                return sorted([list(r.values())[0] for r in cur.fetchall()])

    def get_table_metadata(
        self,
        table: str,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
    ) -> TableMetadata:
        target_db = database or self.database_default
        target_cat = catalog or self.catalog_default

        columns: list[ColumnMetadata] = []
        pks: list[str] = []
        est_rows: int | None = None

        try:
            glue = self._get_glue_client()
            resp = glue.get_table(DatabaseName=target_db, Name=table)
            tbl_info = resp.get("Table", {})
            storage_cols = tbl_info.get("StorageDescriptor", {}).get("Columns", [])
            partition_keys = tbl_info.get("PartitionKeys", [])

            for idx, col in enumerate(storage_cols + partition_keys, start=1):
                col_name = col["Name"]
                col_type = col["Type"]
                columns.append(
                    ColumnMetadata(
                        name=col_name,
                        data_type=col_type,
                        nullable=True,
                        is_primary_key=False,
                        ordinal_position=idx,
                    )
                )

            # Glue table parameters may contain row count stats
            params = tbl_info.get("Parameters", {})
            if "numRows" in params:
                try:
                    est_rows = int(params["numRows"])
                except Exception:
                    est_rows = None

        except Exception:
            # Fallback to information_schema via pyathena
            conn = self._get_pyathena_conn(target_db)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT column_name, data_type, is_nullable, ordinal_position
                    FROM information_schema.columns
                    WHERE table_schema = '{target_db}' AND table_name = '{table}'
                    ORDER BY ordinal_position
                    """
                )
                for r in cur.fetchall():
                    columns.append(
                        ColumnMetadata(
                            name=r["column_name"],
                            data_type=r["data_type"],
                            nullable=(r["is_nullable"].upper() == "YES"),
                            ordinal_position=r["ordinal_position"],
                        )
                    )

        return TableMetadata(
            catalog=target_cat,
            database=target_db,
            schema=schema,
            table=table,
            columns=columns,
            primary_keys=pks,
            estimated_row_count=est_rows,
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
        target_db = database or self.database_default
        tbl_q = f"{quote_identifier(target_db, dialect='athena')}.{quote_identifier(table, dialect='athena')}"
        query = f"SELECT COUNT(*) as cnt FROM {tbl_q}"
        if filter_expr and filter_expr.strip():
            query += f" WHERE {filter_expr}"

        conn = self._get_pyathena_conn(target_db)
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
            return list(row.values())[0] if row else 0

    def get_column_stats(
        self,
        table: str,
        column: str,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
        filter_expr: str | None = None,
    ) -> ColumnStats:
        target_db = database or self.database_default
        tbl_q = f"{quote_identifier(target_db, dialect='athena')}.{quote_identifier(table, dialect='athena')}"
        col_q = quote_identifier(column, dialect="athena")

        query = (
            f"SELECT MIN({col_q}) as min_val, MAX({col_q}) as max_val, "
            f"SUM(CASE WHEN {col_q} IS NULL THEN 1 ELSE 0 END) as null_cnt, "
            f"COUNT(DISTINCT {col_q}) as dist_cnt FROM {tbl_q}"
        )
        if filter_expr and filter_expr.strip():
            query += f" WHERE {filter_expr}"

        conn = self._get_pyathena_conn(target_db)
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
            if not row:
                return ColumnStats()
            return ColumnStats(
                min_value=row.get("min_val"),
                max_value=row.get("max_val"),
                null_count=row.get("null_cnt") or 0,
                distinct_count=row.get("dist_cnt"),
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
        target_db = database or self.database_default
        tbl_q = f"{quote_identifier(target_db, dialect='athena')}.{quote_identifier(table, dialect='athena')}"

        cols_part = "*"
        if columns:
            cols_part = ", ".join(quote_identifier(c, dialect="athena") for c in columns)

        query = f"SELECT {cols_part} FROM {tbl_q}"
        if filter_expr and filter_expr.strip():
            query += f" WHERE {filter_expr}"

        conn = self._get_pyathena_conn(target_db)
        with conn.cursor() as cur:
            cur.execute(query)
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                yield [dict(r) for r in rows]

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_schemas=False,
            supports_catalogs=True,
            supports_read_only_transactions=True,
            supports_server_side_cursors=False,
            supports_cancellation=True,
            supports_estimates=True,
            notes="AWS Athena connector with workgroup & S3 result staging.",
        )

    def close(self) -> None:
        pass
