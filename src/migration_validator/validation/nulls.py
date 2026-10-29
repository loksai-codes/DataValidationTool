"""
NULL count parity validation check.
Compares NULL counts across all mapped columns, distinguishing NULL from empty string.
"""

import time
import uuid
from typing import Dict, List, Optional

from migration_validator.connectors.base import BaseConnector
from migration_validator.validation.models import CheckResult, CheckType, ValidationStatus


def validate_null_counts(
    source_connector: BaseConnector,
    dest_connector: BaseConnector,
    source_table: str,
    dest_table: str,
    column_mapping: Dict[str, str],
    source_db: Optional[str] = None,
    dest_schema: Optional[str] = "public"
) -> List[CheckResult]:
    """Compare NULL counts for each mapped column pair."""
    results: List[CheckResult] = []
    src_cols = list(column_mapping.keys())
    dst_cols = [column_mapping[c] for c in src_cols]

    start = time.perf_counter()
    try:
        src_nulls = source_connector.get_null_counts(source_table, src_cols, schema_or_db=source_db)
        dst_nulls = dest_connector.get_null_counts(dest_table, dst_cols, schema_or_db=dest_schema)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        for src_c, dst_c in column_mapping.items():
            s_val = src_nulls.get(src_c, 0)
            d_val = dst_nulls.get(dst_c, 0)
            diff = d_val - s_val

            check_id = f"CHK-NULL-{src_c[:6].upper()}-{uuid.uuid4().hex[:6]}"

            if diff == 0:
                status = ValidationStatus.PASSED
                err_msg = None
            else:
                status = ValidationStatus.FAILED
                err_msg = f"NULL count difference on '{src_c}': Source has {s_val}, Destination has {d_val} (diff: {diff:+d})"

            results.append(
                CheckResult(
                    check_id=check_id,
                    check_type=CheckType.NULL_COUNT,
                    check_name=f"NULL Value Parity: {src_c} -> {dst_c}",
                    status=status,
                    source_value=f"{s_val:,} NULLs",
                    dest_value=f"{d_val:,} NULLs",
                    difference=f"{diff:+d}",
                    tolerance="0 count difference",
                    duration_ms=round(elapsed_ms / len(column_mapping), 2),
                    error_details=err_msg,
                    metadata={"column": src_c, "source_nulls": s_val, "dest_nulls": d_val}
                )
            )

    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        results.append(
            CheckResult(
                check_id=f"CHK-NULL-ERR-{uuid.uuid4().hex[:6]}",
                check_type=CheckType.NULL_COUNT,
                check_name="NULL Value Parity Evaluation",
                status=ValidationStatus.INCONCLUSIVE,
                source_value="Error",
                dest_value="Error",
                difference="N/A",
                tolerance="0 count difference",
                duration_ms=round(elapsed_ms, 2),
                error_details=f"Execution failed: {str(e)}"
            )
        )

    return results
