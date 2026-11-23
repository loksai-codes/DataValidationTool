"""
Table validation engine.

Pipeline for one source/target table pair::

    discover schemas ─► map columns ─► schema check
          │
          ▼
    table profile (ONE scan per side): row count, NULL counts, key nulls/duplicates,
    numeric SUM/MIN/MAX, temporal MIN/MAX, optional COUNT(DISTINCT)
          │
          ▼
    row-level data comparison
      • full    : fetch all rows in hash-bucket batches and compare
      • profile : compare per-bucket checksums computed *inside each engine*, then
                  recursively subdivide only mismatching buckets and fetch just those rows
      • sample  : full comparison of a deterministic subset of hash buckets
          │
          ▼
    custom SQL checks

Hash buckets are ``SUBSTR(MD5(<key text>), 1, L)`` — the same expression evaluated on both
engines, so the same keys always land in the same bucket regardless of each engine's sort
order or collation. This avoids the false "missing / extra" rows that cross-engine keyset
pagination produces, and lets the profile mode move only checksums across clouds.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from datavalidator.compare import ColumnSpec, CompareStats, compare_frames
from datavalidator.config import DatasetRef, TableConfig
from datavalidator.connectors import ColumnInfo, Connector, TableInfo
from datavalidator.datatypes import Logical, check_compatibility
from datavalidator.log import get_logger
from datavalidator.models import CheckResult, Discrepancy, Status, TableResult
from datavalidator.normalize import (
    NormOptions,
    SideContext,
    comparison_logical,
    display,
    normalize_series,
)

logger = get_logger("engine")
HEX = "0123456789abcdef"


class RunCancelled(Exception):
    """Raised inside a table validation when the run's cancel event is set."""


# =========================================================================== helpers
@dataclass
class Pair:
    idx: int
    src: ColumnInfo
    tgt: ColumnInfo
    cmp: Logical
    is_key: bool
    temporal_ok: bool = False

    @property
    def cid(self) -> str:
        return f"c{self.idx}"


def _fixed_offset_seconds(tz) -> Optional[int]:
    """UTC offset of a zone if it has no DST in recent years, else None."""
    offsets = {
        int(dt.datetime(y, m, 15, 12, tzinfo=tz).utcoffset().total_seconds())
        for y in (2015, 2020, 2025) for m in (1, 4, 7, 10)
    }
    return offsets.pop() if len(offsets) == 1 else None


def _num(v: Any) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
        if isinstance(v, Decimal):
            return v
        if isinstance(v, float):
            return Decimal(repr(v))
        s = str(v).strip()
        if s == "" or s.lower() in ("nan", "none", "null"):
            return None
        return Decimal(s)
    except Exception:  # noqa: BLE001
        return None


def _close(a: Optional[Decimal], b: Optional[Decimal], abs_tol: float, rel_tol: float) -> bool:
    if a is None or b is None:
        return (a is None) == (b is None) or ((a or 0) == 0 and (b or 0) == 0)
    if a == b:
        return True
    diff = abs(a - b)
    return diff <= Decimal(str(abs_tol)) or diff <= Decimal(str(rel_tol)) * max(abs(a), abs(b))


class Side:
    """SQL building for one side (source or target) of a table pair."""

    def __init__(self, role: str, conn: Connector, ds: DatasetRef, cfg: TableConfig):
        self.role = role
        self.conn = conn
        self.ds = ds
        self.cfg = cfg
        self.d = conn.dialect
        self.frm = conn.from_clause(ds)
        self.key_cols: List[ColumnInfo] = []

    def col(self, c: ColumnInfo) -> str:
        return self.d.quote(c.name)

    def string_expr(self, c: ColumnInfo) -> str:
        """Column expression with string normalization rules applied in-database."""
        e = self.col(c)
        n = self.cfg.normalize
        if n.trim_strings:
            e = f"TRIM({e})"
        elif n.rtrim_strings:
            e = f"RTRIM({e})"
        if c.name in n.case_insensitive_columns or c.name.lower() in [x.lower() for x in n.case_insensitive_columns]:
            e = f"LOWER({e})"
        if n.empty_string_as_null:
            e = f"NULLIF({e}, '')"
        return e

    def key_text(self) -> str:
        if self.ds.bucket_key_expr:
            return f"({self.ds.bucket_key_expr})"
        parts = [self.d.cast_text(self.col(k)) for k in self.key_cols]
        if len(parts) == 1:
            return parts[0]
        joined: List[str] = []
        for i, p in enumerate(parts):
            if i:
                joined.append(self.d.lit("|"))
            joined.append(p)
        return self.d.concat(joined)

    def bucket(self, digits: int) -> str:
        return self.d.substr(self.d.md5_hex(self.key_text()), 1, digits)

    def where(self, extra: Sequence[str] = (), keys_not_null: bool = False) -> str:
        conds = []
        if self.ds.filter:
            conds.append(f"({self.ds.filter})")
        if keys_not_null:
            conds += [f"{self.col(k)} IS NOT NULL" for k in self.key_cols]
        conds += list(extra)
        return f" WHERE {' AND '.join(conds)}" if conds else ""

    def bucket_filter(self, labels: Sequence[str]) -> List[str]:
        by_len: Dict[int, List[str]] = {}
        for lab in labels:
            by_len.setdefault(len(lab), []).append(lab)
        ors = []
        for ln, labs in sorted(by_len.items()):
            ors.append(f"{self.bucket(ln)} IN ({', '.join(self.d.lit(x) for x in sorted(labs))})")
        if not ors:
            return []
        return [ors[0] if len(ors) == 1 else "(" + " OR ".join(ors) + ")"]

    def query(self, sql: str, null_sentinel: bool = False) -> pd.DataFrame:
        df = self.conn.query(sql, null_sentinel=null_sentinel)
        df.columns = [str(c).lower() for c in df.columns]
        return df


