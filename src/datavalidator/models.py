"""Result models (serializable with pydantic)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    ERROR = "ERROR"      # the check could not run (connectivity, SQL error ...)
    SKIPPED = "SKIPPED"

    @property
    def rank(self) -> int:
        return {"SKIPPED": 0, "PASS": 1, "WARN": 2, "FAIL": 3, "ERROR": 4}[self.value]

    @staticmethod
    def worst(statuses: List["Status"]) -> "Status":
        real = [s for s in statuses if s != Status.SKIPPED]
        if not real:
            return Status.SKIPPED
        return max(real, key=lambda s: s.rank)


class CheckResult(BaseModel):
    check: str                       # schema | row_count | keys | nulls | aggregates | distinct | data | custom
    name: str
    status: Status
    message: str = ""
    source_value: Optional[Any] = None
    target_value: Optional[Any] = None
    duration_s: float = 0.0
    details: Dict[str, Any] = Field(default_factory=dict)


class Discrepancy(BaseModel):
    kind: str                        # MISSING_IN_TARGET | EXTRA_IN_TARGET | VALUE_MISMATCH | NULL_MISMATCH | DUPLICATE_KEY_SOURCE | DUPLICATE_KEY_TARGET
    key: str
    column: Optional[str] = None
    source_value: Optional[str] = None
    target_value: Optional[str] = None


class TableResult(BaseModel):
    name: str
    source: str
    target: str
    status: Status = Status.SKIPPED
    started_at: str = ""
    duration_s: float = 0.0
    checks: List[CheckResult] = Field(default_factory=list)
    discrepancies: List[Discrepancy] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class RunResult(BaseModel):
    run_id: str
    project: str
    started_at: str
    finished_at: str = ""
    duration_s: float = 0.0
    status: Status = Status.SKIPPED
    tool_version: str = ""
    tables: List[TableResult] = Field(default_factory=list)
    connections: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    @property
    def counts(self) -> Dict[str, int]:
        out = {s.value: 0 for s in Status}
        for t in self.tables:
            out[t.status.value] += 1
        return out
