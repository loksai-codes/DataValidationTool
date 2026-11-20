"""
Row-level comparison of two already-fetched DataFrames (same column ids on both sides).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List

import pandas as pd

from datavalidator.datatypes import Logical
from datavalidator.models import Discrepancy
from datavalidator.normalize import NormOptions, SideContext, display, normalize_series


@dataclass
class ColumnSpec:
    cid: str                 # column id in fetched frames (c0, c1 ...)
    source_name: str
    target_name: str
    cmp: Logical
    src_ctx: SideContext
    tgt_ctx: SideContext
    opts: NormOptions
    abs_tol: float = 0.0
    rel_tol: float = 0.0
    is_key: bool = False


@dataclass
class CompareStats:
    source_rows: int = 0
    target_rows: int = 0
    matched_rows: int = 0
    identical_rows: int = 0
    changed_rows: int = 0
    missing_in_target: int = 0
    extra_in_target: int = 0
    duplicate_keys_source: int = 0
    duplicate_keys_target: int = 0
    column_mismatches: Dict[str, int] = field(default_factory=dict)

    def add(self, other: "CompareStats") -> None:
        for f in ("source_rows", "target_rows", "matched_rows", "identical_rows", "changed_rows",
                  "missing_in_target", "extra_in_target", "duplicate_keys_source", "duplicate_keys_target"):
            setattr(self, f, getattr(self, f) + getattr(other, f))
        for k, v in other.column_mismatches.items():
            self.column_mismatches[k] = self.column_mismatches.get(k, 0) + v

    @property
    def total_differences(self) -> int:
        return (self.changed_rows + self.missing_in_target + self.extra_in_target
                + self.duplicate_keys_source + self.duplicate_keys_target)


def _key_strings(df: pd.DataFrame, keys: List[ColumnSpec], side: str) -> pd.Series:
    parts = []
    for k in keys:
        ctx = k.src_ctx if side == "src" else k.tgt_ctx
        norm = normalize_series(df[k.cid], k.cmp, ctx, k.opts).map(display)
        parts.append(norm.fillna("<NULL>").astype(str))
    if len(parts) == 1:
        return parts[0]
    out = parts[0].copy()
    for p in parts[1:]:
        out = out + " | " + p
    return out


def _within(a, b, abs_tol: float, rel_tol: float) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    if a == b:
        return True
    try:
        diff = abs(a - b)
        if isinstance(diff, Decimal):
            return diff <= Decimal(str(abs_tol)) or diff <= Decimal(str(rel_tol)) * max(abs(a), abs(b))
        return diff <= abs_tol or diff <= rel_tol * max(abs(a), abs(b))
    except TypeError:
        return False


def compare_frames(
    src: pd.DataFrame,
    tgt: pd.DataFrame,
    columns: List[ColumnSpec],
    max_samples: int,
    samples: List[Discrepancy],
) -> CompareStats:
    stats = CompareStats(source_rows=len(src), target_rows=len(tgt))
    keys = [c for c in columns if c.is_key]
    values = [c for c in columns if not c.is_key]

    def sample(d: Discrepancy) -> None:
        if len(samples) < max_samples:
            samples.append(d)

    s = src.copy()
    t = tgt.copy()
    s["__k"] = _key_strings(s, keys, "src") if len(s) else pd.Series([], dtype=object)
    t["__k"] = _key_strings(t, keys, "tgt") if len(t) else pd.Series([], dtype=object)

    for df, label, attr in ((s, "SOURCE", "duplicate_keys_source"), (t, "TARGET", "duplicate_keys_target")):
        dup = df["__k"].duplicated(keep="first")
        n = int(dup.sum())
        if n:
            setattr(stats, attr, n)
            for k in df.loc[dup, "__k"].head(max_samples).tolist():
                sample(Discrepancy(kind=f"DUPLICATE_KEY_{label}", key=k))
    s = s.drop_duplicates("__k", keep="first").set_index("__k")
    t = t.drop_duplicates("__k", keep="first").set_index("__k")

    missing = s.index.difference(t.index)
    extra = t.index.difference(s.index)
    both = s.index.intersection(t.index)
    stats.missing_in_target = len(missing)
    stats.extra_in_target = len(extra)
    stats.matched_rows = len(both)
    for k in missing[:max_samples]:
        sample(Discrepancy(kind="MISSING_IN_TARGET", key=str(k)))
    for k in extra[:max_samples]:
        sample(Discrepancy(kind="EXTRA_IN_TARGET", key=str(k)))

    if len(both) == 0:
        return stats

    sb = s.loc[both]
    tb = t.loc[both]
    changed = pd.Series(False, index=both)
    for c in values:
        sv = normalize_series(sb[c.cid], c.cmp, c.src_ctx, c.opts)
        tv = normalize_series(tb[c.cid], c.cmp, c.tgt_ctx, c.opts)
        s_null = sv.isna()
        t_null = tv.isna()
        null_mismatch = s_null != t_null
        both_vals = ~s_null & ~t_null
        if c.cmp in (Logical.INTEGER, Logical.DECIMAL, Logical.FLOAT) or c.abs_tol or c.rel_tol:
            eq = pd.Series(True, index=both)
            idx = both_vals[both_vals].index
            if len(idx):
                eq.loc[idx] = [_within(a, b, c.abs_tol, c.rel_tol) for a, b in zip(sv.loc[idx], tv.loc[idx], strict=True)]
            value_mismatch = both_vals & ~eq
        else:
            value_mismatch = both_vals & (sv != tv)
        col_bad = null_mismatch | value_mismatch
        n_bad = int(col_bad.sum())
        if n_bad:
            stats.column_mismatches[c.source_name] = stats.column_mismatches.get(c.source_name, 0) + n_bad
            changed |= col_bad
            if len(samples) < max_samples:
                for k in col_bad[col_bad].index[: max_samples - len(samples)]:
                    sample(Discrepancy(
                        kind="NULL_MISMATCH" if null_mismatch.loc[k] else "VALUE_MISMATCH",
                        key=str(k), column=c.source_name,
                        source_value=display(sv.loc[k]), target_value=display(tv.loc[k]),
                    ))
    stats.changed_rows = int(changed.sum())
    stats.identical_rows = len(both) - stats.changed_rows
    return stats
