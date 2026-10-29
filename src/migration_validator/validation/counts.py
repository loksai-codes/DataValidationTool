"""
Record count validation check.
Dynamically retrieves row counts from both source and destination.
"""

import time
import uuid
from typing import Optional

from migration_validator.connectors.base import BaseConnector
from migration_validator.validation.models import CheckResult, CheckType, ValidationStatus


def validate_record_counts(
    source_connector: BaseConnector,
    dest_connector: BaseConnector,
    source_table: str,
    dest_table: str,
    source_db: Optional[str] = None,
    dest_schema: Optional[str] = "public"
) -> CheckResult:
    """Compare total record counts dynamically."""
    start = time.perf_counter()
    check_id = f"CHK-CNT-{uuid.uuid4().hex[:8]}"

    src_count: Optional[int] = None
    dst_count: Optional[int] = None
    err_details: Optional[str] = None

    try:
        src_count = source_connector.get_record_count(source_table, schema_or_db=source_db)
    except Exception as e:
        err_details = f"Failed to retrieve source count: {str(e)}"

    try:
        dst_count = dest_connector.get_record_count(dest_table, schema_or_db=dest_schema)
    except Exception as e:
        msg = f"Failed to retrieve destination count: {str(e)}"
        err_details = f"{err_details}; {msg}" if err_details else msg

    elapsed_ms = (time.perf_counter() - start) * 1000.0

    if src_count is None or dst_count is None:
        status = ValidationStatus.INCONCLUSIVE
        diff_str = "N/A"
    else:
        diff = dst_count - src_count
        diff_str = f"{diff:+d} records"
        if diff == 0:
            status = ValidationStatus.PASSED
        else:
            status = ValidationStatus.FAILED
            err_details = f"Record count discrepancy: Source has {src_count:,}, Destination has {dst_count:,} (diff: {diff:+d})"

    return CheckResult(
        check_id=check_id,
        check_type=CheckType.RECORD_COUNT,
        check_name="Total Record Count Match",
        status=status,
        source_value=f"{src_count:,}" if src_count is not None else "Error",
        dest_value=f"{dst_count:,}" if dst_count is not None else "Error",
        difference=diff_str,
        tolerance="0 records (Exact Match)",
        duration_ms=round(elapsed_ms, 2),
        error_details=err_details,
        metadata={
            "source_raw": src_count,
            "dest_raw": dst_count,
            "diff_raw": (dst_count - src_count) if (src_count is not None and dst_count is not None) else None
        }
    )
