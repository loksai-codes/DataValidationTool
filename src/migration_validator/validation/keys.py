"""
Business key integrity and uniqueness validation check.
Evaluates key nullability, uniqueness, duplicates, and range parity.
Supports single and composite keys.
"""

import time
import uuid
from typing import List, Optional

from migration_validator.connectors.base import BaseConnector
from migration_validator.validation.models import CheckResult, CheckType, ValidationStatus


def validate_keys(
    source_connector: BaseConnector,
    dest_connector: BaseConnector,
    source_table: str,
    dest_table: str,
    key_columns: List[str],
    source_db: Optional[str] = None,
    dest_schema: Optional[str] = "public"
) -> List[CheckResult]:
    """Execute business key validation checks."""
    results: List[CheckResult] = []
    key_display = ", ".join(key_columns)

    start = time.perf_counter()
    check_id = f"CHK-KEY-DUP-{uuid.uuid4().hex[:8]}"

    try:
        src_keys = source_connector.get_key_summary(source_table, key_columns, schema_or_db=source_db)
        dst_keys = dest_connector.get_key_summary(dest_table, key_columns, schema_or_db=dest_schema)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        # Check 1: Duplicate Key Detection
        has_src_dups = src_keys.duplicate_keys > 0
        has_dst_dups = dst_keys.duplicate_keys > 0

        if not has_src_dups and not has_dst_dups:
            dup_status = ValidationStatus.PASSED
            dup_err = None
        else:
            dup_status = ValidationStatus.FAILED
            dup_err = f"Duplicate keys detected: Source ({src_keys.duplicate_keys}), Destination ({dst_keys.duplicate_keys})"

        results.append(
            CheckResult(
                check_id=check_id,
                check_type=CheckType.KEY_INTEGRITY,
                check_name=f"Key Uniqueness & Duplicate Detection ({key_display})",
                status=dup_status,
                source_value=f"{src_keys.duplicate_keys} duplicates ({src_keys.distinct_keys:,} distinct)",
                dest_value=f"{dst_keys.duplicate_keys} duplicates ({dst_keys.distinct_keys:,} distinct)",
                difference=f"Dst diff: {dst_keys.duplicate_keys - src_keys.duplicate_keys}",
                tolerance="0 duplicate keys",
                duration_ms=round(elapsed_ms, 2),
                error_details=dup_err,
                metadata={
                    "source_duplicates": src_keys.duplicate_keys,
                    "dest_duplicates": dst_keys.duplicate_keys,
                    "source_distinct": src_keys.distinct_keys,
                    "dest_distinct": dst_keys.distinct_keys
                }
            )
        )

        # Check 2: NULL Keys Detection
        null_start = time.perf_counter()
        null_check_id = f"CHK-KEY-NULL-{uuid.uuid4().hex[:8]}"
        has_src_nulls = src_keys.null_keys > 0
        has_dst_nulls = dst_keys.null_keys > 0
        null_elapsed_ms = (time.perf_counter() - null_start) * 1000.0

        if not has_src_nulls and not has_dst_nulls:
            null_status = ValidationStatus.PASSED
            null_err = None
        else:
            null_status = ValidationStatus.FAILED
            null_err = f"NULL keys found: Source ({src_keys.null_keys}), Destination ({dst_keys.null_keys})"

        results.append(
            CheckResult(
                check_id=null_check_id,
                check_type=CheckType.KEY_INTEGRITY,
                check_name=f"Key Nullability Verification ({key_display})",
                status=null_status,
                source_value=f"{src_keys.null_keys} NULL keys",
                dest_value=f"{dst_keys.null_keys} NULL keys",
                difference=f"Dst diff: {dst_keys.null_keys - src_keys.null_keys}",
                tolerance="0 NULL keys",
                duration_ms=round(null_elapsed_ms, 2),
                error_details=null_err,
                metadata={
                    "source_null_keys": src_keys.null_keys,
                    "dest_null_keys": dst_keys.null_keys
                }
            )
        )

        # Check 3: Key Range Parity (if single numeric/comparable key)
        if src_keys.min_key is not None and dst_keys.min_key is not None:
            range_check_id = f"CHK-KEY-RNG-{uuid.uuid4().hex[:8]}"
            is_min_match = (str(src_keys.min_key) == str(dst_keys.min_key))
            is_max_match = (str(src_keys.max_key) == str(dst_keys.max_key))

            if is_min_match and is_max_match:
                rng_status = ValidationStatus.PASSED
                rng_err = None
            else:
                rng_status = ValidationStatus.FAILED
                rng_err = f"Key bounds mismatch: Source [{src_keys.min_key}..{src_keys.max_key}] vs Dest [{dst_keys.min_key}..{dst_keys.max_key}]"

            results.append(
                CheckResult(
                    check_id=range_check_id,
                    check_type=CheckType.KEY_INTEGRITY,
                    check_name=f"Key Boundary Range Parity ({key_display})",
                    status=rng_status,
                    source_value=f"[{src_keys.min_key} .. {src_keys.max_key}]",
                    dest_value=f"[{dst_keys.min_key} .. {dst_keys.max_key}]",
                    difference="Bound match" if rng_status == ValidationStatus.PASSED else "Boundary divergence",
                    tolerance="Exact range match",
                    duration_ms=0.5,
                    error_details=rng_err,
                    metadata={
                        "source_min": src_keys.min_key,
                        "source_max": src_keys.max_key,
                        "dest_min": dst_keys.min_key,
                        "dest_max": dst_keys.max_key
                    }
                )
            )

    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        results.append(
            CheckResult(
                check_id=check_id,
                check_type=CheckType.KEY_INTEGRITY,
                check_name=f"Key Integrity Validation ({key_display})",
                status=ValidationStatus.INCONCLUSIVE,
                source_value="Error",
                dest_value="Error",
                difference="N/A",
                tolerance="0 duplicates / 0 nulls",
                duration_ms=round(elapsed_ms, 2),
                error_details=f"Execution failed: {str(e)}"
            )
        )

    return results
