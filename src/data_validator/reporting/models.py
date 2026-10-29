"""Report models and summary structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ReportSummary:
    run_id: str
    job_name: str
    status: str
    source_table: str
    target_table: str
    rows_source: int
    rows_target: int
    rows_compared: int
    missing_count: int
    extra_count: int
    changed_count: int
    duplicate_key_count: int
    null_key_count: int
    total_discrepancies: int
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None


@dataclass
class ValidationReportData:
    summary: ReportSummary
    config_snapshot: dict[str, Any]
    checks: list[dict[str, Any]] = field(default_factory=list)
    discrepancies: list[dict[str, Any]] = field(default_factory=list)
    event_logs: list[dict[str, Any]] = field(default_factory=list)
