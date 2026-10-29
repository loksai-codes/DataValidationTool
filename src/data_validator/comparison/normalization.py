"""Type normalization rules for data comparison.

Handles strings, decimals, dates, timestamps, time zones, booleans, and NULLs.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
import re

from data_validator.config.models import NormalizationConfig


def normalize_null(val: Any) -> Any:
    """Normalize None, NaN, and special null representations."""
    if val is None:
        return None
    if isinstance(val, float) and (val != val):  # math.isnan
        return None
    return val


def normalize_string(val: str, config: NormalizationConfig) -> str:
    """Normalize string value according to config options."""
    s = val
    if config.trim_strings:
        s = s.strip()
    if config.ignore_whitespace_diffs:
        s = re.sub(r"\s+", " ", s).strip()
    if not config.case_sensitive_strings:
        s = s.lower()
    return s


def normalize_number_or_decimal(val: Any) -> Decimal | None:
    """Convert integer, float, or string to Decimal for high-precision comparison."""
    if val is None:
        return None
    try:
        if isinstance(val, (int, str)):
            return Decimal(str(val))
        elif isinstance(val, float):
            # Normalize float representation
            return Decimal(str(val))
        elif isinstance(val, Decimal):
            return val
    except InvalidOperation:
        return None
    return None


def compare_decimals(d1: Decimal | None, d2: Decimal | None, tolerance: str = "0") -> bool:
    """Compare two decimals with an optional tolerance threshold."""
    if d1 is None and d2 is None:
        return True
    if d1 is None or d2 is None:
        return False
    tol = Decimal(tolerance)
    return abs(d1 - d2) <= tol


def normalize_datetime(val: Any, target_tz: str = "UTC") -> datetime | None:
    """Normalize datetime/timestamp to UTC timezone-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        dt = val
    elif isinstance(val, date):
        dt = datetime.combine(val, time.min)
    elif isinstance(val, str):
        try:
            # Handle common ISO formats
            clean_str = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_str)
        except Exception:
            return None
    else:
        return None

    # If naive, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def normalize_value(val: Any, config: NormalizationConfig | None = None) -> Any:
    """Normalize a generic field value before comparison."""
    if config is None:
        config = NormalizationConfig()

    val = normalize_null(val)
    if val is None:
        return None

    if isinstance(val, bool):
        return val

    if isinstance(val, (int, float, Decimal)):
        # Normalize numeric representations
        dec = normalize_number_or_decimal(val)
        return dec if dec is not None else val

    if isinstance(val, (datetime, date)):
        return normalize_datetime(val, target_tz=config.timestamp_timezone)

    if isinstance(val, str):
        # Check if it looks like an ISO timestamp
        if len(val) >= 10 and ("-" in val or ":" in val):
            parsed_dt = normalize_datetime(val, target_tz=config.timestamp_timezone)
            if parsed_dt is not None:
                return parsed_dt

        # Check if it looks like a numeric string
        dec = normalize_number_or_decimal(val)
        if dec is not None:
            # If tolerance is 0, comparison can use normalized decimal
            return dec

        return normalize_string(val, config)

    if isinstance(val, (bytes, bytearray)):
        return bytes(val)

    return val


def values_are_equal(
    source_val: Any,
    target_val: Any,
    config: NormalizationConfig | None = None,
) -> bool:
    """Determine if two values are equal after applying normalization rules."""
    if config is None:
        config = NormalizationConfig()

    s_norm = normalize_value(source_val, config)
    t_norm = normalize_value(target_val, config)

    if s_norm is None and t_norm is None:
        return True
    if s_norm is None or t_norm is None:
        return False

    # Decimal comparison with tolerance
    if isinstance(s_norm, Decimal) and isinstance(t_norm, Decimal):
        return compare_decimals(s_norm, t_norm, config.decimal_tolerance)

    # Datetime comparison
    if isinstance(s_norm, datetime) and isinstance(t_norm, datetime):
        return s_norm == t_norm

    # Fallback equality
    return s_norm == t_norm


def normalize_key_tuple(record: dict[str, Any], key_columns: list[str]) -> tuple[Any, ...]:
    """Extract and normalize unique key values from a record as an immutable hashable tuple."""
    key_values = []
    for col in key_columns:
        # Case-insensitive column lookup
        col_lower = col.lower()
        val = None
        for k, v in record.items():
            if k.lower() == col_lower:
                val = v
                break

        val_norm = normalize_null(val)
        if val_norm is not None and isinstance(val_norm, (int, float, Decimal)):
            val_norm = normalize_number_or_decimal(val_norm)
        elif val_norm is not None and isinstance(val_norm, str):
            val_norm = val_norm.strip()

        key_values.append(val_norm)
    return tuple(key_values)
