"""Key uniqueness and nullability verification checks.

Detects NULL keys and duplicate keys before normal reconciliation;
never silently collapses duplicates.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from data_validator.comparison.normalization import normalize_key_tuple
from data_validator.persistence.models import DiscrepancyRecord, DiscrepancyType


@dataclass
class KeyCheckResult:
    passed: bool
    total_records: int
    null_key_count: int
    duplicate_key_count: int
    summary: str
    discrepancies: list[DiscrepancyRecord] = field(default_factory=list)


def run_key_checks(
    records: list[dict[str, Any]],
    unique_keys: list[str],
    run_id: str,
    partition_id: str,
    side: str = "source",
) -> KeyCheckResult:
    """Analyze records for NULL keys or duplicate keys."""
    key_counts: dict[tuple[Any, ...], int] = defaultdict(int)
    null_keys: list[dict[str, Any]] = []
    discrepancies: list[DiscrepancyRecord] = []

    for rec in records:
        key_tuple = normalize_key_tuple(rec, unique_keys)

        # Check if any component in composite key is None
        if any(v is None for v in key_tuple):
            null_keys.append(rec)
            key_map = {col: rec.get(col) for col in unique_keys}
            discrepancies.append(
                DiscrepancyRecord(
                    run_id=run_id,
                    partition_id=partition_id,
                    discrepancy_type=DiscrepancyType.NULL_KEY,
                    key_values=json.dumps(key_map, default=str),
                    column_name=",".join(unique_keys),
                    source_value=json.dumps(key_map, default=str) if side == "source" else None,
                    target_value=json.dumps(key_map, default=str) if side == "target" else None,
                    details_json=json.dumps(
                        {"side": side, "error": "NULL unique key value detected"},
                        default=str,
                    ),
                )
            )
        else:
            key_counts[key_tuple] += 1

    duplicate_keys = {k: count for k, count in key_counts.items() if count > 1}
    for key_tuple, count in duplicate_keys.items():
        key_map = {col: val for col, val in zip(unique_keys, key_tuple)}
        discrepancies.append(
            DiscrepancyRecord(
                run_id=run_id,
                partition_id=partition_id,
                discrepancy_type=DiscrepancyType.DUPLICATE_KEY,
                key_values=json.dumps(key_map, default=str),
                column_name=",".join(unique_keys),
                source_value=f"Occurs {count} times in {side}" if side == "source" else None,
                target_value=f"Occurs {count} times in {side}" if side == "target" else None,
                details_json=json.dumps(
                    {"side": side, "occurrences": count, "key": key_map},
                    default=str,
                ),
            )
        )

    null_cnt = len(null_keys)
    dup_cnt = len(duplicate_keys)
    passed = (null_cnt == 0 and dup_cnt == 0)

    if passed:
        summary = f"{side.capitalize()} key check passed ({len(records):,} records analyzed, 0 nulls, 0 duplicates)."
    else:
        summary = (
            f"{side.capitalize()} key check failed: {null_cnt:,} NULL keys and "
            f"{dup_cnt:,} duplicate keys detected in {len(records):,} records."
        )

    return KeyCheckResult(
        passed=passed,
        total_records=len(records),
        null_key_count=null_cnt,
        duplicate_key_count=dup_cnt,
        summary=summary,
        discrepancies=discrepancies,
    )
