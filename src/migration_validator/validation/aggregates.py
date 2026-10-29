"""
Numeric aggregate and distinct count validation check.
Compares SUM, AVG, MIN, MAX, and COUNT DISTINCT using configurable tolerances.
"""

import time
import uuid
from typing import List, Optional

from migration_validator.connectors.base import BaseConnector
from migration_validator.validation.models import CheckResult, CheckType, ValidationStatus


def is_within_tolerance(
    src_val: float,
    dst_val: float,
    tol_abs: float = 0.01,
    tol_rel: float = 0.0001
) -> bool:
    """Check if difference is within absolute or relative threshold."""
    diff = abs(dst_val - src_val)
    if diff <= tol_abs:
        return True
    if abs(src_val) > 1e-9:
        rel_diff = diff / abs(src_val)
        if rel_diff <= tol_rel:
            return True
    return False


def validate_aggregates(
    source_connector: BaseConnector,
    dest_connector: BaseConnector,
    source_table: str,
    dest_table: str,
    numeric_columns: List[str],
    tolerance_abs: float = 0.01,
    tolerance_rel: float = 0.0001,
    source_db: Optional[str] = None,
    dest_schema: Optional[str] = "public"
) -> List[CheckResult]:
    """Execute pushdown aggregate comparisons for numeric columns."""
    results: List[CheckResult] = []
    if not numeric_columns:
        return results

    start = time.perf_counter()
    try:
        src_aggs = source_connector.get_aggregates(source_table, numeric_columns, schema_or_db=source_db)
        dst_aggs = dest_connector.get_aggregates(dest_table, numeric_columns, schema_or_db=dest_schema)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        for col in numeric_columns:
            s_data = src_aggs.get(col, {})
            d_data = dst_aggs.get(col, {})

            for metric in ["sum", "avg", "min", "max"]:
                s_val = float(s_data.get(metric, 0.0))
                d_val = float(d_data.get(metric, 0.0))
                diff = d_val - s_val
                abs_diff = abs(diff)

                within_tol = is_within_tolerance(s_val, d_val, tolerance_abs, tolerance_rel)
                check_id = f"CHK-AGG-{col[:4].upper()}-{metric.upper()}-{uuid.uuid4().hex[:6]}"

                if within_tol:
                    status = ValidationStatus.PASSED
                    err_msg = None
                else:
                    status = ValidationStatus.FAILED
                    err_msg = (
                        f"Aggregate divergence on {col}.{metric}: Source={s_val:,.4f}, "
                        f"Dest={d_val:,.4f} (diff={diff:+,.4f}, exceeds abs tol={tolerance_abs} & rel tol={tolerance_rel})"
                    )

                results.append(
                    CheckResult(
                        check_id=check_id,
                        check_type=CheckType.AGGREGATE,
                        check_name=f"Numeric Aggregate: {metric.upper()}({col})",
                        status=status,
                        source_value=f"{s_val:,.4f}",
                        dest_value=f"{d_val:,.4f}",
                        difference=f"{diff:+,.4f}",
                        tolerance=f"abs={tolerance_abs}, rel={tolerance_rel}",
                        duration_ms=round(elapsed_ms / (len(numeric_columns) * 4), 2),
                        error_details=err_msg,
                        metadata={
                            "column": col,
                            "metric": metric,
                            "source_val": s_val,
                            "dest_val": d_val,
                            "diff": diff,
                            "abs_diff": abs_diff
                        }
                    )
                )

    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        results.append(
            CheckResult(
                check_id=f"CHK-AGG-ERR-{uuid.uuid4().hex[:6]}",
                check_type=CheckType.AGGREGATE,
                check_name="Numeric Aggregate Comparison",
                status=ValidationStatus.INCONCLUSIVE,
                source_value="Error",
                dest_value="Error",
                difference="N/A",
                tolerance=f"abs={tolerance_abs}, rel={tolerance_rel}",
                duration_ms=round(elapsed_ms, 2),
                error_details=f"Execution failed: {str(e)}"
            )
        )

    return results


def validate_distinct_counts(
    source_connector: BaseConnector,
    dest_connector: BaseConnector,
    source_table: str,
    dest_table: str,
    columns_to_check: List[str],
    source_db: Optional[str] = None,
    dest_schema: Optional[str] = "public"
) -> List[CheckResult]:
    """Compare COUNT(DISTINCT col) for selected columns (e.g. emails)."""
    results: List[CheckResult] = []
    if not columns_to_check:
        return results

    start = time.perf_counter()
    try:
        src_dist = source_connector.get_distinct_counts(source_table, columns_to_check, schema_or_db=source_db)
        dst_dist = dest_connector.get_distinct_counts(dest_table, columns_to_check, schema_or_db=dest_schema)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        for col in columns_to_check:
            s_val = src_dist.get(col, 0)
            d_val = dst_dist.get(col, 0)
            diff = d_val - s_val

            check_id = f"CHK-DIST-{col[:6].upper()}-{uuid.uuid4().hex[:6]}"

            if diff == 0:
                status = ValidationStatus.PASSED
                err_msg = None
            else:
                status = ValidationStatus.FAILED
                err_msg = f"Distinct count mismatch on '{col}': Source has {s_val:,}, Dest has {d_val:,} (diff: {diff:+d})"

            results.append(
                CheckResult(
                    check_id=check_id,
                    check_type=CheckType.DISTINCT_COUNT,
                    check_name=f"Distinct Value Count: COUNT(DISTINCT {col})",
                    status=status,
                    source_value=f"{s_val:,} unique values",
                    dest_value=f"{d_val:,} unique values",
                    difference=f"{diff:+d}",
                    tolerance="0 count difference",
                    duration_ms=round(elapsed_ms / len(columns_to_check), 2),
                    error_details=err_msg,
                    metadata={"column": col, "source_distinct": s_val, "dest_distinct": d_val}
                )
            )

    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        results.append(
            CheckResult(
                check_id=f"CHK-DIST-ERR-{uuid.uuid4().hex[:6]}",
                check_type=CheckType.DISTINCT_COUNT,
                check_name="Distinct Value Counts Evaluation",
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
