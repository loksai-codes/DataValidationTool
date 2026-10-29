"""Core data comparison engine using unique keys and actual field values.

No fingerprints, no cross-platform SQL joins, no silent duplicate collapsing.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from data_validator.comparison.normalization import (
    NormalizationConfig,
    normalize_key_tuple,
    values_are_equal,
)
from data_validator.persistence.models import DiscrepancyRecord, DiscrepancyType


@dataclass
class PartitionComparisonResult:
    rows_source: int = 0
    rows_target: int = 0
    rows_compared: int = 0
    missing_count: int = 0
    extra_count: int = 0
    changed_count: int = 0
    match_count: int = 0
    duplicate_key_count: int = 0
    null_key_count: int = 0
    discrepancies: list[DiscrepancyRecord] = field(default_factory=list)


def compare_records_batch(
    source_records: list[dict[str, Any]],
    target_records: list[dict[str, Any]],
    unique_keys: list[str],
    ignored_columns: list[str] | None = None,
    normalization_config: NormalizationConfig | None = None,
    run_id: str = "run_default",
    partition_id: str = "part_0000",
) -> PartitionComparisonResult:
    """Compare a batch of records from source and target for a partition."""
    if ignored_columns is None:
        ignored_columns = []
    if normalization_config is None:
        normalization_config = NormalizationConfig()

    ignored_set = {c.lower() for c in ignored_columns}
    key_set = {k.lower() for k in unique_keys}

    result = PartitionComparisonResult(
        rows_source=len(source_records),
        rows_target=len(target_records),
    )

    source_by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    target_by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

    # 1. Index source records & detect null keys
    for r in source_records:
        k_tuple = normalize_key_tuple(r, unique_keys)
        if any(v is None for v in k_tuple):
            result.null_key_count += 1
            key_map = {col: r.get(col) for col in unique_keys}
            result.discrepancies.append(
                DiscrepancyRecord(
                    run_id=run_id,
                    partition_id=partition_id,
                    discrepancy_type=DiscrepancyType.NULL_KEY,
                    key_values=json.dumps(key_map, default=str),
                    column_name=",".join(unique_keys),
                    source_value=json.dumps(r, default=str),
                    target_value=None,
                    details_json=json.dumps({"side": "source", "error": "NULL key in source"}, default=str),
                )
            )
        else:
            source_by_key[k_tuple].append(r)

    # 2. Index target records & detect null keys
    for r in target_records:
        k_tuple = normalize_key_tuple(r, unique_keys)
        if any(v is None for v in k_tuple):
            result.null_key_count += 1
            key_map = {col: r.get(col) for col in unique_keys}
            result.discrepancies.append(
                DiscrepancyRecord(
                    run_id=run_id,
                    partition_id=partition_id,
                    discrepancy_type=DiscrepancyType.NULL_KEY,
                    key_values=json.dumps(key_map, default=str),
                    column_name=",".join(unique_keys),
                    source_value=None,
                    target_value=json.dumps(r, default=str),
                    details_json=json.dumps({"side": "target", "error": "NULL key in target"}, default=str),
                )
            )
        else:
            target_by_key[k_tuple].append(r)

    # 3. Detect duplicate keys
    for k_tuple, records in source_by_key.items():
        if len(records) > 1:
            result.duplicate_key_count += len(records) - 1
            key_map = {col: val for col, val in zip(unique_keys, k_tuple)}
            result.discrepancies.append(
                DiscrepancyRecord(
                    run_id=run_id,
                    partition_id=partition_id,
                    discrepancy_type=DiscrepancyType.DUPLICATE_KEY,
                    key_values=json.dumps(key_map, default=str),
                    column_name=",".join(unique_keys),
                    source_value=f"Occurs {len(records)} times in source",
                    target_value=None,
                    details_json=json.dumps(
                        {"side": "source", "occurrences": len(records), "key": key_map},
                        default=str,
                    ),
                )
            )

    for k_tuple, records in target_by_key.items():
        if len(records) > 1:
            result.duplicate_key_count += len(records) - 1
            key_map = {col: val for col, val in zip(unique_keys, k_tuple)}
            result.discrepancies.append(
                DiscrepancyRecord(
                    run_id=run_id,
                    partition_id=partition_id,
                    discrepancy_type=DiscrepancyType.DUPLICATE_KEY,
                    key_values=json.dumps(key_map, default=str),
                    column_name=",".join(unique_keys),
                    source_value=None,
                    target_value=f"Occurs {len(records)} times in target",
                    details_json=json.dumps(
                        {"side": "target", "occurrences": len(records), "key": key_map},
                        default=str,
                    ),
                )
            )

    # 4. Compare matched records
    all_keys = set(source_by_key.keys()).union(set(target_by_key.keys()))
    result.rows_compared = len(all_keys)

    for k_tuple in all_keys:
        in_source = k_tuple in source_by_key
        in_target = k_tuple in target_by_key
        key_map = {col: val for col, val in zip(unique_keys, k_tuple)}
        key_json = json.dumps(key_map, default=str)

        if in_source and not in_target:
            # Missing in target
            result.missing_count += len(source_by_key[k_tuple])
            for s_row in source_by_key[k_tuple]:
                result.discrepancies.append(
                    DiscrepancyRecord(
                        run_id=run_id,
                        partition_id=partition_id,
                        discrepancy_type=DiscrepancyType.MISSING_IN_TARGET,
                        key_values=key_json,
                        column_name=None,
                        source_value=json.dumps(s_row, default=str),
                        target_value=None,
                        details_json=json.dumps({"key": key_map, "source_row": s_row}, default=str),
                    )
                )

        elif in_target and not in_source:
            # Extra in target
            result.extra_count += len(target_by_key[k_tuple])
            for t_row in target_by_key[k_tuple]:
                result.discrepancies.append(
                    DiscrepancyRecord(
                        run_id=run_id,
                        partition_id=partition_id,
                        discrepancy_type=DiscrepancyType.EXTRA_IN_TARGET,
                        key_values=key_json,
                        column_name=None,
                        source_value=None,
                        target_value=json.dumps(t_row, default=str),
                        details_json=json.dumps({"key": key_map, "target_row": t_row}, default=str),
                    )
                )

        else:
            # Present on both sides: compare field by field
            s_row = source_by_key[k_tuple][0]
            t_row = target_by_key[k_tuple][0]

            # Case-insensitive column map
            s_cols = {c.lower(): (c, v) for c, v in s_row.items()}
            t_cols = {c.lower(): (c, v) for c, v in t_row.items()}

            shared_cols = set(s_cols.keys()).union(set(t_cols.keys()))
            field_mismatches: list[tuple[str, Any, Any]] = []

            for col_lower in shared_cols:
                if col_lower in key_set or col_lower in ignored_set:
                    continue

                s_orig_col, s_val = s_cols.get(col_lower, (col_lower, None))
                t_orig_col, t_val = t_cols.get(col_lower, (col_lower, None))
                col_display_name = s_orig_col if col_lower in s_cols else t_orig_col

                if not values_are_equal(s_val, t_val, normalization_config):
                    field_mismatches.append((col_display_name, s_val, t_val))

            if field_mismatches:
                result.changed_count += 1
                for col_name, s_val, t_val in field_mismatches:
                    result.discrepancies.append(
                        DiscrepancyRecord(
                            run_id=run_id,
                            partition_id=partition_id,
                            discrepancy_type=DiscrepancyType.CHANGED_RECORD,
                            key_values=key_json,
                            column_name=col_name,
                            source_value=str(s_val) if s_val is not None else "<NULL>",
                            target_value=str(t_val) if t_val is not None else "<NULL>",
                            details_json=json.dumps(
                                {
                                    "key": key_map,
                                    "column": col_name,
                                    "source": s_val,
                                    "target": t_val,
                                },
                                default=str,
                            ),
                        )
                    )
            else:
                result.match_count += 1

    return result
