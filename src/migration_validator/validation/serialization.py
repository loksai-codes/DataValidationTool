"""
Canonical deterministic serialization and SHA-256 fingerprinting.
Ensures identical data produces identical hashes across diverse database engines.
"""

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pandas as pd

# Unique sentinel for NULL to distinguish it from the literal string "NULL" or ""
NULL_SENTINEL = "\x00__VALIDATOR_NULL__\x00"


def canonical_value_str(val: Any) -> str:
    """
    Convert a single column value into its canonical deterministic string representation.
    Handles None, NaNs, floats, decimals, timestamps, dates, and strings.
    """
    if val is None or pd.isna(val):
        return NULL_SENTINEL

    # Decimal & Floating Point
    if isinstance(val, (float, Decimal)):
        # Normalize to 4 decimal places for comparisons, avoiding floating point jitter
        return f"{float(val):.4f}"

    # Integers & Booleans
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)

    # Datetime & Timestamps
    if isinstance(val, (datetime, pd.Timestamp)):
        # Format without microseconds or timezone ambiguity
        return val.strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(val, date):
        return val.strftime("%Y-%m-%d")

    # Strings and Other types
    s = str(val).strip()
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
