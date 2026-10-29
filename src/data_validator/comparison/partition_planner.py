"""Deterministic partition planner for non-overlapping, complete table validation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from data_validator.config.models import PartitionConfig, PartitionStrategy
from data_validator.persistence.models import PartitionRecord, PartitionStatus


@dataclass
class PlannedPartition:
    partition_index: int
    filter_expression: str
    lower_bound: Any
    upper_bound: Any
    is_null_partition: bool = False
    cursor_state: dict[str, Any] | None = None


def plan_partitions(
    run_id: str,
    partition_config: PartitionConfig,
    column_min: Any = None,
    column_max: Any = None,
    estimated_row_count: int = 0,
) -> list[PartitionRecord]:
    """Generate deterministic, non-overlapping partition plan.

    Supports:
    - KEY_RANGE (numeric ranges)
    - DATE_RANGE (daily/monthly chunks)
    - KEYSET_PAGINATION
    - BOUNDED_FULL_SCAN (single whole-table partition)
    """
    strategy = partition_config.strategy
    col = partition_config.column

    planned: list[PlannedPartition] = []

    if strategy == PartitionStrategy.BOUNDED_FULL_SCAN or (
        partition_config.allow_full_scan_fallback and (column_min is None or column_max is None)
    ):
        # Single bounded partition covering entire table
        planned.append(
            PlannedPartition(
                partition_index=0,
                filter_expression="1=1",
                lower_bound=None,
                upper_bound=None,
            )
        )

    elif strategy == PartitionStrategy.KEY_RANGE:
        if column_min is None or column_max is None:
            if partition_config.allow_full_scan_fallback:
                planned.append(
                    PlannedPartition(
                        partition_index=0,
                        filter_expression="1=1",
                        lower_bound=None,
                        upper_bound=None,
                    )
                )
            else:
                raise ValueError(
                    f"Key range partitioning requires min/max statistics for column '{col}'."
                )
        else:
            try:
                min_int = int(column_min)
                max_int = int(column_max)
            except (ValueError, TypeError):
                raise ValueError(
                    f"Column '{col}' min '{column_min}' and max '{column_max}' cannot be converted to integer for key_range."
                )

            step = partition_config.range_size or 25000
            current_low = min_int
            idx = 0

            if min_int > max_int:
                # Empty range
                planned.append(
                    PlannedPartition(
                        partition_index=0,
                        filter_expression="1=1",
                        lower_bound=min_int,
                        upper_bound=max_int,
                    )
                )
            else:
                while current_low <= max_int:
                    current_high = min(current_low + step - 1, max_int)
                    # Non-overlapping boundary
                    expr = f"{col} >= {current_low} AND {col} <= {current_high}"
                    planned.append(
                        PlannedPartition(
                            partition_index=idx,
                            filter_expression=expr,
                            lower_bound=current_low,
                            upper_bound=current_high,
                        )
                    )
                    idx += 1
                    current_low = current_high + 1

            if partition_config.include_null_partition:
                planned.append(
                    PlannedPartition(
                        partition_index=idx,
                        filter_expression=f"{col} IS NULL",
                        lower_bound=None,
                        upper_bound=None,
                        is_null_partition=True,
                    )
                )

    elif strategy == PartitionStrategy.DATE_RANGE:
        if column_min is None or column_max is None:
            raise ValueError(
                f"Date range partitioning requires start/end dates for column '{col}'."
            )

        def _to_date(val: Any) -> date:
            if isinstance(val, datetime):
                return val.date()
            if isinstance(val, date):
                return val
            return datetime.fromisoformat(str(val)).date()

        start_d = _to_date(column_min)
        end_d = _to_date(column_max)

        idx = 0
        curr = start_d
        step_days = 1 if partition_config.date_interval == "day" else 30

        while curr <= end_d:
            next_d = curr + timedelta(days=step_days)
            expr = f"{col} >= '{curr.isoformat()}' AND {col} < '{next_d.isoformat()}'"
            planned.append(
                PlannedPartition(
                    partition_index=idx,
                    filter_expression=expr,
                    lower_bound=curr.isoformat(),
                    upper_bound=next_d.isoformat(),
                )
            )
            idx += 1
            curr = next_d

        if partition_config.include_null_partition:
            planned.append(
                PlannedPartition(
                    partition_index=idx,
                    filter_expression=f"{col} IS NULL",
                    lower_bound=None,
                    upper_bound=None,
                    is_null_partition=True,
                )
            )

    elif strategy == PartitionStrategy.KEYSET_PAGINATION:
        # Keyset pagination with cursor checkpoints
        page_size = partition_config.range_size or 5000
        # If total row count is known, pre-slice estimated pages
        total = estimated_row_count or 1
        num_pages = max(1, (total + page_size - 1) // page_size)
        for i in range(num_pages):
            planned.append(
                PlannedPartition(
                    partition_index=i,
                    filter_expression=f"OFFSET {i * page_size} LIMIT {page_size}",
                    lower_bound=i * page_size,
                    upper_bound=(i + 1) * page_size,
                    cursor_state={"page": i, "limit": page_size, "offset": i * page_size},
                )
            )

    else:
        raise ValueError(f"Unsupported partition strategy: {strategy}")

    # Build persistence PartitionRecord objects with stable IDs
    partition_records: list[PartitionRecord] = []
    for p in planned:
        p_id = f"{run_id}_part_{p.partition_index:04d}"
        partition_records.append(
            PartitionRecord(
                partition_id=p_id,
                run_id=run_id,
                partition_index=p.partition_index,
                lower_bound=str(p.lower_bound) if p.lower_bound is not None else None,
                upper_bound=str(p.upper_bound) if p.upper_bound is not None else None,
                cursor_state=json.dumps(p.cursor_state) if p.cursor_state else p.filter_expression,
                status=PartitionStatus.PENDING,
            )
        )

    return partition_records
