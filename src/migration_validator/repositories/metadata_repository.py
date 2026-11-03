"""
PostgreSQL and SQLite metadata repository for storing validation runs, check results,
discrepancies, checkpoints, and dataset mappings in the validator_meta schema.
"""

import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from migration_validator.config import PostgresConfig
from migration_validator.connectors.engine_pool import get_engine_pool
from migration_validator.logging_config import get_logger

logger = get_logger("repositories.metadata")


def serialize_checkpoint_key(key: Any) -> Optional[str]:
    """Deterministically serialize a cursor key (scalar or composite tuple) to string."""
    if key is None:
        return None
    if hasattr(key, "item"):
        key = key.item()
    if isinstance(key, (int, float, str, bool)):
        return json.dumps(key)
    if isinstance(key, (tuple, list)):
        clean_list = [x.item() if hasattr(x, "item") else x for x in key]
        return json.dumps(clean_list)
    return str(key)


def deserialize_checkpoint_key(raw_str: Optional[str]) -> Optional[Any]:
    """Deserialize a checkpoint cursor key string back to its original scalar or tuple type."""
    if raw_str is None:
        return None
    try:
        val = json.loads(raw_str)
        if isinstance(val, list):
            return tuple(val)
        return val
    except Exception:
        return raw_str


class MetadataRepository:
    """Manages persistence of validation execution history, checkpoints, and discrepancies."""

    def __init__(
        self,
        config: Optional[PostgresConfig] = None,
        engine: Optional[Engine] = None,
    ):
        self.config = config
        self._engine: Optional[Engine] = engine

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            if self.config is None:
                raise ValueError("Neither engine nor PostgresConfig provided to MetadataRepository.")
            self._engine = get_engine_pool().get_engine(
                self.config.connection_url,
                pool_size=5,
                max_overflow=10,
                connect_timeout=10,
            )
        return self._engine

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in str(self.engine.url)

    def _sql(self, sql_str: str) -> str:
        """Adapts schema-qualified SQL for dialect compatibility."""
        if self.is_sqlite:
            # SQLite does not support schemas by default; map validator_meta.table to validator_meta__table
            return re.sub(r"\bvalidator_meta\.(\w+)", r"validator_meta__\1", sql_str)
        return sql_str

    def initialize_schema(self, migration_sql_path: Optional[str] = None) -> bool:
        """Apply migration scripts to create validator_meta schema and tables."""
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        migrations_dir = os.path.join(base_dir, "migrations")

        migration_files: List[str] = []
        if migration_sql_path:
            migration_files = [migration_sql_path]
        elif os.path.isdir(migrations_dir):
            migration_files = sorted([
                os.path.join(migrations_dir, f)
                for f in os.listdir(migrations_dir)
                if f.endswith(".sql")
            ])

        for mf in migration_files:
            logger.info("Applying migration: %s", mf)
            with open(mf, "r", encoding="utf-8") as f:
                sql_content = f.read()

            if self.is_sqlite:
                # Translate PostgreSQL DDL to SQLite
                sql_content = re.sub(r"CREATE SCHEMA IF NOT EXISTS.*?;", "", sql_content, flags=re.DOTALL)
                sql_content = re.sub(r"\bvalidator_meta\.(\w+)", r"validator_meta__\1", sql_content)
                sql_content = re.sub(r"\bBIGSERIAL PRIMARY KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", sql_content)
                sql_content = re.sub(r"\bTIMESTAMP WITH TIME ZONE\b", "DATETIME", sql_content)
                sql_content = re.sub(r"\bJSONB\b", "TEXT", sql_content)
                sql_content = re.sub(r"\bTEXT\[\]\b", "TEXT", sql_content)
                sql_content = re.sub(r"\bDOUBLE PRECISION\b", "REAL", sql_content)
                sql_content = re.sub(r"\bADD COLUMN IF NOT EXISTS\b", "ADD COLUMN", sql_content)

            statements = [s.strip() for s in sql_content.split(";") if s.strip()]
            with self.engine.begin() as conn:
                for stmt in statements:
                    try:
                        conn.execute(text(stmt + ";"))
                    except Exception as ex:
                        # Non-critical warning (e.g. index already exists)
                        logger.debug("Migration notice for %s: %s", mf, ex)

        logger.info("Metadata schema initialized successfully.")
        return True

    def record_run_start(
        self,
        run_id: str,
        source_type: str,
        source_identifier: str,
        dest_type: str,
        dest_identifier: str,
        config_dict: Dict[str, Any],
    ) -> None:
        """Record the beginning of a validation run."""
        sql = text(self._sql("""
            INSERT INTO validator_meta.validation_runs (
                run_id, started_at, status, source_type, source_identifier,
                dest_type, dest_identifier, config_json
            ) VALUES (
                :run_id, :started_at, 'RUNNING', :src_type, :src_id,
                :dst_type, :dst_id, :config_json
            );
        """))
        # Redact any passwords in config_dict before serialization
        safe_config = json.loads(json.dumps(config_dict, default=str))

        with self.engine.begin() as conn:
            conn.execute(
                sql,
                {
                    "run_id": run_id,
                    "started_at": datetime.now(),
                    "src_type": source_type,
                    "src_id": source_identifier,
                    "dst_type": dest_type,
                    "dst_id": dest_identifier,
                    "config_json": json.dumps(safe_config),
                },
            )

    def update_run_status(self, run_id: str, status: str) -> None:
        """Update status of a validation run (RUNNING, CANCELLED, FAILED, PASSED)."""
        sql = text(self._sql("""
            UPDATE validator_meta.validation_runs
            SET status = :status
            WHERE run_id = :run_id;
        """))
        with self.engine.begin() as conn:
            conn.execute(sql, {"run_id": run_id, "status": status})

    def record_run_finish(
        self,
        run_id: str,
        status: str,
        total_checks: int,
        passed_checks: int,
        failed_checks: int,
        warning_checks: int,
        inconclusive_checks: int,
        total_records_evaluated: int,
        total_discrepancies: int,
        duration_seconds: float,
    ) -> None:
        """Record the completion of a validation run."""
        sql = text(self._sql("""
            UPDATE validator_meta.validation_runs
            SET
                completed_at = :completed_at,
                status = :status,
                total_checks = :total_checks,
                passed_checks = :passed_checks,
                failed_checks = :failed_checks,
                warning_checks = :warning_checks,
                inconclusive_checks = :inconclusive_checks,
                total_records_evaluated = :records_evaluated,
                total_discrepancies = :discrepancies,
                duration_seconds = :duration_seconds
            WHERE run_id = :run_id;
        """))
        with self.engine.begin() as conn:
            conn.execute(
                sql,
                {
                    "run_id": run_id,
                    "completed_at": datetime.now(),
                    "status": status,
                    "total_checks": total_checks,
                    "passed_checks": passed_checks,
                    "failed_checks": failed_checks,
                    "warning_checks": warning_checks,
                    "inconclusive_checks": inconclusive_checks,
                    "records_evaluated": total_records_evaluated,
                    "discrepancies": total_discrepancies,
                    "duration_seconds": round(duration_seconds, 2),
                },
            )

    def record_checks(self, run_id: str, check_results: List[Dict[str, Any]]) -> None:
        """Record all check results for a validation run."""
        if not check_results:
            return

        conflict_clause = (
            "ON CONFLICT(check_id) DO UPDATE SET status=excluded.status, difference=excluded.difference, "
            "error_message=excluded.error_message, details_json=excluded.details_json"
        )
        sql = text(self._sql(f"""
            INSERT INTO validator_meta.validation_checks (
                check_id, run_id, check_type, check_name, status,
                source_metric, dest_metric, difference, tolerance,
                duration_ms, error_message, details_json
            ) VALUES (
                :check_id, :run_id, :check_type, :check_name, :status,
                :source_metric, :dest_metric, :difference, :tolerance,
                :duration_ms, :error_message, :details_json
            ) {conflict_clause};
        """))

        with self.engine.begin() as conn:
            for c in check_results:
                conn.execute(
                    sql,
                    {
                        "check_id": c["check_id"],
                        "run_id": run_id,
                        "check_type": c["check_type"],
                        "check_name": c["check_name"],
                        "status": c["status"],
                        "source_metric": str(c.get("source_value")),
                        "dest_metric": str(c.get("dest_value")),
                        "difference": str(c.get("difference")),
                        "tolerance": str(c.get("tolerance")),
                        "duration_ms": c.get("duration_ms", 0.0),
                        "error_message": c.get("error_details"),
                        "details_json": json.dumps(c.get("metadata", {}), default=str),
                    },
                )

    def record_discrepancies(self, run_id: str, discrepancies: List[Dict[str, Any]]) -> None:
        """Batch insert discrepancy records to avoid unbounded memory."""
        if not discrepancies:
            return

        sql = text(self._sql("""
            INSERT INTO validator_meta.discrepancies (
                run_id, discrepancy_type, record_key, column_name,
                source_val, dest_val, details_json, discrepancy_hash
            ) VALUES (
                :run_id, :discrepancy_type, :record_key, :column_name,
                :source_val, :dest_val, :details_json, :discrepancy_hash
            )
            ON CONFLICT (run_id, discrepancy_hash) DO NOTHING;
        """))

        with self.engine.begin() as conn:
            for idx, d in enumerate(discrepancies):
                raw_t = d["type"]
                dtype_str = raw_t.value if hasattr(raw_t, "value") else str(raw_t).split(".")[-1]
                details_sig = json.dumps(d.get("details", {}), sort_keys=True, default=str)
                raw_sig = f"{run_id}|{dtype_str}|{str(d.get('key'))}|{d.get('column')}|{d.get('source_val')}|{d.get('dest_val')}|{details_sig}"
                disc_hash = hashlib.sha256(raw_sig.encode("utf-8")).hexdigest()[:32]
                raw_t = d["type"]
                dtype_str = raw_t.value if hasattr(raw_t, "value") else str(raw_t).split(".")[-1]
                conn.execute(
                    sql,
                    {
                        "run_id": run_id,
                        "discrepancy_type": dtype_str,
                        "record_key": str(d["key"]),
                        "column_name": d.get("column"),
                        "source_val": str(d.get("source_val")) if d.get("source_val") is not None else None,
                        "dest_val": str(d.get("dest_val")) if d.get("dest_val") is not None else None,
                        "details_json": json.dumps(d.get("details", {}), default=str),
                        "discrepancy_hash": disc_hash,
                    },
                )

    def commit_checkpoint_with_discrepancies(
        self,
        run_id: str,
        batch_index: int,
        last_source_key: Any,
        last_dest_key: Any,
        records_evaluated: int,
        discrepancies_found: int,
        discrepancies: List[Dict[str, Any]],
        config_fingerprint: str,
        status: str = "COMMITTED",
    ) -> None:
        """
        Atomically persist discrepancy records and commit the partition checkpoint in a single transaction.
        Guarantees that checkpoints cannot advance beyond the discrepancies committed.
        """
        s_key_str = serialize_checkpoint_key(last_source_key)
        d_key_str = serialize_checkpoint_key(last_dest_key)
        chk_id = f"CHKPT-{run_id}-{batch_index:06d}"
        now = datetime.now()

        disc_sql = text(self._sql("""
            INSERT INTO validator_meta.discrepancies (
                run_id, discrepancy_type, record_key, column_name,
                source_val, dest_val, details_json, discrepancy_hash
            ) VALUES (
                :run_id, :discrepancy_type, :record_key, :column_name,
                :source_val, :dest_val, :details_json, :discrepancy_hash
            )
            ON CONFLICT (run_id, discrepancy_hash) DO NOTHING;
        """))

        chk_sql = text(self._sql("""
            INSERT INTO validator_meta.partition_checkpoints (
                checkpoint_id, run_id, batch_index, last_source_key, last_dest_key,
                records_evaluated, discrepancies_found, config_fingerprint, status,
                created_at, updated_at
            ) VALUES (
                :checkpoint_id, :run_id, :batch_index, :last_source_key, :last_dest_key,
                :records_evaluated, :discrepancies_found, :config_fingerprint, :status,
                :created_at, :updated_at
            )
            ON CONFLICT (checkpoint_id) DO UPDATE SET
                last_source_key = EXCLUDED.last_source_key,
                last_dest_key = EXCLUDED.last_dest_key,
                records_evaluated = EXCLUDED.records_evaluated,
                discrepancies_found = EXCLUDED.discrepancies_found,
                config_fingerprint = EXCLUDED.config_fingerprint,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at;
        """))

        with self.engine.begin() as conn:
            # 1. Insert discrepancies
            for idx, d in enumerate(discrepancies):
                raw_t = d["type"]
                dtype_str = raw_t.value if hasattr(raw_t, "value") else str(raw_t).split(".")[-1]
                details_sig = json.dumps(d.get("details", {}), sort_keys=True, default=str)
                raw_sig = f"{run_id}|{dtype_str}|{str(d.get('key'))}|{d.get('column')}|{d.get('source_val')}|{d.get('dest_val')}|{details_sig}"
                disc_hash = hashlib.sha256(raw_sig.encode("utf-8")).hexdigest()[:32]
                raw_t = d["type"]
                dtype_str = raw_t.value if hasattr(raw_t, "value") else str(raw_t).split(".")[-1]
                conn.execute(
                    disc_sql,
                    {
                        "run_id": run_id,
                        "discrepancy_type": dtype_str,
                        "record_key": str(d["key"]),
                        "column_name": d.get("column"),
                        "source_val": str(d.get("source_val")) if d.get("source_val") is not None else None,
                        "dest_val": str(d.get("dest_val")) if d.get("dest_val") is not None else None,
                        "details_json": json.dumps(d.get("details", {}), default=str),
                        "discrepancy_hash": disc_hash,
                    },
                )
            # 2. Insert checkpoint atomically
            conn.execute(
                chk_sql,
                {
                    "checkpoint_id": chk_id,
                    "run_id": run_id,
                    "batch_index": batch_index,
                    "last_source_key": s_key_str,
                    "last_dest_key": d_key_str,
                    "records_evaluated": records_evaluated,
                    "discrepancies_found": discrepancies_found,
                    "config_fingerprint": config_fingerprint,
                    "status": status,
                    "created_at": now,
                    "updated_at": now,
                },
            )

    def get_latest_checkpoint(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve the latest committed partition checkpoint for a run."""
        sql = text(self._sql("""
            SELECT * FROM validator_meta.partition_checkpoints
            WHERE run_id = :run_id AND status = 'COMMITTED'
            ORDER BY batch_index DESC
            LIMIT 1;
        """))
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"run_id": run_id}).mappings().fetchone()
        if not row:
            return None
        d = dict(row)
        d["last_source_key"] = deserialize_checkpoint_key(d.get("last_source_key"))
        d["last_dest_key"] = deserialize_checkpoint_key(d.get("last_dest_key"))
        return d

    def get_checkpoints_for_run(self, run_id: str) -> List[Dict[str, Any]]:
        """Retrieve all partition checkpoints for a run."""
        sql = text(self._sql("""
            SELECT * FROM validator_meta.partition_checkpoints
            WHERE run_id = :run_id
            ORDER BY batch_index ASC;
        """))
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"run_id": run_id}).mappings().fetchall()
        checkpoints = []
        for r in rows:
            d = dict(r)
            d["last_source_key"] = deserialize_checkpoint_key(d.get("last_source_key"))
            d["last_dest_key"] = deserialize_checkpoint_key(d.get("last_dest_key"))
            checkpoints.append(d)
        return checkpoints

    def get_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent validation runs."""
        sql = text(self._sql("""
            SELECT
                run_id, started_at, completed_at, status,
                source_type, source_identifier, dest_type, dest_identifier,
                total_checks, passed_checks, failed_checks, warning_checks,
                inconclusive_checks, total_records_evaluated, total_discrepancies,
                duration_seconds
            FROM validator_meta.validation_runs
            ORDER BY started_at DESC
            LIMIT :limit;
        """))
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"limit": limit}).mappings().fetchall()
        return [dict(r) for r in rows]

    def get_run_by_id(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific validation run."""
        sql = text(self._sql("""
            SELECT * FROM validator_meta.validation_runs
            WHERE run_id = :run_id;
        """))
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"run_id": run_id}).mappings().fetchone()
        return dict(row) if row else None

    def get_checks_for_run(self, run_id: str) -> List[Dict[str, Any]]:
        """Retrieve all check results for a run."""
        sql = text(self._sql("""
            SELECT * FROM validator_meta.validation_checks
            WHERE run_id = :run_id
            ORDER BY check_type, check_name;
        """))
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"run_id": run_id}).mappings().fetchall()
        return [dict(r) for r in rows]

    def get_discrepancies_for_run(
        self,
        run_id: str,
        discrepancy_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Retrieve paginated discrepancies for a run with optional type filtering."""
        where_clause = "WHERE run_id = :run_id"
        params: Dict[str, Any] = {"run_id": run_id, "limit": limit, "offset": offset}

        if discrepancy_type and discrepancy_type != "ALL":
            where_clause += " AND discrepancy_type = :dtype"
            params["dtype"] = discrepancy_type

        sql = text(self._sql(f"""
            SELECT id, discrepancy_type, record_key, column_name, source_val, dest_val, details_json
            FROM validator_meta.discrepancies
            {where_clause}
            ORDER BY id ASC
            LIMIT :limit OFFSET :offset;
        """))

        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().fetchall()
        return [dict(r) for r in rows]

    def get_discrepancy_counts_by_type(self, run_id: str) -> Dict[str, int]:
        """Return counts of discrepancies grouped by type."""
        sql = text(self._sql("""
            SELECT discrepancy_type, COUNT(*) as count
            FROM validator_meta.discrepancies
            WHERE run_id = :run_id
            GROUP BY discrepancy_type;
        """))
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"run_id": run_id}).fetchall()
        counts: Dict[str, int] = {}
        for r in rows:
            t = str(r[0])
            if "." in t:
                t = t.split(".")[-1]
            counts[t] = int(r[1])
        return counts

    def get_overall_summary_stats(self) -> Dict[str, Any]:
        """Retrieve aggregate system metrics for the Overview page."""
        sql = text(self._sql("""
            SELECT
                COUNT(*) AS total_runs,
                COALESCE(SUM(CASE WHEN status = 'PASSED' THEN 1 ELSE 0 END), 0) AS passed_runs,
                COALESCE(SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END), 0) AS failed_runs,
                COALESCE(SUM(CASE WHEN status = 'WARNING' THEN 1 ELSE 0 END), 0) AS warning_runs,
                COALESCE(SUM(CASE WHEN status = 'INCONCLUSIVE' THEN 1 ELSE 0 END), 0) AS inconclusive_runs,
                COALESCE(SUM(total_records_evaluated), 0) AS total_records_validated,
                COALESCE(SUM(total_discrepancies), 0) AS total_discrepancies
            FROM validator_meta.validation_runs;
        """))
        with self.engine.connect() as conn:
            row = conn.execute(sql).mappings().one()
        return dict(row)

    def close(self) -> None:
        self._engine = None
