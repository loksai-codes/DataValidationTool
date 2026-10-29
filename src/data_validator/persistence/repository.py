"""Abstract repository interface for durable validation run state and discrepancies."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from data_validator.persistence.models import (
    CheckRecord,
    DiscrepancyRecord,
    EventLogRecord,
    PartitionRecord,
    PartitionStatus,
    ReportReference,
    RunRecord,
    RunStatus,
)


class BaseRunRepository(ABC):
    """Abstract base repository for durable run persistence."""

    @abstractmethod
    def create_run(self, run: RunRecord) -> None:
        """Create a new run entry."""
        pass

    @abstractmethod
    def get_run(self, run_id: str) -> RunRecord | None:
        """Fetch a run by ID."""
        pass

    @abstractmethod
    def update_run_status(
        self,
        run_id: str,
        status: RunStatus,
        error_message: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        """Update run overall status."""
        pass

    @abstractmethod
    def update_run_heartbeat(self, run_id: str) -> None:
        """Update last heartbeat timestamp for run."""
        pass

    @abstractmethod
    def list_runs(
        self,
        limit: int = 50,
        status: RunStatus | None = None,
        job_name: str | None = None,
    ) -> list[RunRecord]:
        """List historical runs with optional filtering."""
        pass

    @abstractmethod
    def save_checks(self, checks: list[CheckRecord]) -> None:
        """Persist or update check records."""
        pass

    @abstractmethod
    def get_checks(self, run_id: str) -> list[CheckRecord]:
        """Fetch all check records for a run."""
        pass

    @abstractmethod
    def save_partitions(self, partitions: list[PartitionRecord]) -> None:
        """Save initial partition plan."""
        pass

    @abstractmethod
    def get_partitions(self, run_id: str) -> list[PartitionRecord]:
        """Fetch all partition records for a run."""
        pass

    @abstractmethod
    def get_unfinished_partitions(self, run_id: str) -> list[PartitionRecord]:
        """Fetch incomplete or failed partitions for resumption."""
        pass

    @abstractmethod
    def acquire_partition_lease(
        self, partition_id: str, worker_id: str, lease_duration_seconds: int = 60
    ) -> bool:
        """Try to acquire or renew a partition lease."""
        pass

    @abstractmethod
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
        """Transactionally commit partition completion and its detected discrepancies."""
        pass

    @abstractmethod
    def recover_stale_leases(self, timeout_seconds: int = 120) -> int:
        """Recover stale Running partitions/runs to Interrupted status."""
        pass

    @abstractmethod
    def get_discrepancies(
        self,
        run_id: str,
        discrepancy_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DiscrepancyRecord]:
        """Fetch paginated discrepancies for a run."""
        pass

    @abstractmethod
    def count_discrepancies(self, run_id: str, discrepancy_type: str | None = None) -> int:
        """Count discrepancies for a run."""
        pass

    @abstractmethod
    def log_event(
        self, run_id: str, level: str, component: str, message: str
    ) -> None:
        """Append a sanitized log entry for a run."""
        pass

    @abstractmethod
    def get_event_logs(self, run_id: str, limit: int = 200) -> list[EventLogRecord]:
        """Retrieve execution event logs for a run."""
        pass

    @abstractmethod
    def save_report_reference(self, ref: ReportReference) -> None:
        """Store reference to an exported report."""
        pass

    @abstractmethod
    def get_report_references(self, run_id: str) -> list[ReportReference]:
        """Fetch exported report references for a run."""
        pass
