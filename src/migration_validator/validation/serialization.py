"""
Canonical deterministic serialization and SHA-256 fingerprinting.
Ensures identical data produces identical hashes across diverse database engines.
"""

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pandas as pd

# Unique sentinel for NULL to distinguish it from the literal string "NULL" or ""
NULL_SENTINEL = "\x00__VALIDATOR_NULL__\x00"


def canonical_value_str(val: Any) -> str:
    """
    Convert a single column value into its canonical deterministic string representation.
    Handles None, NaNs, floats, decimals, timestamps, dates, booleans, integers, and strings.
    Ensures identical numeric values (e.g. 10 and 10.0) and equivalent UTC timestamps match deterministically.
    """
    if val is None or pd.isna(val):
        return NULL_SENTINEL

    # Booleans (must check before int since bool subclasses int)
    if isinstance(val, bool):
        return "true" if val else "false"

    # Large integers (avoid IEEE 754 precision loss on 64-bit integer IDs)
    if isinstance(val, int):
        if abs(val) > 9_007_199_254_740_992:
            return str(val)

    # Floating point, Decimal, and standard integers
    if isinstance(val, (int, float, Decimal)):
        try:
            f = float(val)
            if f.is_integer() and abs(f) < 9_007_199_254_740_992:
                return str(int(f))
            return f"{f:.4f}"
        except (ValueError, OverflowError):
            return str(val)

    # Binary / Bytes
    if isinstance(val, (bytes, bytearray)):
        return val.hex()

    # JSON Objects & Collections
    if isinstance(val, (dict, list)):
        import json
        return json.dumps(val, sort_keys=True, default=str)

    # Datetime & Timestamps (normalize aware to UTC and drop timezone suffix for uniform comparison)
    if isinstance(val, (datetime, pd.Timestamp)):
        if getattr(val, "tzinfo", None) is not None:
            try:
                val = val.astimezone(timezone.utc)
            except Exception:
                pass
        if val.microsecond:
            return val.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")
        return val.strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(val, date):
        return val.strftime("%Y-%m-%d")

    # Strings and Other types
    s = str(val).strip()
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        import json
        try:
            parsed = json.loads(s)
            return json.dumps(parsed, sort_keys=True, default=str)
        except Exception:
            pass
    return s


def canonical_serialize_record(row_dict: Dict[str, Any], columns: Optional[List[str]] = None) -> str:
    """
    Deterministically serialize a record into a canonical string.
    Columns are sorted alphabetically to prevent column order bias.
    Format: col1:val1|col2:val2|...
    """
    target_cols = sorted(columns) if columns else sorted(row_dict.keys())
    parts = []
    for col in target_cols:
        val = row_dict.get(col)
        c_val = canonical_value_str(val)
        parts.append(f"{col}:{c_val}")

    return "|".join(parts)


def compute_record_hash(row_dict: Dict[str, Any], columns: Optional[List[str]] = None) -> str:
    """Compute the SHA-256 fingerprint of a record's canonical representation."""
    canonical_str = canonical_serialize_record(row_dict, columns)
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
