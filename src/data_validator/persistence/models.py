"""Data models and enums for validation persistence and state tracking."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, Enum):
    DRAFT = "Draft"
    QUEUED = "Queued"
    RUNNING = "Running"
    PAUSED = "Paused"
    INTERRUPTED = "Interrupted"
    FAILED = "Failed"
    COMPLETED = "Completed"
    COMPLETED_WITH_DISCREPANCIES = "Completed with discrepancies"
    CANCEL_REQUESTED = "Cancel requested"
    CANCELLED = "Cancelled"


class CheckStatus(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    PASSED = "Passed"
    FAILED = "Failed"
    SKIPPED = "Skipped"


class PartitionStatus(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    COMPLETED = "Completed"
    FAILED = "Failed"
    SKIPPED = "Skipped"


class DiscrepancyType(str, Enum):
    MISSING_IN_TARGET = "missing_in_target"
    EXTRA_IN_TARGET = "extra_in_target"
    CHANGED_RECORD = "changed_record"
    DUPLICATE_KEY = "duplicate_key"
    NULL_KEY = "null_key"
    SCHEMA_MISMATCH = "schema_mismatch"
    COUNT_MISMATCH = "count_mismatch"


@dataclass
class RunRecord:
    run_id: str
    job_name: str
    status: RunStatus
    config_json: str
    source_metadata_json: str | None = None
    target_metadata_json: str | None = None
    total_partitions: int = 0
    completed_partitions: int = 0
    failed_partitions: int = 0
    rows_source: int = 0
    rows_target: int = 0
    rows_compared: int = 0
    missing_count: int = 0
    extra_count: int = 0
    changed_count: int = 0
    duplicate_key_count: int = 0
    null_key_count: int = 0
    error_message: str | None = None
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class CheckRecord:
    check_id: str
    run_id: str
    check_name: str
    status: CheckStatus
    summary: str | None = None
    details_json: str | None = None
    executed_at: datetime = field(default_factory=_utcnow)


@dataclass
class PartitionRecord:
    partition_id: str
    run_id: str
    partition_index: int
    lower_bound: str | None = None
    upper_bound: str | None = None
    cursor_state: str | None = None
    status: PartitionStatus = PartitionStatus.PENDING
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    attempts: int = 0
    rows_source: int = 0
    rows_target: int = 0
    rows_compared: int = 0
    missing_count: int = 0
    extra_count: int = 0
    changed_count: int = 0
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class DiscrepancyRecord:
    run_id: str
    partition_id: str
    discrepancy_type: DiscrepancyType
    key_values: str
    column_name: str | None = None
    source_value: str | None = None
    target_value: str | None = None
    details_json: str | None = None
    id: int | None = None
    detected_at: datetime = field(default_factory=_utcnow)


@dataclass
class EventLogRecord:
    run_id: str
    level: str
    component: str
    message: str
    id: int | None = None
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class ReportReference:
    run_id: str
    format: str
    file_path: str
    id: int | None = None
    created_at: datetime = field(default_factory=_utcnow)
