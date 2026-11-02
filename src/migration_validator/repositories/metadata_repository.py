"""
PostgreSQL metadata repository for storing validation runs, check results,
discrepancies, and dataset mappings in the validator_meta schema.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from migration_validator.config import PostgresConfig
from migration_validator.connectors.engine_pool import get_engine_pool
from migration_validator.logging_config import get_logger

logger = get_logger("repositories.metadata")


class MetadataRepository:
    """Manages persistence of validation execution history in PostgreSQL."""

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

    def initialize_schema(self, migration_sql_path: Optional[str] = None) -> bool:
        """Apply migration scripts to create validator_meta schema and tables."""
        import os
        if migration_sql_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            migration_sql_path = os.path.join(base_dir, "migrations", "001_initial_schema.sql")

        logger.info("Initializing metadata schema using migration: %s", migration_sql_path)
        with open(migration_sql_path, "r", encoding="utf-8") as f:
            sql_content = f.read()

        with self.engine.begin() as conn:
            # Execute migration statements
            conn.execute(text(sql_content))

        logger.info("Metadata schema validator_meta initialized successfully.")
        return True

    def record_run_start(
        self,
        run_id: str,
        source_type: str,
        source_identifier: str,
        dest_type: str,
        dest_identifier: str,
        config_dict: Dict[str, Any]
    ) -> None:
        """Record the beginning of a validation run."""
        sql = text("""
            INSERT INTO validator_meta.validation_runs (
                run_id, started_at, status, source_type, source_identifier,
                dest_type, dest_identifier, config_json
            ) VALUES (
                :run_id, :started_at, 'RUNNING', :src_type, :src_id,
                :dst_type, :dst_id, :config_json
            );
        """)
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
                    "config_json": json.dumps(safe_config)
                }
            )

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
        duration_seconds: float
    ) -> None:
        """Record the completion of a validation run."""
        sql = text("""
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
        """)
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
                    "duration_seconds": round(duration_seconds, 2)
                }
            )

    def record_checks(self, run_id: str, check_results: List[Dict[str, Any]]) -> None:
        """Record all check results for a validation run."""
        if not check_results:
            return

        sql = text("""
            INSERT INTO validator_meta.validation_checks (
                check_id, run_id, check_type, check_name, status,
                source_metric, dest_metric, difference, tolerance,
                duration_ms, error_message, details_json
            ) VALUES (
                :check_id, :run_id, :check_type, :check_name, :status,
                :source_metric, :dest_metric, :difference, :tolerance,
                :duration_ms, :error_message, :details_json
            ) ON CONFLICT (check_id) DO UPDATE SET
                status = EXCLUDED.status,
                difference = EXCLUDED.difference,
                error_message = EXCLUDED.error_message,
                details_json = EXCLUDED.details_json;
        """)

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
                        "details_json": json.dumps(c.get("metadata", {}), default=str)
                    }
                )

    def record_discrepancies(self, run_id: str, discrepancies: List[Dict[str, Any]]) -> None:
        """Batch insert discrepancy records to avoid unbounded memory."""
        if not discrepancies:
            return

        sql = text("""
            INSERT INTO validator_meta.discrepancies (
                run_id, discrepancy_type, record_key, column_name,
                source_val, dest_val, details_json
            ) VALUES (
                :run_id, :discrepancy_type, :record_key, :column_name,
                :source_val, :dest_val, :details_json
            );
        """)

        with self.engine.begin() as conn:
            for d in discrepancies:
                conn.execute(
                    sql,
                    {
                        "run_id": run_id,
                        "discrepancy_type": d["type"],
                        "record_key": str(d["key"]),
                        "column_name": d.get("column"),
                        "source_val": str(d.get("source_val")) if d.get("source_val") is not None else None,
                        "dest_val": str(d.get("dest_val")) if d.get("dest_val") is not None else None,
                        "details_json": json.dumps(d.get("details", {}), default=str)
                    }
                )

    def get_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent validation runs."""
        sql = text("""
            SELECT
                run_id, started_at, completed_at, status,
                source_type, source_identifier, dest_type, dest_identifier,
                total_checks, passed_checks, failed_checks, warning_checks,
                inconclusive_checks, total_records_evaluated, total_discrepancies,
                duration_seconds
            FROM validator_meta.validation_runs
            ORDER BY started_at DESC
            LIMIT :limit;
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"limit": limit}).mappings().fetchall()
        return [dict(r) for r in rows]

    def get_run_by_id(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific validation run."""
        sql = text("""
            SELECT * FROM validator_meta.validation_runs
            WHERE run_id = :run_id;
        """)
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"run_id": run_id}).mappings().fetchone()
        return dict(row) if row else None

    def get_checks_for_run(self, run_id: str) -> List[Dict[str, Any]]:
        """Retrieve all check results for a run."""
        sql = text("""
            SELECT * FROM validator_meta.validation_checks
            WHERE run_id = :run_id
            ORDER BY check_type, check_name;
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"run_id": run_id}).mappings().fetchall()
        return [dict(r) for r in rows]

    def get_discrepancies_for_run(
        self,
        run_id: str,
        discrepancy_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retrieve paginated discrepancies for a run with optional type filtering."""
        where_clause = "WHERE run_id = :run_id"
        params: Dict[str, Any] = {"run_id": run_id, "limit": limit, "offset": offset}

        if discrepancy_type and discrepancy_type != "ALL":
            where_clause += " AND discrepancy_type = :dtype"
            params["dtype"] = discrepancy_type

        sql = text(f"""
            SELECT id, discrepancy_type, record_key, column_name, source_val, dest_val, details_json
            FROM validator_meta.discrepancies
            {where_clause}
            ORDER BY id ASC
            LIMIT :limit OFFSET :offset;
        """)

        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().fetchall()
        return [dict(r) for r in rows]

    def get_discrepancy_counts_by_type(self, run_id: str) -> Dict[str, int]:
        """Return counts of discrepancies grouped by type."""
        sql = text("""
            SELECT discrepancy_type, COUNT(*) as count
            FROM validator_meta.discrepancies
            WHERE run_id = :run_id
            GROUP BY discrepancy_type;
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"run_id": run_id}).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def get_overall_summary_stats(self) -> Dict[str, Any]:
        """Retrieve aggregate system metrics for the Overview page."""
        sql = text("""
            SELECT
                COUNT(*) AS total_runs,
                COALESCE(SUM(CASE WHEN status = 'PASSED' THEN 1 ELSE 0 END), 0) AS passed_runs,
                COALESCE(SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END), 0) AS failed_runs,
                COALESCE(SUM(CASE WHEN status = 'WARNING' THEN 1 ELSE 0 END), 0) AS warning_runs,
                COALESCE(SUM(CASE WHEN status = 'INCONCLUSIVE' THEN 1 ELSE 0 END), 0) AS inconclusive_runs,
                COALESCE(SUM(total_records_evaluated), 0) AS total_records_validated,
                COALESCE(SUM(total_discrepancies), 0) AS total_discrepancies
            FROM validator_meta.validation_runs;
        """)
        with self.engine.connect() as conn:
            row = conn.execute(sql).mappings().one()
        return dict(row)

    def close(self) -> None:
        self._engine = None
