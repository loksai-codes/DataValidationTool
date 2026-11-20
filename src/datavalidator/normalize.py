"""
Value normalization for row-level comparison.

Drivers return the same logical value in many shapes (``Decimal('1.50')``, ``1.5``, ``'1.50'``
from Athena CSV, ``datetime`` naive in Asia/Shanghai vs aware UTC ...). Every column pair is
compared under one *comparison logical type*, and both sides are normalized into canonical
Python values before comparison:

* integer / decimal -> ``decimal.Decimal`` (exact; 1.50 == 1.5)
* float             -> ``float`` (compared with a relative tolerance)
* timestamps        -> ``'YYYY-MM-DD HH:MM:SS[.ffffff]'`` in UTC, truncated to the configured precision
* dates             -> ``'YYYY-MM-DD'``
* booleans          -> ``'true'`` / ``'false'``
* json / complex    -> compact JSON with sorted keys
* binary            -> lower-case hex
* strings           -> ``str`` (optional trim / case folding / empty-as-null)

Values that cannot be parsed are never silently turned into NULL; they become
``'<unparsable:...>'`` so they surface as mismatches.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from datavalidator.datatypes import Logical

UTC = ZoneInfo("UTC")
_TRUE = {"true", "t", "1", "y", "yes", "on"}
_FALSE = {"false", "f", "0", "n", "no", "off"}


@dataclass
class SideContext:
    logical: Logical
    tz: ZoneInfo


@dataclass
class NormOptions:
    timestamp_precision: str = "us"
    trim: bool = False
    rtrim: bool = False
    lower: bool = False
    empty_as_null: bool = False


def comparison_logical(src: Logical, tgt: Logical) -> Logical:
    """Choose the logical type under which a source/target column pair is compared."""
    if src == tgt:
        return src
    pair = {src, tgt}
    if src.is_numeric and tgt.is_numeric:
        return Logical.FLOAT if Logical.FLOAT in pair else Logical.DECIMAL
    if pair <= {Logical.BOOLEAN, Logical.INTEGER}:
        return Logical.BOOLEAN
    if src.is_temporal and tgt.is_temporal:
        return Logical.TIMESTAMP_TZ
    if pair <= {Logical.JSON, Logical.STRING, Logical.COMPLEX}:
        return Logical.JSON
    if Logical.STRING in pair:
        other = (pair - {Logical.STRING}).pop()
        return Logical.TIMESTAMP_TZ if other == Logical.TIMESTAMP else other
    if Logical.UNKNOWN in pair:
        return Logical.STRING
    return Logical.STRING


def _is_null(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float):
        return v != v
    if v is pd.NaT or v is pd.NA:
        return True
    if isinstance(v, np.floating):
        return bool(np.isnan(v))
    if isinstance(v, np.datetime64):
        return bool(np.isnat(v))
    if isinstance(v, Decimal):
        return v.is_nan()
    return False


def _to_decimal(v: Any) -> Any:
    if isinstance(v, bool):
        return Decimal(int(v))
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, np.integer)):
        return Decimal(int(v))
    if isinstance(v, (float, np.floating)):
        return Decimal(repr(float(v)))
    try:
        return Decimal(str(v).strip())
    except InvalidOperation:
        return f"<unparsable:{v}>"


def _to_float(v: Any) -> Any:
    try:
        return float(v)
    except (TypeError, ValueError):
        return f"<unparsable:{v}>"


def _to_bool(v: Any) -> Any:
    if isinstance(v, (bool, np.bool_)):
        return "true" if v else "false"
    if isinstance(v, (int, float, Decimal, np.integer, np.floating)):
        return "true" if v != 0 else "false"
    if isinstance(v, (bytes, bytearray)):
        return "true" if any(v) else "false"
    s = str(v).strip().lower()
    if s in _TRUE:
        return "true"
    if s in _FALSE:
        return "false"
    return f"<unparsable:{v}>"


def _json(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        obj = v
    else:
        if isinstance(v, (bytes, bytearray)):
            v = v.decode("utf-8", errors="replace")
        try:
            obj = json.loads(v)
        except (TypeError, ValueError):
            return str(v)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def _binary(v: Any) -> Any:
    if isinstance(v, memoryview):
        v = v.tobytes()
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).hex()
    s = str(v).strip()
    if s.startswith("\\x"):
        s = s[2:]
    return s.lower()


def _string(v: Any, opts: NormOptions) -> Any:
    if isinstance(v, (bytes, bytearray, memoryview)):
        v = bytes(v).decode("utf-8", errors="replace")
    elif isinstance(v, float) and v.is_integer():
        v = str(int(v))
    s = str(v)
    if opts.trim:
        s = s.strip()
    elif opts.rtrim:
        s = s.rstrip()
    if opts.lower:
        s = s.lower()
    if opts.empty_as_null and s == "":
        return None
    return s


_FMT = {"s": 19, "ms": 23, "us": 26}


def _timestamps(series: pd.Series, side: SideContext, opts: NormOptions, date_only: bool) -> pd.Series:
    """Vectorized: parse -> localize naive to side tz (or UTC for tz-typed columns) -> UTC -> text."""
    out = pd.Series([None] * len(series), index=series.index, dtype=object)
    mask = ~series.map(_is_null).astype(bool)
    if not mask.any():
        return out
    raw = series[mask]

    def to_ts(v: Any):
        if isinstance(v, pd.Timestamp):
            return v
        if isinstance(v, dt.datetime):
            return pd.Timestamp(v)
        if isinstance(v, dt.date):
            return pd.Timestamp(dt.datetime(v.year, v.month, v.day))
        if isinstance(v, (int, np.integer)):          # epoch seconds / ms heuristics
            iv = int(v)
            return pd.Timestamp(iv, unit="ms" if abs(iv) > 10**11 else "s", tz="UTC")
        s = str(v).strip()
        try:
            return pd.Timestamp(s)
        except (ValueError, TypeError):
            return None

    stamps = raw.map(to_ts)
    unit = opts.timestamp_precision
    width = _FMT[unit]

    # ---- fast vectorized path: homogeneous naive or homogeneous aware values
    good = stamps.map(lambda x: x is not None and not pd.isna(x)).astype(bool)
    if good.all():
        tzs = {x.tzinfo is None for x in stamps}
        if len(tzs) == 1:
            try:
                idx = pd.DatetimeIndex(list(stamps))
                if date_only:
                    out.loc[stamps.index] = list(idx.strftime("%Y-%m-%d"))
                    return out
                if tzs == {True}:
                    zone = UTC if side.logical == Logical.TIMESTAMP_TZ else side.tz
                    idx = idx.tz_localize(zone, ambiguous=np.ones(len(idx), dtype=bool),
                                          nonexistent="shift_forward")
                idx = idx.tz_convert("UTC").floor(unit)
                out.loc[stamps.index] = [s[:width] for s in idx.strftime("%Y-%m-%d %H:%M:%S.%f")]
                return out
            except (ValueError, TypeError, OverflowError):
                pass  # fall back to the per-value path below

    for idx, ts in stamps.items():
        if ts is None or pd.isna(ts):
            out[idx] = f"<unparsable:{raw[idx]}>"
            continue
        if date_only:
            out[idx] = ts.strftime("%Y-%m-%d")
            continue
        if ts.tzinfo is None:
            local_tz = UTC if side.logical == Logical.TIMESTAMP_TZ else side.tz
            ts = pd.Timestamp(ts.to_pydatetime().replace(tzinfo=local_tz))
        ts = ts.tz_convert("UTC").floor(unit)
        out[idx] = ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:width]
    return out


def normalize_series(series: pd.Series, cmp: Logical, side: SideContext, opts: NormOptions) -> pd.Series:
    """Return a new object Series of canonical values (None for SQL NULL)."""
    if cmp in (Logical.TIMESTAMP, Logical.TIMESTAMP_TZ):
        return _timestamps(series, side, opts, date_only=False)
    if cmp == Logical.DATE:
        return _timestamps(series, side, opts, date_only=True)

    if cmp in (Logical.INTEGER, Logical.DECIMAL):
        fn = _to_decimal
    elif cmp == Logical.FLOAT:
        fn = _to_float
    elif cmp == Logical.BOOLEAN:
        fn = _to_bool
    elif cmp in (Logical.JSON, Logical.COMPLEX):
        fn = _json
    elif cmp == Logical.BINARY:
        fn = _binary
    else:
        def fn(v: Any, _o=opts) -> Any:
            return _string(v, _o)

    def apply(v: Any) -> Any:
        if _is_null(v):
            return None
        return fn(v)

    out = pd.Series([apply(v) for v in series.tolist()], index=series.index, dtype=object)
    return out


def display(v: Any) -> Optional[str]:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else str(v)
    return str(v)
