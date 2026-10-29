"""SQLite implementation of BaseRunRepository for durable validation persistence."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from data_validator.persistence.models import (
    CheckRecord,
    CheckStatus,
    DiscrepancyRecord,
    DiscrepancyType,
    EventLogRecord,
    PartitionRecord,
    PartitionStatus,
    ReportReference,
    RunRecord,
    RunStatus,
)
from data_validator.persistence.repository import BaseRunRepository


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


class SQLiteRunRepository(BaseRunRepository):
    """Production-grade SQLite-backed repository for validation runs and discrepancies."""

    def __init__(self, db_path: str | Path = "data/validation_metadata.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    job_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    source_metadata_json TEXT,
                    target_metadata_json TEXT,
                    total_partitions INTEGER DEFAULT 0,
                    completed_partitions INTEGER DEFAULT 0,
                    failed_partitions INTEGER DEFAULT 0,
                    rows_source INTEGER DEFAULT 0,
                    rows_target INTEGER DEFAULT 0,
                    rows_compared INTEGER DEFAULT 0,
                    missing_count INTEGER DEFAULT 0,
                    extra_count INTEGER DEFAULT 0,
                    changed_count INTEGER DEFAULT 0,
                    duplicate_key_count INTEGER DEFAULT 0,
                    null_key_count INTEGER DEFAULT 0,
                    error_message TEXT,
                    started_at TEXT,
                    last_heartbeat_at TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_checks (
                    check_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    check_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT,
                    details_json TEXT,
                    executed_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS partitions (
                    partition_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    partition_index INTEGER NOT NULL,
                    lower_bound TEXT,
                    upper_bound TEXT,
                    cursor_state TEXT,
                    status TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    attempts INTEGER DEFAULT 0,
                    rows_source INTEGER DEFAULT 0,
                    rows_target INTEGER DEFAULT 0,
                    rows_compared INTEGER DEFAULT 0,
                    missing_count INTEGER DEFAULT 0,
                    extra_count INTEGER DEFAULT 0,
                    changed_count INTEGER DEFAULT 0,
                    error_message TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS discrepancies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    partition_id TEXT NOT NULL,
                    discrepancy_type TEXT NOT NULL,
                    key_values TEXT NOT NULL,
                    column_name TEXT NOT NULL DEFAULT '',
                    source_value TEXT,
                    target_value TEXT,
                    details_json TEXT,
                    detected_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
                    UNIQUE(run_id, partition_id, discrepancy_type, key_values, column_name)
                );

                CREATE TABLE IF NOT EXISTS event_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    component TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS report_references (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    format TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
                    UNIQUE(run_id, format)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_partitions_run ON partitions(run_id, status);
                CREATE INDEX IF NOT EXISTS idx_discrepancies_run ON discrepancies(run_id, discrepancy_type);
                CREATE INDEX IF NOT EXISTS idx_events_run ON event_logs(run_id, id);
                """
            )

    def create_run(self, run: RunRecord) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, job_name, status, config_json, source_metadata_json,
                    target_metadata_json, total_partitions, completed_partitions,
                    failed_partitions, rows_source, rows_target, rows_compared,
                    missing_count, extra_count, changed_count, duplicate_key_count,
                    null_key_count, error_message, started_at, last_heartbeat_at,
                    completed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.job_name,
                    run.status.value,
                    run.config_json,
                    run.source_metadata_json,
                    run.target_metadata_json,
                    run.total_partitions,
                    run.completed_partitions,
                    run.failed_partitions,
                    run.rows_source,
                    run.rows_target,
                    run.rows_compared,
                    run.missing_count,
                    run.extra_count,
                    run.changed_count,
                    run.duplicate_key_count,
                    run.null_key_count,
                    run.error_message,
                    run.started_at.isoformat() if run.started_at else None,
                    run.last_heartbeat_at.isoformat() if run.last_heartbeat_at else None,
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.created_at.isoformat(),
                ),
            )
            conn.commit()

    def _row_to_run(self, row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            job_name=row["job_name"],
            status=RunStatus(row["status"]),
            config_json=row["config_json"],
            source_metadata_json=row["source_metadata_json"],
            target_metadata_json=row["target_metadata_json"],
            total_partitions=row["total_partitions"],
            completed_partitions=row["completed_partitions"],
            failed_partitions=row["failed_partitions"],
            rows_source=row["rows_source"],
            rows_target=row["rows_target"],
            rows_compared=row["rows_compared"],
            missing_count=row["missing_count"],
            extra_count=row["extra_count"],
            changed_count=row["changed_count"],
            duplicate_key_count=row["duplicate_key_count"],
            null_key_count=row["null_key_count"],
            error_message=row["error_message"],
            started_at=_parse_iso(row["started_at"]),
            last_heartbeat_at=_parse_iso(row["last_heartbeat_at"]),
            completed_at=_parse_iso(row["completed_at"]),
            created_at=_parse_iso(row["created_at"]) or datetime.now(timezone.utc),
        )

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connection() as conn:
            cursor = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_run(row)

    def update_run_status(
        self,
        run_id: str,
        status: RunStatus,
        error_message: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?,
                    error_message = COALESCE(?, error_message),
                    completed_at = COALESCE(?, completed_at),
                    last_heartbeat_at = ?
                WHERE run_id = ?
                """,
                (
                    status.value,
                    error_message,
                    completed_at.isoformat() if completed_at else None,
                    _utc_now_iso(),
                    run_id,
                ),
            )
            conn.commit()

    def update_run_heartbeat(self, run_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE runs SET last_heartbeat_at = ? WHERE run_id = ?",
                (_utc_now_iso(), run_id),
            )
            conn.commit()

    def list_runs(
        self,
        limit: int = 50,
        status: RunStatus | None = None,
        job_name: str | None = None,
    ) -> list[RunRecord]:
        query = "SELECT * FROM runs WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if job_name:
            query += " AND job_name LIKE ?"
            params.append(f"%{job_name}%")
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._connection() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_run(r) for r in cursor.fetchall()]

    def save_checks(self, checks: list[CheckRecord]) -> None:
        with self._connection() as conn:
            for chk in checks:
                conn.execute(
                    """
                    INSERT INTO run_checks (check_id, run_id, check_name, status, summary, details_json, executed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(check_id) DO UPDATE SET
                        status = excluded.status,
                        summary = excluded.summary,
                        details_json = excluded.details_json,
                        executed_at = excluded.executed_at
                    """,
                    (
                        chk.check_id,
                        chk.run_id,
                        chk.check_name,
                        chk.status.value,
                        chk.summary,
                        chk.details_json,
                        chk.executed_at.isoformat(),
                    ),
                )
            conn.commit()

    def get_checks(self, run_id: str) -> list[CheckRecord]:
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM run_checks WHERE run_id = ? ORDER BY executed_at ASC",
                (run_id,),
            )
            return [
                CheckRecord(
                    check_id=r["check_id"],
                    run_id=r["run_id"],
                    check_name=r["check_name"],
                    status=CheckStatus(r["status"]),
                    summary=r["summary"],
                    details_json=r["details_json"],
                    executed_at=_parse_iso(r["executed_at"]) or datetime.now(timezone.utc),
                )
                for r in cursor.fetchall()
            ]

    def save_partitions(self, partitions: list[PartitionRecord]) -> None:
        with self._connection() as conn:
            for part in partitions:
                conn.execute(
                    """
                    INSERT INTO partitions (
                        partition_id, run_id, partition_index, lower_bound, upper_bound,
                        cursor_state, status, lease_owner, lease_expires_at, attempts,
                        rows_source, rows_target, rows_compared, missing_count,
                        extra_count, changed_count, error_message, started_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(partition_id) DO UPDATE SET
                        lower_bound = excluded.lower_bound,
                        upper_bound = excluded.upper_bound,
                        cursor_state = excluded.cursor_state
                    """,
                    (
                        part.partition_id,
                        part.run_id,
                        part.partition_index,
                        part.lower_bound,
                        part.upper_bound,
                        part.cursor_state,
                        part.status.value,
                        part.lease_owner,
                        part.lease_expires_at.isoformat() if part.lease_expires_at else None,
                        part.attempts,
                        part.rows_source,
                        part.rows_target,
                        part.rows_compared,
                        part.missing_count,
                        part.extra_count,
                        part.changed_count,
                        part.error_message,
                        part.started_at.isoformat() if part.started_at else None,
                        part.completed_at.isoformat() if part.completed_at else None,
                    ),
                )
            if partitions:
                conn.execute(
                    """
                    UPDATE runs
                    SET total_partitions = (SELECT COUNT(*) FROM partitions WHERE run_id = ?)
                    WHERE run_id = ?
                    """,
                    (partitions[0].run_id, partitions[0].run_id),
                )
            conn.commit()

    def _row_to_partition(self, r: sqlite3.Row) -> PartitionRecord:
        return PartitionRecord(
            partition_id=r["partition_id"],
            run_id=r["run_id"],
            partition_index=r["partition_index"],
            lower_bound=r["lower_bound"],
            upper_bound=r["upper_bound"],
            cursor_state=r["cursor_state"],
            status=PartitionStatus(r["status"]),
            lease_owner=r["lease_owner"],
            lease_expires_at=_parse_iso(r["lease_expires_at"]),
            attempts=r["attempts"],
            rows_source=r["rows_source"],
            rows_target=r["rows_target"],
            rows_compared=r["rows_compared"],
            missing_count=r["missing_count"],
            extra_count=r["extra_count"],
            changed_count=r["changed_count"],
            error_message=r["error_message"],
            started_at=_parse_iso(r["started_at"]),
            completed_at=_parse_iso(r["completed_at"]),
        )

    def get_partitions(self, run_id: str) -> list[PartitionRecord]:
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM partitions WHERE run_id = ? ORDER BY partition_index ASC",
                (run_id,),
            )
            return [self._row_to_partition(r) for r in cursor.fetchall()]

    def get_unfinished_partitions(self, run_id: str) -> list[PartitionRecord]:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM partitions
                WHERE run_id = ? AND status != ?
                ORDER BY partition_index ASC
                """,
                (run_id, PartitionStatus.COMPLETED.value),
            )
            return [self._row_to_partition(r) for r in cursor.fetchall()]

    def acquire_partition_lease(
        self, partition_id: str, worker_id: str, lease_duration_seconds: int = 60
    ) -> bool:
        now_dt = datetime.now(timezone.utc)
        expires_dt = datetime.fromtimestamp(
            now_dt.timestamp() + lease_duration_seconds, tz=timezone.utc
        )
        now_iso = now_dt.isoformat()
        expires_iso = expires_dt.isoformat()

        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE partitions
                SET lease_owner = ?,
                    lease_expires_at = ?,
                    attempts = attempts + 1,
                    status = ?,
                    started_at = COALESCE(started_at, ?)
                WHERE partition_id = ?
                  AND (
                    status IN (?, ?)
                    OR lease_expires_at IS NULL
                    OR lease_expires_at < ?
                  )
                """,
                (
                    worker_id,
                    expires_iso,
                    PartitionStatus.RUNNING.value,
                    now_iso,
                    partition_id,
                    PartitionStatus.PENDING.value,
                    PartitionStatus.FAILED.value,
                    now_iso,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def commit_partition_results(
        self,
        partition_id: str,
        status: PartitionStatus,
        discrepancies: list[DiscrepancyRecord],
        rows_source: int,
        rows_target: int,
        rows_compared: int,
        missing_count: int,
        extra_count: int,
        changed_count: int,
        error_message: str | None = None,
    ) -> None:
        """Atomic commit of partition status and its detected discrepancies."""
        now_iso = _utc_now_iso()

        with self._connection() as conn:
            # 1. Update partition record
            conn.execute(
                """
                UPDATE partitions
                SET status = ?,
                    rows_source = ?,
                    rows_target = ?,
                    rows_compared = ?,
                    missing_count = ?,
                    extra_count = ?,
                    changed_count = ?,
                    error_message = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    completed_at = ?
                WHERE partition_id = ?
                """,
                (
                    status.value,
                    rows_source,
                    rows_target,
                    rows_compared,
                    missing_count,
                    extra_count,
                    changed_count,
                    error_message,
                    now_iso,
                    partition_id,
                ),
            )

            # Retrieve run_id for this partition
            run_cur = conn.execute(
                "SELECT run_id FROM partitions WHERE partition_id = ?",
                (partition_id,),
            )
            run_row = run_cur.fetchone()
            if not run_row:
                conn.commit()
                return
            run_id = run_row["run_id"]

            # 2. Insert discrepancies idempotently
            for disc in discrepancies:
                col_name = disc.column_name or ""
                conn.execute(
                    """
                    INSERT INTO discrepancies (
                        run_id, partition_id, discrepancy_type, key_values,
                        column_name, source_value, target_value, details_json,
                        detected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, partition_id, discrepancy_type, key_values, column_name)
                    DO UPDATE SET
                        source_value = excluded.source_value,
                        target_value = excluded.target_value,
                        details_json = excluded.details_json
                    """,
                    (
                        run_id,
                        partition_id,
                        disc.discrepancy_type.value,
                        disc.key_values,
                        col_name,
                        disc.source_value,
                        disc.target_value,
                        disc.details_json,
                        now_iso,
                    ),
                )

            # 3. Update aggregated counters on run
            conn.execute(
                """
                UPDATE runs
                SET completed_partitions = (
                        SELECT COUNT(*) FROM partitions
                        WHERE run_id = ? AND status = ?
                    ),
                    failed_partitions = (
                        SELECT COUNT(*) FROM partitions
                        WHERE run_id = ? AND status = ?
                    ),
                    rows_source = (
                        SELECT COALESCE(SUM(rows_source), 0) FROM partitions WHERE run_id = ?
                    ),
                    rows_target = (
                        SELECT COALESCE(SUM(rows_target), 0) FROM partitions WHERE run_id = ?
                    ),
                    rows_compared = (
                        SELECT COALESCE(SUM(rows_compared), 0) FROM partitions WHERE run_id = ?
                    ),
                    missing_count = (
                        SELECT COUNT(*) FROM discrepancies
                        WHERE run_id = ? AND discrepancy_type = ?
                    ),
                    extra_count = (
                        SELECT COUNT(*) FROM discrepancies
                        WHERE run_id = ? AND discrepancy_type = ?
                    ),
                    changed_count = (
                        SELECT COUNT(DISTINCT key_values) FROM discrepancies
                        WHERE run_id = ? AND discrepancy_type = ?
                    ),
                    duplicate_key_count = (
                        SELECT COUNT(*) FROM discrepancies
                        WHERE run_id = ? AND discrepancy_type = ?
                    ),
                    null_key_count = (
                        SELECT COUNT(*) FROM discrepancies
                        WHERE run_id = ? AND discrepancy_type = ?
                    ),
                    last_heartbeat_at = ?
                WHERE run_id = ?
                """,
                (
                    run_id,
                    PartitionStatus.COMPLETED.value,
                    run_id,
                    PartitionStatus.FAILED.value,
                    run_id,
                    run_id,
                    run_id,
                    run_id,
                    DiscrepancyType.MISSING_IN_TARGET.value,
                    run_id,
                    DiscrepancyType.EXTRA_IN_TARGET.value,
                    run_id,
                    DiscrepancyType.CHANGED_RECORD.value,
                    run_id,
                    DiscrepancyType.DUPLICATE_KEY.value,
                    run_id,
                    DiscrepancyType.NULL_KEY.value,
                    now_iso,
                    run_id,
                ),
            )
            conn.commit()

    def recover_stale_leases(self, timeout_seconds: int = 120) -> int:
        """Mark expired Running partitions as Failed/Pending, and dead Running runs as Interrupted."""
        now_dt = datetime.now(timezone.utc)
        cutoff_dt = datetime.fromtimestamp(
            now_dt.timestamp() - timeout_seconds, tz=timezone.utc
        )
        now_iso = now_dt.isoformat()
        cutoff_iso = cutoff_dt.isoformat()

        recovered_count = 0
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE partitions
                SET status = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error_message = 'Lease expired / worker interrupted'
                WHERE status = ?
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (PartitionStatus.FAILED.value, PartitionStatus.RUNNING.value, now_iso),
            )
            recovered_count += cursor.rowcount

            cursor2 = conn.execute(
                """
                UPDATE runs
                SET status = ?,
                    error_message = 'Process interrupted or heartbeat lost'
                WHERE status = ?
                  AND (last_heartbeat_at IS NULL OR last_heartbeat_at < ?)
                """,
                (RunStatus.INTERRUPTED.value, RunStatus.RUNNING.value, cutoff_iso),
            )
            recovered_count += cursor2.rowcount
            conn.commit()

        return recovered_count

    def get_discrepancies(
        self,
        run_id: str,
        discrepancy_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DiscrepancyRecord]:
        query = "SELECT * FROM discrepancies WHERE run_id = ?"
        params: list[Any] = [run_id]
        if discrepancy_type:
            query += " AND discrepancy_type = ?"
            params.append(discrepancy_type)
        query += " ORDER BY id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._connection() as conn:
            cursor = conn.execute(query, params)
            return [
                DiscrepancyRecord(
                    id=r["id"],
                    run_id=r["run_id"],
                    partition_id=r["partition_id"],
                    discrepancy_type=DiscrepancyType(r["discrepancy_type"]),
                    key_values=r["key_values"],
                    column_name=r["column_name"] if r["column_name"] else None,
                    source_value=r["source_value"],
                    target_value=r["target_value"],
                    details_json=r["details_json"],
                    detected_at=_parse_iso(r["detected_at"]) or datetime.now(timezone.utc),
                )
                for r in cursor.fetchall()
            ]

    def count_discrepancies(self, run_id: str, discrepancy_type: str | None = None) -> int:
        query = "SELECT COUNT(*) as cnt FROM discrepancies WHERE run_id = ?"
        params: list[Any] = [run_id]
        if discrepancy_type:
            query += " AND discrepancy_type = ?"
            params.append(discrepancy_type)

        with self._connection() as conn:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            return int(row["cnt"]) if row else 0

    def log_event(
        self, run_id: str, level: str, component: str, message: str
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO event_logs (run_id, level, component, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, level, component, message, _utc_now_iso()),
            )
            conn.commit()

    def get_event_logs(self, run_id: str, limit: int = 200) -> list[EventLogRecord]:
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM event_logs WHERE run_id = ? ORDER BY id ASC LIMIT ?",
                (run_id, limit),
            )
            return [
                EventLogRecord(
                    id=r["id"],
                    run_id=r["run_id"],
                    level=r["level"],
                    component=r["component"],
                    message=r["message"],
                    created_at=_parse_iso(r["created_at"]) or datetime.now(timezone.utc),
                )
                for r in cursor.fetchall()
            ]

    def save_report_reference(self, ref: ReportReference) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO report_references (run_id, format, file_path, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id, format) DO UPDATE SET
                    file_path = excluded.file_path,
                    created_at = excluded.created_at
                """,
                (ref.run_id, ref.format, ref.file_path, ref.created_at.isoformat()),
            )
            conn.commit()

    def get_report_references(self, run_id: str) -> list[ReportReference]:
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM report_references WHERE run_id = ? ORDER BY id ASC",
                (run_id,),
            )
            return [
                ReportReference(
                    id=r["id"],
                    run_id=r["run_id"],
                    format=r["format"],
                    file_path=r["file_path"],
                    created_at=_parse_iso(r["created_at"]) or datetime.now(timezone.utc),
                )
                for r in cursor.fetchall()
            ]
