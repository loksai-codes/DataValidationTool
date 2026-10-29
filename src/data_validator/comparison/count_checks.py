"""Row count comparison check."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CountCheckResult:
    passed: bool
    source_count: int
    target_count: int
    delta: int
    delta_percentage: float
    summary: str


def run_count_check(source_count: int, target_count: int) -> CountCheckResult:
    """Compare filtered row counts between source and target tables."""
    delta = target_count - source_count
    max_count = max(source_count, target_count)
    pct = (abs(delta) / max_count * 100.0) if max_count > 0 else 0.0
    passed = (delta == 0)

    if passed:
        summary = f"Row counts match perfectly: {source_count:,} rows."
    else:
        summary = (
            f"Row count mismatch: Source has {source_count:,} rows, "
            f"Target has {target_count:,} rows (delta: {delta:+,}, {pct:.2f}%)."
        )

    return CountCheckResult(
        passed=passed,
        source_count=source_count,
        target_count=target_count,
        delta=delta,
        delta_percentage=pct,
        summary=summary,
    )
