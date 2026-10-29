"""Aggregate checks for numeric/metric comparison."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from data_validator.comparison.normalization import compare_decimals, normalize_number_or_decimal


@dataclass
class AggregateItemDiff:
    metric: str
    column_name: str
    source_val: Any
    target_val: Any
    passed: bool
    delta: Any


@dataclass
class AggregateCheckResult:
    passed: bool
    summary: str
    diffs: list[AggregateItemDiff] = field(default_factory=list)


def run_aggregate_check(
    source_stats: dict[str, dict[str, Any]],
    target_stats: dict[str, dict[str, Any]],
    tolerance: str = "0",
) -> AggregateCheckResult:
    """Compare aggregates (sum, min, max, avg, distinct_count) between source and target."""
    diffs: list[AggregateItemDiff] = []
    all_cols = set(source_stats.keys()).union(set(target_stats.keys()))

    for col in sorted(all_cols):
        s_col_stats = source_stats.get(col, {})
        t_col_stats = target_stats.get(col, {})
        metrics = set(s_col_stats.keys()).union(set(t_col_stats.keys()))

        for m in sorted(metrics):
            s_val = s_col_stats.get(m)
            t_val = t_col_stats.get(m)

            s_dec = normalize_number_or_decimal(s_val)
            t_dec = normalize_number_or_decimal(t_val)

            if s_dec is not None and t_dec is not None:
                item_passed = compare_decimals(s_dec, t_dec, tolerance=tolerance)
                delta = t_dec - s_dec
            else:
                item_passed = (s_val == t_val)
                delta = None

            diffs.append(
                AggregateItemDiff(
                    metric=m,
                    column_name=col,
                    source_val=s_val,
                    target_val=t_val,
                    passed=item_passed,
                    delta=delta,
                )
            )

    all_passed = all(d.passed for d in diffs) if diffs else True
    summary = (
        "All aggregate checks passed."
        if all_passed
        else f"Aggregate check failed on {len([d for d in diffs if not d.passed])} metrics."
    )

    return AggregateCheckResult(passed=all_passed, summary=summary, diffs=diffs)
