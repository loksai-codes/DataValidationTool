"""
Domain models, enums, and dataclasses for the validation engine.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ValidationStatus(str, Enum):
    """Execution status for individual checks and overall runs."""
    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    INCONCLUSIVE = "INCONCLUSIVE"
    CANCELLED = "CANCELLED"
    RUNNING = "RUNNING"

    @classmethod
    def from_str(cls, val: Any) -> "ValidationStatus":
        """Safely convert any string to a ValidationStatus enum."""
        if not val:
            return cls.INCONCLUSIVE
        try:
            return cls(str(val).upper().strip())
        except (ValueError, KeyError):
            return cls.INCONCLUSIVE


class CheckType(str, Enum):
    """Categorization of validation checks."""
    SCHEMA = "SCHEMA"
    RECORD_COUNT = "RECORD_COUNT"
    KEY_INTEGRITY = "KEY_INTEGRITY"
    NULL_COUNT = "NULL_COUNT"
    AGGREGATE = "AGGREGATE"
    DISTINCT_COUNT = "DISTINCT_COUNT"
    RECORD_COMPARISON = "RECORD_COMPARISON"
    DATA_QUALITY = "DATA_QUALITY"


class CheckResult(BaseModel):
    """Result of an individual validation check."""
    check_id: str
    check_type: CheckType
    check_name: str
    status: ValidationStatus
    source_value: Optional[Any] = None
    dest_value: Optional[Any] = None
    difference: Optional[Any] = None
    tolerance: Optional[Any] = None
    duration_ms: float = 0.0
    error_details: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DiscrepancyType(str, Enum):
    """Types of record-level and semantic discrepancies."""
    MISSING_IN_DEST = "MISSING_IN_DEST"
    EXTRA_IN_DEST = "EXTRA_IN_DEST"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    NULL_MISMATCH = "NULL_MISMATCH"


class Discrepancy(BaseModel):
    """A single record or column mismatch."""
    type: DiscrepancyType
    key: str
    column: Optional[str] = None
    source_val: Optional[str] = None
    dest_val: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class ValidationRuleConfig(BaseModel):
    """Configuration defining which validation rules to execute."""
    run_schema_validation: bool = True
    run_record_count: bool = True
    run_key_validation: bool = True
    run_null_validation: bool = True
    run_numeric_aggregates: bool = True
    run_distinct_counts: bool = True
    run_record_comparison: bool = True
    run_data_quality: bool = False

    # Key and Column configuration
    key_columns: List[str] = Field(default_factory=lambda: ["customer_id"])
    column_mapping: Dict[str, str] = Field(
        default_factory=lambda: {
            "customer_id": "customer_id",
            "customer_name": "customer_name",
            "email": "email",
            "city": "city",
            "account_balance": "account_balance",
            "created_at": "created_at"
        }
    )

    # Tolerances
    numeric_tolerance_abs: float = 0.01
    numeric_tolerance_rel: float = 0.0001

    # Sampling configuration
    use_sampling: bool = False
    sample_size: int = 10000

    # Batching for record comparison
    batch_size: int = 5000
    comparison_strategy: str = "auto"
    max_discrepancy_samples: int = 1000


class ValidationRunSummary(BaseModel):
    """Comprehensive summary of a validation execution."""
    run_id: str
    overall_status: ValidationStatus
    started_at: str
    completed_at: Optional[str] = None
    duration_seconds: float = 0.0
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    warning_checks: int = 0
    inconclusive_checks: int = 0
    total_records_evaluated: int = 0
    total_discrepancies: int = 0
    discrepancy_counts: Dict[str, int] = Field(default_factory=dict)
    checks: List[CheckResult] = Field(default_factory=list)