# =========================================================================== validator
class TableValidator:
    def __init__(self, cfg: TableConfig, source: Connector, target: Connector,
                 progress: Optional[Callable[[str, str], None]] = None,
                 cancel_event: Optional[threading.Event] = None):
        self.cfg = cfg
        self.src = Side("source", source, cfg.source, cfg)
        self.tgt = Side("target", target, cfg.target, cfg)
        self.pairs: List[Pair] = []
        self.samples: List[Discrepancy] = []
        self.stats: Dict[str, Any] = {"queries": 0, "rows_fetched_source": 0, "rows_fetched_target": 0}
        self._progress = progress or (lambda table, msg: None)
        self._cancel = cancel_event
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"dv-{cfg.name}")

    # ------------------------------------------------------------------ plumbing
    def _check_cancel(self) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise RunCancelled("cancelled by user")

    def _both(self, fs: Callable[[], Any], ft: Callable[[], Any]) -> Tuple[Any, Any]:
        self._check_cancel()
        a = self._pool.submit(fs)
        b = self._pool.submit(ft)
        self.stats["queries"] += 2
        return a.result(), b.result()

    def _say(self, msg: str, final: bool = False) -> None:
        if not final:
            self._check_cancel()
        logger.info("[%s] %s", self.cfg.name, msg)
        self._progress(self.cfg.name, msg)

    # ------------------------------------------------------------------ main
    def run(self) -> TableResult:
        t0 = time.perf_counter()
        res = TableResult(
            name=self.cfg.name, source=self.src.conn.describe(self.cfg.source),
            target=self.tgt.conn.describe(self.cfg.target),
            started_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), tags=self.cfg.tags,
        )
        checks = self.cfg.checks
        try:
            self._say("discovering schemas")
            src_info, tgt_info = self._both(lambda: self.src.conn.get_table_info(self.cfg.source),
                                            lambda: self.tgt.conn.get_table_info(self.cfg.target))
            schema_chk = self._map_columns(src_info, tgt_info)
            if "schema" in checks:
                res.checks.append(schema_chk)

            profile = None
            if any(c in checks for c in ("row_count", "keys", "nulls", "aggregates", "distinct")):
                self._say("profiling tables (1 scan per side)")
                profile = self._table_profile()
                res.checks.extend(self._profile_checks(profile))

            if "data" in checks:
                res.checks.append(self._data_check(profile))

            if "custom" in checks:
                for cc in self.cfg.custom_checks:
                    res.checks.append(self._custom_check(cc))
        except RunCancelled as exc:
            res.error = str(exc)
            res.checks.append(CheckResult(check="engine", name="Cancelled", status=Status.ERROR,
                                          message="run cancelled before this table finished"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] validation error", self.cfg.name)
            res.error = str(exc)
            res.checks.append(CheckResult(check="engine", name="Validation execution", status=Status.ERROR,
                                          message=str(exc)))
        finally:
            self._pool.shutdown(wait=False)

        res.discrepancies = self.samples
        res.stats = self.stats
        res.status = Status.worst([c.status for c in res.checks])
        res.duration_s = round(time.perf_counter() - t0, 2)
        self._say(f"done: {res.status.value} in {res.duration_s}s", final=True)
        return res

    # ------------------------------------------------------------------ schema
    def _map_columns(self, src: TableInfo, tgt: TableInfo) -> CheckResult:
        t0 = time.perf_counter()
        cfg = self.cfg
        excluded = {c.lower() for c in cfg.exclude_columns + cfg.normalize.ignore_columns}
        mapping: Dict[str, str] = {}
        for s_name, t_name in cfg.columns.items():
            mapping[s_name] = t_name
        if cfg.auto_map_columns:
            for c in src.columns:
                if c.name not in mapping and tgt.get(c.name) is not None:
                    mapping[c.name] = tgt.get(c.name).name

        keys = list(cfg.keys)
        key_source = "config"
        if not keys:
            keys = self.src.conn.primary_keys(cfg.source)
            key_source = "source primary key"
            if not keys:
                tgt_pk = self.tgt.conn.primary_keys(cfg.target)
                inv = {v.lower(): k for k, v in mapping.items()}
                keys = [inv.get(k.lower(), k) for k in tgt_pk]
                key_source = "target primary key"
        self.stats["keys"] = keys
        self.stats["key_source"] = key_source if keys else "none"

        missing_src, missing_tgt, problems, warnings, cols = [], [], [], [], []
        idx = 0
        mapped_tgt = set()
        for s_name, t_name in mapping.items():
            if s_name.lower() in excluded and s_name not in keys:
                continue
            s_col, t_col = src.get(s_name), tgt.get(t_name)
            if s_col is None:
                missing_src.append(s_name)
                continue
            if t_col is None:
                missing_tgt.append(t_name)
                continue
            mapped_tgt.add(t_col.name.lower())
            compat = check_compatibility(s_col.type, t_col.type)
            cmp = comparison_logical(s_col.logical, t_col.logical)
            pair = Pair(idx, s_col, t_col, cmp, is_key=s_col.name in keys or s_col.name.lower() in [k.lower() for k in keys])
            idx += 1
            self.pairs.append(pair)
            row = {"source": s_col.name, "source_type": s_col.type.native, "target": t_col.name,
                   "target_type": t_col.type.native, "compare_as": cmp.value, "compatibility": compat.level,
                   "note": compat.reason or ""}
            cols.append(row)
            if compat.level == "fail":
                problems.append(f"{s_col.name}: {compat.reason}")
            elif compat.level == "warn":
                warnings.append(f"{s_col.name}: {compat.reason}")

        unmapped_src = [c.name for c in src.columns
                        if c.name not in mapping and c.name.lower() not in excluded]
        extra_tgt = [c.name for c in tgt.columns
                     if c.name.lower() not in mapped_tgt and c.name.lower() not in excluded]

        key_pairs = [p for p in self.pairs if p.is_key]
        if keys and len(key_pairs) != len(keys):
            problems.append(f"key columns {keys} are not all mapped on both sides")
        self.src.key_cols = [p.src for p in key_pairs]
        self.tgt.key_cols = [p.tgt for p in key_pairs]

        # temporal checksum eligibility (constant UTC offsets only)
        for p in self.pairs:
            if p.cmp.is_temporal and not p.is_key:
                offs = []
                for side, col in ((self.src, p.src), (self.tgt, p.tgt)):
                    if col.logical == Logical.TIMESTAMP_TZ or p.cmp == Logical.DATE:
                        offs.append(0)
                    else:
                        offs.append(_fixed_offset_seconds(side.conn.tz))
                p.temporal_ok = all(o is not None for o in offs) and all(c.logical.is_temporal for c in (p.src, p.tgt))
                p._offsets = offs  # type: ignore[attr-defined]

        if unmapped_src:
            problems.append(f"source columns with no target column: {unmapped_src}")
        if missing_tgt:
            problems.append(f"mapped target columns not found: {missing_tgt}")
        if missing_src:
            problems.append(f"mapped source columns not found: {missing_src}")
        if extra_tgt:
            (problems if cfg.fail_on_extra_target_columns else warnings).append(
                f"target columns not in source: {extra_tgt}")
        if not keys:
            warnings.append("no key columns configured or detected; row-level comparison is disabled")

        if problems:
            status = Status.FAIL
        elif warnings:
            status = Status.FAIL if cfg.fail_on_schema_warnings else Status.WARN
        else:
            status = Status.PASS
        return CheckResult(
            check="schema", name="Schema & type compatibility", status=status,
            message="; ".join(problems + warnings) or f"{len(self.pairs)} columns mapped and compatible",
            source_value=f"{len(src.columns)} columns", target_value=f"{len(tgt.columns)} columns",
            duration_s=round(time.perf_counter() - t0, 3),
            details={"columns": cols, "keys": keys, "key_source": self.stats["key_source"],
                     "unmapped_source": unmapped_src, "extra_target": extra_tgt},
        )

    # ------------------------------------------------------------------ table profile
    def _profile_sql(self, side: Side) -> str:
        d = side.d
        sel = ["COUNT(*) AS dv_rows"]
        checks = self.cfg.checks
        keys = side.key_cols
        if keys and "keys" in checks:
            null_cond = " OR ".join(f"{side.col(k)} IS NULL" for k in keys)
            sel.append(f"SUM(CASE WHEN {null_cond} THEN 1 ELSE 0 END) AS dv_null_keys")
            if len(keys) == 1:
                sel.append(f"COUNT(DISTINCT {side.col(keys[0])}) AS dv_distinct_keys")
            else:
                nn = " AND ".join(f"{side.col(k)} IS NOT NULL" for k in keys)
                sel.append(f"COUNT(DISTINCT CASE WHEN {nn} THEN {side.key_text()} END) AS dv_distinct_keys")
        distinct = {c.lower() for c in self.cfg.distinct_columns}
        for p in self.pairs:
            c = p.src if side.role == "source" else p.tgt
            e = side.col(c)
            sel.append(f"COUNT({e}) AS n{p.idx}")
            if "aggregates" in checks:
                if p.cmp in (Logical.INTEGER, Logical.DECIMAL, Logical.FLOAT) and c.logical.is_numeric:
                    sel += [f"{d.numeric_sum(e, c.logical)} AS s{p.idx}", f"MIN({e}) AS mn{p.idx}",
                            f"MAX({e}) AS mx{p.idx}"]
                elif p.cmp == Logical.BOOLEAN:
                    inner = d.bool_to_int(e) if c.logical == Logical.BOOLEAN else e
                    sel.append(f"SUM({inner}) AS s{p.idx}")
                elif p.cmp.is_temporal and c.logical.is_temporal:
                    sel += [f"MIN({e}) AS mn{p.idx}", f"MAX({e}) AS mx{p.idx}"]
            if "distinct" in checks and p.src.name.lower() in distinct:
                sel.append(f"COUNT(DISTINCT {e}) AS d{p.idx}")
        return f"SELECT {', '.join(sel)} FROM {side.frm}{side.where()}"

    def _table_profile(self) -> Dict[str, Dict[str, Any]]:
        s_df, t_df = self._both(lambda: self.src.query(self._profile_sql(self.src)),
                                lambda: self.tgt.query(self._profile_sql(self.tgt)))
        return {"source": s_df.iloc[0].to_dict(), "target": t_df.iloc[0].to_dict()}

    def _tol(self, p: Pair) -> Tuple[float, float]:
        tol = self.cfg.tolerances
        per = tol.columns.get(p.src.name) or tol.columns.get(p.src.name.lower()) or {}
        abs_t = per.get("abs", tol.numeric_abs)
        rel_t = per.get("rel", tol.numeric_rel)
        if p.cmp == Logical.FLOAT:
            rel_t = max(rel_t, tol.float_rel)
        return abs_t, rel_t

    def _profile_checks(self, prof: Dict[str, Dict[str, Any]]) -> List[CheckResult]:
        s, t = prof["source"], prof["target"]
        out: List[CheckResult] = []
        checks = self.cfg.checks
        s_rows, t_rows = int(_num(s["dv_rows"]) or 0), int(_num(t["dv_rows"]) or 0)
        self.stats["source_rows"], self.stats["target_rows"] = s_rows, t_rows

        if "row_count" in checks:
            tol = self.cfg.tolerances
            diff = t_rows - s_rows
            allowed = max(tol.row_count_abs, int(s_rows * tol.row_count_pct / 100.0))
            status = Status.PASS if abs(diff) <= allowed else Status.FAIL
            msg = "row counts match" if diff == 0 else f"difference {diff:+,} rows" + (
                f" (within tolerance {allowed:,})" if status == Status.PASS else "")
            out.append(CheckResult(check="row_count", name="Row count", status=status, message=msg,
                                   source_value=s_rows, target_value=t_rows,
                                   details={"difference": diff, "allowed": allowed}))

        if "keys" in checks:
            if not self.src.key_cols:
                out.append(CheckResult(check="keys", name="Key integrity", status=Status.SKIPPED,
                                       message="no key columns"))
            else:
                info = {}
                bad = []
                for label, row, rows in (("source", s, s_rows), ("target", t, t_rows)):
                    nulls = int(_num(row.get("dv_null_keys")) or 0)
                    distinct = int(_num(row.get("dv_distinct_keys")) or 0)
                    dups = max(0, rows - nulls - distinct)
                    info[label] = {"null_keys": nulls, "duplicate_keys": dups, "distinct_keys": distinct}
                    if nulls:
                        bad.append(f"{label} has {nulls:,} NULL keys")
                    if dups:
                        bad.append(f"{label} has {dups:,} duplicate key rows")
                out.append(CheckResult(
                    check="keys", name=f"Key integrity ({', '.join(p.src.name for p in self.pairs if p.is_key)})",
                    status=Status.FAIL if bad else Status.PASS,
                    message="; ".join(bad) or "keys are unique and non-null on both sides",
                    source_value=info["source"], target_value=info["target"], details=info))

        if "nulls" in checks:
            rows = []
            for p in self.pairs:
                sn = s_rows - int(_num(s.get(f"n{p.idx}")) or 0)
                tn = t_rows - int(_num(t.get(f"n{p.idx}")) or 0)
                if sn != tn:
                    rows.append({"column": p.src.name, "source_nulls": sn, "target_nulls": tn, "difference": tn - sn})
            out.append(CheckResult(
                check="nulls", name="NULL counts per column", status=Status.FAIL if rows else Status.PASS,
                message=f"{len(rows)} column(s) with different NULL counts" if rows else
                f"NULL counts match for {len(self.pairs)} columns",
                details={"mismatches": rows}))

        if "aggregates" in checks:
            rows, compared = [], 0
            for p in self.pairs:
                abs_t, rel_t = self._tol(p)
                if f"s{p.idx}" in s and f"s{p.idx}" in t:
                    compared += 1
                    metrics = [("sum", f"s{p.idx}")]
                    if f"mn{p.idx}" in s and f"mn{p.idx}" in t and not p.cmp.is_temporal:
                        metrics += [("min", f"mn{p.idx}"), ("max", f"mx{p.idx}")]
                    for m, k in metrics:
                        a, b = _num(s.get(k)), _num(t.get(k))
                        if not _close(a, b, abs_t, rel_t):
                            rows.append({"column": p.src.name, "metric": m, "source": display(a),
                                         "target": display(b), "difference": display((b or 0) - (a or 0))})
                    # average derived from sum / non-null count
                    sn, tn = _num(s.get(f"n{p.idx}")) or 0, _num(t.get(f"n{p.idx}")) or 0
                    sa = (_num(s.get(f"s{p.idx}")) or 0) / sn if sn else None
                    ta = (_num(t.get(f"s{p.idx}")) or 0) / tn if tn else None
                    if not _close(sa, ta, abs_t, max(rel_t, 1e-12)):
                        rows.append({"column": p.src.name, "metric": "avg", "source": display(sa),
                                     "target": display(ta), "difference": None})
                elif p.cmp.is_temporal and f"mn{p.idx}" in s and f"mn{p.idx}" in t:
                    compared += 1
                    opts = NormOptions(timestamp_precision=self.cfg.tolerances.timestamp_precision)
                    for m, k in (("min", f"mn{p.idx}"), ("max", f"mx{p.idx}")):
                        a = normalize_series(pd.Series([s.get(k)], dtype=object), p.cmp,
                                             SideContext(p.src.logical, self.src.conn.tz), opts).iloc[0]
                        b = normalize_series(pd.Series([t.get(k)], dtype=object), p.cmp,
                                             SideContext(p.tgt.logical, self.tgt.conn.tz), opts).iloc[0]
                        if a != b:
                            rows.append({"column": p.src.name, "metric": m, "source": a, "target": b,
                                         "difference": None})
            out.append(CheckResult(
                check="aggregates", name="Column aggregates (sum/avg/min/max)",
                status=Status.FAIL if rows else (Status.PASS if compared else Status.SKIPPED),
                message=f"{len(rows)} aggregate difference(s)" if rows else f"{compared} column(s) match",
                details={"mismatches": rows, "columns_compared": compared}))

        if "distinct" in checks and self.cfg.distinct_columns:
            rows = []
            for p in self.pairs:
                k = f"d{p.idx}"
                if k in s and k in t:
                    a, b = int(_num(s[k]) or 0), int(_num(t[k]) or 0)
                    if a != b:
                        rows.append({"column": p.src.name, "source": a, "target": b, "difference": b - a})
            out.append(CheckResult(check="distinct", name="Distinct value counts",
                                   status=Status.FAIL if rows else Status.PASS,
                                   message=f"{len(rows)} column(s) differ" if rows else "distinct counts match",
                                   details={"mismatches": rows}))
        return out

    # ------------------------------------------------------------------ data comparison
    def _column_specs(self) -> List[ColumnSpec]:
        n = self.cfg.normalize
        ci = {c.lower() for c in n.case_insensitive_columns}
        specs = []
        for p in self.pairs:
            abs_t, rel_t = self._tol(p)
            opts = NormOptions(timestamp_precision=self.cfg.tolerances.timestamp_precision,
                               trim=n.trim_strings, rtrim=n.rtrim_strings,
                               lower=p.src.name.lower() in ci, empty_as_null=n.empty_string_as_null)
            specs.append(ColumnSpec(
                cid=p.cid, source_name=p.src.name, target_name=p.tgt.name, cmp=p.cmp,
                src_ctx=SideContext(p.src.logical, self.src.conn.tz),
                tgt_ctx=SideContext(p.tgt.logical, self.tgt.conn.tz),
                opts=opts, abs_tol=abs_t if p.cmp.is_numeric else 0.0,
                rel_tol=rel_t if p.cmp.is_numeric else 0.0, is_key=p.is_key))
        return specs

    def _fetch_sql(self, side: Side, labels: Optional[Sequence[str]]) -> str:
        cols = []
        for p in self.pairs:
            c = p.src if side.role == "source" else p.tgt
            cols.append(f"{side.d.fetch_column(side.col(c), c.logical)} AS {p.cid}")
        extra = side.bucket_filter(labels) if labels else []
        return f"SELECT {', '.join(cols)} FROM {side.frm}{side.where(extra, keys_not_null=True)}"

    def _fetch_and_compare(self, labels: Optional[Sequence[str]], specs: List[ColumnSpec],
                           agg: CompareStats) -> None:
        s_df, t_df = self._both(
            lambda: self.src.query(self._fetch_sql(self.src, labels), self.src.d.uses_null_sentinel),
            lambda: self.tgt.query(self._fetch_sql(self.tgt, labels), self.tgt.d.uses_null_sentinel))
        self.stats["rows_fetched_source"] += len(s_df)
        self.stats["rows_fetched_target"] += len(t_df)
        st = compare_frames(s_df, t_df, specs, self.cfg.compare.max_discrepancy_samples, self.samples)
        agg.add(st)

    @staticmethod
    def _prefixes(digits: int) -> List[str]:
        return ["".join(p) for p in itertools.product(HEX, repeat=digits)]

    def _batches(self, labels: List[str], rows_per_label: float) -> List[List[str]]:
        per = max(1, int(self.cfg.compare.fetch_batch_rows // max(rows_per_label, 1)))
        per = min(per, 256)
        return [labels[i:i + per] for i in range(0, len(labels), per)]

    def _row_count(self) -> int:
        def cnt(side: Side) -> int:
            return int(_num(side.query(f"SELECT COUNT(*) AS c FROM {side.frm}{side.where()}").iat[0, 0]) or 0)
        a, b = self._both(lambda: cnt(self.src), lambda: cnt(self.tgt))
        self.stats["source_rows"], self.stats["target_rows"] = a, b
        return max(a, b)

    def _data_check(self, profile: Optional[Dict[str, Any]]) -> CheckResult:
        t0 = time.perf_counter()
        opts = self.cfg.compare
        if opts.mode == "none":
            return CheckResult(check="data", name="Row-level comparison", status=Status.SKIPPED, message="mode=none")
        if not self.src.key_cols:
            return CheckResult(check="data", name="Row-level comparison", status=Status.SKIPPED,
                               message="no key columns configured or detected")
        n = (max(self.stats.get("source_rows", 0), self.stats.get("target_rows", 0))
             if profile is not None else self._row_count())
        mode = opts.mode
        if mode == "auto":
            mode = "full" if n <= opts.full_max_rows else "profile"
        self._say(f"row-level comparison: mode={mode}, ~{n:,} rows")
        specs = self._column_specs()
        agg = CompareStats()
        detail: Dict[str, Any] = {"mode": mode}

        if mode == "full" or mode == "sample":
            if mode == "full" and n <= opts.fetch_batch_rows:
                self._fetch_and_compare(None, specs, agg)
                detail["batches"] = 1
            else:
                digits = 1
                while n / (16 ** digits) > opts.fetch_batch_rows and digits < opts.max_bucket_hex_digits:
                    digits += 1
                if mode == "sample":
                    digits = max(digits, 2)
                labels = self._prefixes(digits)
                if mode == "sample":
                    k = max(1, math.ceil(len(labels) * opts.sample_pct / 100.0))
                    labels = sorted(labels, key=lambda x: hashlib.md5(("dv" + x).encode()).hexdigest())[:k]
                    detail["sampled_buckets"] = f"{k}/{16 ** digits}"
                    detail["sample_pct"] = round(100.0 * k / (16 ** digits), 3)
                batches = self._batches(labels, n / (16 ** digits))
                for i, b in enumerate(batches, 1):
                    self._say(f"{mode}: batch {i}/{len(batches)}")
                    self._fetch_and_compare(b, specs, agg)
                detail["batches"] = len(batches)
        else:  # profile
            self._profile_drilldown(n, specs, agg, detail)

        detail.update({
            "source_rows_compared": agg.source_rows, "target_rows_compared": agg.target_rows,
            "matched_rows": agg.matched_rows, "identical_rows": agg.identical_rows,
            "changed_rows": agg.changed_rows, "missing_in_target": agg.missing_in_target,
            "extra_in_target": agg.extra_in_target, "duplicate_keys_source": agg.duplicate_keys_source,
            "duplicate_keys_target": agg.duplicate_keys_target, "column_mismatches": agg.column_mismatches,
        })
        bad = agg.total_differences
        unresolved = detail.get("unresolved_bucket_differences", 0)
        parts = []
        for label, v in (("missing in target", agg.missing_in_target), ("extra in target", agg.extra_in_target),
                         ("changed", agg.changed_rows), ("duplicate keys (source)", agg.duplicate_keys_source),
                         ("duplicate keys (target)", agg.duplicate_keys_target)):
            if v:
                parts.append(f"{v:,} {label}")
        if detail.get("unresolved_bucket_differences"):
            parts.append(f"{detail['unresolved_bucket_differences']} bucket checksum difference(s) not "
                         "explained at row level (check normalization / tolerances)")
        if mode == "profile":
            ok_msg = (f"all {detail.get('buckets_compared', 0):,} hash buckets identical "
                      f"(~{n:,} rows verified in-database, {agg.source_rows + agg.target_rows:,} rows transferred)")
        else:
            ok_msg = f"{agg.matched_rows:,} rows identical" + (f" (sample {detail.get('sample_pct')}%)" if mode == "sample" else "")
        return CheckResult(
            check="data", name=f"Row-level comparison ({mode})",
            status=Status.FAIL if bad else (Status.WARN if unresolved else Status.PASS),
            message=", ".join(parts) if parts else ok_msg,
            source_value=agg.source_rows, target_value=agg.target_rows,
            duration_s=round(time.perf_counter() - t0, 2), details=detail)

    # ------------------------------------------------------------------ bucket profile
    def _row_weight(self, side: Side) -> str:
        """Per-row weight in [1, 256] derived from the key hash.

        Weighting every value by its row's key makes the bucket checksums row-aware: two rows
        in the same bucket swapping values (a classic mis-aligned ETL bug) changes the
        checksum, whereas a plain SUM per column would not notice.
        """
        d = side.d
        return f"({d.hex_to_int(d.substr(d.md5_hex(side.key_text()), 9, 2), 2)} + 1)"

    def _bucket_sql(self, side: Side, digits: int, within: Optional[Sequence[str]]) -> str:
        d = side.d
        b = side.bucket(digits)
        w = self._row_weight(side)
        sel = [f"{b} AS dv_b", "COUNT(*) AS dv_rows"]
        cmp_opts = self.cfg.compare
        for p in self.pairs:
            if p.is_key:
                continue
            c = p.src if side.role == "source" else p.tgt
            e = side.col(c)
            if p.cmp == Logical.STRING and c.logical == Logical.STRING:
                e = side.string_expr(c)
            sel.append(f"COUNT({e}) AS n{p.idx}")
            if p.cmp.is_numeric and c.logical.is_numeric:
                sel.append(f"{d.weighted_sum(e, c.logical, w)} AS s{p.idx}")
            elif p.cmp == Logical.BOOLEAN:
                inner = d.bool_to_int(e) if c.logical == Logical.BOOLEAN else e
                sel.append(f"SUM(({inner}) * {w}) AS s{p.idx}")
            elif p.cmp == Logical.STRING and c.logical == Logical.STRING:
                if cmp_opts.string_checksums:
                    row_text = d.concat([side.key_text(), d.lit("|"), d.cast_text(e)])
                    h = d.hex_to_int(d.substr(d.md5_hex(row_text), 1, 7), 7)
                    # explicit NULL guard: CONCAT/|| NULL semantics differ between engines
                    sel.append(f"SUM(CASE WHEN {e} IS NULL THEN NULL ELSE {h} END) AS h{p.idx}")
                else:
                    sel.append(f"SUM({d.char_length(e)} * {w}) AS h{p.idx}")
            elif p.cmp.is_temporal and p.temporal_ok and cmp_opts.temporal_checksums:
                sel.append(f"SUM({d.epoch_seconds(e, c.logical)} * {w}) AS e{p.idx}")
                sel.append(f"SUM(CASE WHEN {e} IS NOT NULL THEN {w} END) AS w{p.idx}")
        extra = side.bucket_filter(within) if within else []
        return (f"SELECT {', '.join(sel)} FROM {side.frm}{side.where(extra, keys_not_null=True)} "
                f"GROUP BY {b}")

    def _bucket_diff(self, s_df: pd.DataFrame, t_df: pd.DataFrame) -> Tuple[List[Tuple[str, int]], int]:
        """Return mismatching (label, est_rows) and the number of buckets compared."""
        s_map = {str(r["dv_b"]): r for r in s_df.to_dict("records")}
        t_map = {str(r["dv_b"]): r for r in t_df.to_dict("records")}
        labels = set(s_map) | set(t_map)
        bad: List[Tuple[str, int]] = []
        for lab in labels:
            a, b = s_map.get(lab), t_map.get(lab)
            rows = max(int(_num((a or {}).get("dv_rows")) or 0), int(_num((b or {}).get("dv_rows")) or 0))
            if a is None or b is None or not self._bucket_equal(a, b):
                bad.append((lab, rows))
        return sorted(bad), len(labels)

    def _bucket_equal(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        if _num(a["dv_rows"]) != _num(b["dv_rows"]):
            return False
        for p in self.pairs:
            if p.is_key:
                continue
            i = p.idx
            na, nb = _num(a.get(f"n{i}")) or 0, _num(b.get(f"n{i}")) or 0
            if na != nb:
                return False
            if f"s{i}" in a:
                abs_t, rel_t = self._tol(p)
                # per-row tolerance scales with the number of rows in the bucket
                if not _close(_num(a.get(f"s{i}")), _num(b.get(f"s{i}")), abs_t * 256 * float(na or 1), rel_t):
                    return False
            if f"h{i}" in a and _num(a.get(f"h{i}")) != _num(b.get(f"h{i}")):
                return False
            if f"e{i}" in a:
                offs = getattr(p, "_offsets", [0, 0])
                # naive wall-clock epochs -> UTC: subtract offset x (sum of row weights)
                ea = (_num(a.get(f"e{i}")) or 0) - offs[0] * (_num(a.get(f"w{i}")) or 0)
                eb = (_num(b.get(f"e{i}")) or 0) - offs[1] * (_num(b.get(f"w{i}")) or 0)
                if ea != eb:
                    return False
        return True

    def _profile_drilldown(self, n: int, specs: List[ColumnSpec], agg: CompareStats, detail: Dict[str, Any]) -> None:
        opts = self.cfg.compare
        digits = max(1, opts.initial_bucket_hex_digits)
        within: Optional[List[str]] = None
        compared_total = 0
        levels = []
        leaves: List[Tuple[str, int]] = []
        while True:
            self._say(f"profile: bucket checksums at {digits} hex digit(s)"
                      + (f" within {len(within)} bucket(s)" if within else ""))
            s_sql = self._bucket_sql(self.src, digits, within)
            t_sql = self._bucket_sql(self.tgt, digits, within)
            s_df, t_df = self._both(lambda: self.src.query(s_sql), lambda: self.tgt.query(t_sql))  # noqa: B023
            bad, compared = self._bucket_diff(s_df, t_df)
            compared_total += compared if within is None else 0
            levels.append({"digits": digits, "buckets": compared, "mismatched": len(bad)})
            if not bad:
                break
            big = [b for b in bad if b[1] > opts.leaf_bucket_rows and digits < opts.max_bucket_hex_digits]
            leaves += [b for b in bad if b not in big]
            if not big:
                break
            within = [b[0] for b in big]
            digits += 1
        detail.update({"buckets_compared": compared_total, "levels": levels, "mismatched_leaf_buckets": len(leaves)})
        if not leaves:
            return
        before = agg.total_differences
        # group leaves into fetch batches by estimated row count
        batch: List[str] = []
        rows_in_batch = 0
        batches: List[List[str]] = []
        for lab, rows in leaves:
            if batch and (rows_in_batch + rows > opts.fetch_batch_rows or len(batch) >= 256):
                batches.append(batch)
                batch, rows_in_batch = [], 0
            batch.append(lab)
            rows_in_batch += rows
        if batch:
            batches.append(batch)
        for i, b in enumerate(batches, 1):
            self._say(f"profile: fetching rows for mismatched buckets, batch {i}/{len(batches)}")
            self._fetch_and_compare(b, specs, agg)
        detail["drilldown_batches"] = len(batches)
        if agg.total_differences == before:
            detail["unresolved_bucket_differences"] = len(leaves)

    # ------------------------------------------------------------------ custom SQL checks
    def _custom_check(self, cc) -> CheckResult:
        t0 = time.perf_counter()
        try:
            s_df, t_df = self._both(lambda: self.src.query(cc.source_sql), lambda: self.tgt.query(cc.target_sql))
        except Exception as exc:  # noqa: BLE001
            return CheckResult(check="custom", name=cc.name, status=Status.ERROR, message=str(exc))
        fail = Status.FAIL if cc.severity == "fail" else Status.WARN

        def cell(v: Any) -> Any:
            n_ = _num(v)
            return n_ if n_ is not None else (None if v is None else str(v))

        if s_df.shape == (1, 1) and t_df.shape == (1, 1):
            a, b = cell(s_df.iat[0, 0]), cell(t_df.iat[0, 0])
            ok = _close(a, b, cc.tolerance_abs, cc.tolerance_rel) if isinstance(a, Decimal) and isinstance(b, Decimal) else a == b
            return CheckResult(check="custom", name=cc.name, status=Status.PASS if ok else fail,
                               source_value=display(a), target_value=display(b),
                               message="values match" if ok else "values differ",
                               duration_s=round(time.perf_counter() - t0, 2))

        def rows(df: pd.DataFrame) -> List[Tuple]:
            return sorted((tuple(display(cell(v)) or "<NULL>" for v in r) for r in df.itertuples(index=False)))

        sr, tr = rows(s_df), rows(t_df)
        only_s = sorted(set(sr) - set(tr))
        only_t = sorted(set(tr) - set(sr))
        ok = not only_s and not only_t and len(sr) == len(tr)
        return CheckResult(
            check="custom", name=cc.name, status=Status.PASS if ok else fail,
            source_value=f"{len(sr)} rows", target_value=f"{len(tr)} rows",
            message="result sets match" if ok else f"{len(only_s)} row(s) only in source, {len(only_t)} only in target",
            duration_s=round(time.perf_counter() - t0, 2),
            details={"only_in_source": [list(r) for r in only_s[:50]], "only_in_target": [list(r) for r in only_t[:50]]})


# =========================================================================== explain
@dataclass
class SideSQL:
    role: str                       # source | target
    connection: str
    connection_type: str
    dialect: str
    profile_sql: str
    bucket_sql: Optional[str]       # None when the table has no key
    fetch_sql: Optional[str]
    has_filter: bool
    billing_note: Optional[str]     # set for engines billed by data scanned


@dataclass
class TableExplain:
    table: str
    schema_check: CheckResult
    bucket_digits: int
    sides: List[SideSQL]


def explain_table(cfg: TableConfig, source: Connector, target: Connector) -> TableExplain:
    """The SQL each side would run for a table, built exactly as a validation run builds it.

    Only catalog queries are executed (to map columns and find keys).
    """
    v = TableValidator(cfg, source, target)
    try:
        s_info = v.src.conn.get_table_info(cfg.source)
        t_info = v.tgt.conn.get_table_info(cfg.target)
        chk = v._map_columns(s_info, t_info)
        digits = cfg.compare.initial_bucket_hex_digits
        sides = []
        for side in (v.src, v.tgt):
            keyed = bool(v.src.key_cols)
            sides.append(SideSQL(
                role=side.role, connection=side.conn.name, connection_type=side.conn.config.type,
                dialect=side.d.name, profile_sql=v._profile_sql(side),
                bucket_sql=v._bucket_sql(side, digits, None) if keyed else None,
                fetch_sql=v._fetch_sql(side, ["a0", "a1"]) if keyed else None,
                has_filter=bool(side.ds.filter), billing_note=side.d.billing_note,
            ))
        return TableExplain(cfg.name, chk, digits, sides)
    finally:
        v._pool.shutdown(wait=False)
