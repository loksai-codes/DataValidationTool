"""
Logical type system.

Every engine reports native type names differently (MySQL ``datetime``, Postgres
``timestamp without time zone``, Athena ``timestamp``, MaxCompute ``DATETIME`` ...).
We map each native type to one portable *logical* type, which drives:

* schema compatibility rules,
* which in-database profile metrics are computed for a column,
* how values are normalized before row-level comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class Logical(str, Enum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    FLOAT = "float"
    STRING = "string"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"        # naive / "without time zone"
    TIMESTAMP_TZ = "timestamp_tz"  # absolute instant (timestamptz, MySQL TIMESTAMP w/ UTC session)
    TIME = "time"
    BINARY = "binary"
    JSON = "json"
    COMPLEX = "complex"            # array / map / struct
    UNKNOWN = "unknown"

    @property
    def is_numeric(self) -> bool:
        return self in (Logical.INTEGER, Logical.DECIMAL, Logical.FLOAT)

    @property
    def is_temporal(self) -> bool:
        return self in (Logical.DATE, Logical.TIMESTAMP, Logical.TIMESTAMP_TZ)


@dataclass(frozen=True)
class TypeInfo:
    native: str
    logical: Logical
    length: Optional[int] = None      # varchar(n) / char(n)
    precision: Optional[int] = None   # decimal(p, s)
    scale: Optional[int] = None


_PARAMS = re.compile(r"\(([^)]*)\)")

# Order matters: first match wins. Patterns are matched against the lower-cased base name.
_RULES: Tuple[Tuple[str, Logical], ...] = (
    # booleans first (MySQL tinyint(1) handled separately below)
    (r"^(bool|boolean|bit)$", Logical.BOOLEAN),
    # integers
    (r"^(tinyint|smallint|mediumint|int|integer|bigint|int2|int4|int8|int16|int32|int64|"
     r"serial|bigserial|smallserial|long|short|byte|unsigned big int|hugeint|ubigint|uinteger|"
     r"usmallint|utinyint)( unsigned)?$", Logical.INTEGER),
    # exact numerics
    (r"^(decimal|numeric|number|dec|money|smallmoney)$", Logical.DECIMAL),
    # approximate numerics
    (r"^(float|float4|float8|double|double precision|real|binary_float|binary_double)$",
     Logical.FLOAT),
    # tz-aware timestamps
    (r"^(timestamptz|timestamp with time zone|timestamp_tz|timestamp with local time zone|"
     r"timestamp_ltz)$", Logical.TIMESTAMP_TZ),
    # naive timestamps
    (r"^(timestamp|timestamp without time zone|datetime|datetime2|smalldatetime|timestamp_ntz)$",
     Logical.TIMESTAMP),
    (r"^date$", Logical.DATE),
    (r"^(time|time without time zone|time with time zone|timetz)$", Logical.TIME),
    # strings
    (r"^(char|character|nchar|varchar|character varying|nvarchar|varchar2|nvarchar2|text|"
     r"tinytext|mediumtext|longtext|string|clob|nclob|uuid|enum|set|citext|name|bpchar|"
     r"large_string|str|object)$", Logical.STRING),
    # json
    (r"^(json|jsonb)$", Logical.JSON),
    # binary
    (r"^(binary|varbinary|blob|tinyblob|mediumblob|longblob|bytea|raw|bytes|image)$",
     Logical.BINARY),
    # complex
    (r"^(array|map|struct|row|list|user-defined|anyarray)$", Logical.COMPLEX),
)


def parse_type(native: str, engine: Optional[str] = None) -> TypeInfo:
    """Parse a native type string into a :class:`TypeInfo`.

    ``engine`` lets us apply engine-specific quirks (e.g. MySQL ``TIMESTAMP`` is stored as UTC
    and we always open MySQL sessions with ``time_zone='+00:00'``, so it is treated as an
    absolute instant, while ``DATETIME`` is naive).
    """
    raw = (native or "").strip()
    low = raw.lower()

    # complex types first: array<int>, map<string,int>, struct<...>, row(...)
    if re.match(r"^(array|map|struct|row)\s*[<(]", low) or low.endswith("[]"):
        return TypeInfo(raw, Logical.COMPLEX)

    params = _PARAMS.search(low)
    nums = []
    if params:
        for p in params.group(1).split(","):
            p = p.strip()
            if p.isdigit():
                nums.append(int(p))
    base = _PARAMS.sub("", low).strip()
    base = re.sub(r"\s+", " ", base)

    # MySQL tinyint(1) is the conventional boolean
    if engine == "mysql" and base.startswith("tinyint") and nums == [1]:
        return TypeInfo(raw, Logical.BOOLEAN)
    if engine == "mysql" and base == "timestamp":
        return TypeInfo(raw, Logical.TIMESTAMP_TZ)
    if base == "bit" and nums and nums[0] > 1:
        return TypeInfo(raw, Logical.BINARY)
    # Oracle NUMBER without scale behaves like an integer; NUMBER(p,0) too
    if base in ("number", "numeric", "decimal") and len(nums) == 2 and nums[1] == 0 and engine == "oracle":
        return TypeInfo(raw, Logical.INTEGER, precision=nums[0], scale=0)

    for pattern, logical in _RULES:
        if re.match(pattern, base):
            info = TypeInfo(raw, logical)
            if logical == Logical.STRING and nums:
                info = TypeInfo(raw, logical, length=nums[0])
            elif logical == Logical.DECIMAL:
                prec = nums[0] if len(nums) >= 1 else None
                scale = nums[1] if len(nums) >= 2 else (0 if prec is not None else None)
                info = TypeInfo(raw, logical, precision=prec, scale=scale)
            return info
    return TypeInfo(raw, Logical.UNKNOWN)


@dataclass
class Compatibility:
    level: str  # "ok" | "warn" | "fail"
    reason: Optional[str] = None


_SAFE_WIDENING = {
    (Logical.INTEGER, Logical.DECIMAL),
    (Logical.INTEGER, Logical.FLOAT),
    (Logical.DECIMAL, Logical.FLOAT),
    (Logical.DATE, Logical.TIMESTAMP),
    (Logical.DATE, Logical.TIMESTAMP_TZ),
    (Logical.TIMESTAMP, Logical.TIMESTAMP_TZ),
    (Logical.TIMESTAMP_TZ, Logical.TIMESTAMP),
    (Logical.JSON, Logical.STRING),
    (Logical.STRING, Logical.JSON),
    (Logical.BOOLEAN, Logical.INTEGER),
    (Logical.INTEGER, Logical.BOOLEAN),
    (Logical.COMPLEX, Logical.STRING),
    (Logical.COMPLEX, Logical.JSON),
}


def check_compatibility(src: TypeInfo, dst: TypeInfo) -> Compatibility:
    """Decide whether ``dst`` can faithfully hold every value of ``src``."""
    if Logical.UNKNOWN in (src.logical, dst.logical):
        return Compatibility("warn", f"unrecognized type ({src.native} -> {dst.native})")

    if src.logical == dst.logical:
        if src.logical == Logical.STRING and src.length and dst.length and dst.length < src.length:
            return Compatibility("fail", f"string length shrinks {src.length} -> {dst.length} (truncation risk)")
        if src.logical == Logical.DECIMAL:
            if src.scale is not None and dst.scale is not None and dst.scale < src.scale:
                return Compatibility("fail", f"decimal scale shrinks {src.scale} -> {dst.scale} (rounding)")
            if src.precision and dst.precision:
                src_int = src.precision - (src.scale or 0)
                dst_int = dst.precision - (dst.scale or 0)
                if dst_int < src_int:
                    return Compatibility("fail", f"decimal integer digits shrink {src_int} -> {dst_int} (overflow risk)")
        return Compatibility("ok")

    pair = (src.logical, dst.logical)
    if pair in _SAFE_WIDENING:
        if pair == (Logical.DECIMAL, Logical.FLOAT):
            return Compatibility("warn", "decimal -> float may lose precision")
        if pair in ((Logical.TIMESTAMP, Logical.TIMESTAMP_TZ), (Logical.TIMESTAMP_TZ, Logical.TIMESTAMP)):
            return Compatibility("warn", "time zone semantics change; check connection 'timezone' settings")
        if pair in ((Logical.BOOLEAN, Logical.INTEGER), (Logical.INTEGER, Logical.BOOLEAN)):
            return Compatibility("warn", "boolean <-> integer representation change")
        return Compatibility("ok")

    if dst.logical == Logical.STRING:
        return Compatibility("warn", f"{src.logical.value} stored as string; formatting may differ")
    if src.logical == Logical.FLOAT and dst.logical == Logical.DECIMAL:
        return Compatibility("warn", "float -> decimal; values are rounded to the target scale")
    if src.logical == Logical.STRING and dst.logical != Logical.STRING:
        return Compatibility("warn", f"string parsed into {dst.logical.value}; unparsable values would be lost")
    return Compatibility("fail", f"incompatible types {src.native} ({src.logical.value}) -> {dst.native} ({dst.logical.value})")
