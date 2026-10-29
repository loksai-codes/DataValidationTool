"""Column nullability and NULL count comparison check."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ColumnNullStat:
    column_name: str
    source_nulls: int
    target_nulls: int
    source_total: int
    target_total: int
    source_pct: float
    target_pct: float
    delta_nulls: int


@dataclass
class NullCheckResult:
    passed: bool
    summary: str
    column_stats: list[ColumnNullStat] = field(default_factory=list)


def run_null_check(
    source_records: list[dict[str, Any]],
    target_records: list[dict[str, Any]],
    compared_columns: list[str],
) -> NullCheckResult:
    """Analyze null distribution per column between source and target."""
    stats: list[ColumnNullStat] = []
    s_total = len(source_records)
    t_total = len(target_records)

    has_mismatch = False
    for col in compared_columns:
        s_nulls = sum(1 for r in source_records if r.get(col) is None)
        t_nulls = sum(1 for r in target_records if r.get(col) is None)
        s_pct = (s_nulls / s_total * 100.0) if s_total > 0 else 0.0
        t_pct = (t_nulls / t_total * 100.0) if t_total > 0 else 0.0
        delta = t_nulls - s_nulls

        if s_nulls != t_nulls:
            has_mismatch = True

        stats.append(
            ColumnNullStat(
                column_name=col,
                source_nulls=s_nulls,
                target_nulls=t_nulls,
                source_total=s_total,
                target_total=t_total,
                source_pct=s_pct,
                target_pct=t_pct,
                delta_nulls=delta,
            )
        )

    passed = not has_mismatch
    summary = (
        "NULL distributions match across all compared columns."
        if passed
        else f"NULL count discrepancies detected in {sum(1 for s in stats if s.delta_nulls != 0)} columns."
    )

    return NullCheckResult(passed=passed, summary=summary, column_stats=stats)
