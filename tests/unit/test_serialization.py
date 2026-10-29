"""
Unit tests for canonical serialization and SHA-256 fingerprinting.
"""

from datetime import datetime
from decimal import Decimal

import pandas as pd
import pytest

from migration_validator.validation.serialization import (
    NULL_SENTINEL,
    canonical_value_str,
    compute_record_hash,
)


@pytest.mark.unit
def test_null_handling_distinction():
    """Verify NULL is distinct from literal string 'NULL' and empty string ''."""
    null_canon = canonical_value_str(None)
    nan_canon = canonical_value_str(float("nan"))
    pd_na_canon = canonical_value_str(pd.NA)

    empty_str_canon = canonical_value_str("")
    literal_null_canon = canonical_value_str("NULL")

    assert null_canon == NULL_SENTINEL
    assert nan_canon == NULL_SENTINEL
    assert pd_na_canon == NULL_SENTINEL

    assert empty_str_canon == ""
    assert literal_null_canon == "NULL"

    assert null_canon != empty_str_canon
    assert null_canon != literal_null_canon


@pytest.mark.unit
def test_float_and_decimal_normalization():
    """Verify floats and Decimals normalize consistently to avoid precision jitter."""
    f1 = 248894.06323579658
    f2 = 248894.0632
    d1 = Decimal("248894.0632")

    assert canonical_value_str(f1) == "248894.0632"
    assert canonical_value_str(f2) == "248894.0632"
    assert canonical_value_str(d1) == "248894.0632"


@pytest.mark.unit
def test_timestamp_normalization():
    """Verify timestamps format deterministically to YYYY-MM-DD HH:MM:SS."""
    dt1 = datetime(2025, 7, 19, 5, 41, 2)
    dt2 = pd.Timestamp("2025-07-19 05:41:02.000000")

    assert canonical_value_str(dt1) == "2025-07-19 05:41:02"
    assert canonical_value_str(dt2) == "2025-07-19 05:41:02"


@pytest.mark.unit
def test_hash_determinism_and_column_order_independence():
    """Verify hashing produces identical digests regardless of dictionary insertion order."""
    row_a = {
        "customer_id": 1,
        "customer_name": "Upma Dash",
        "email": "qadim94@example.net",
        "city": "Dehri",
        "account_balance": 476171.91,
        "created_at": datetime(2025, 7, 19, 5, 41, 2)
    }

    row_b = {
        "created_at": datetime(2025, 7, 19, 5, 41, 2),
        "city": "Dehri",
        "customer_id": 1,
        "account_balance": 476171.91,
        "email": "qadim94@example.net",
        "customer_name": "Upma Dash"
    }

    cols = ["customer_id", "customer_name", "email", "city", "account_balance", "created_at"]

    hash_a = compute_record_hash(row_a, cols)
    hash_b = compute_record_hash(row_b, cols)

    assert hash_a == hash_b
    assert len(hash_a) == 64  # SHA-256 hex string


@pytest.mark.unit
def test_hash_detects_value_changes():
    """Verify hash changes when any field value changes."""
    base_row = {
        "customer_id": 1,
        "customer_name": "Upma Dash",
        "email": "qadim94@example.net",
        "city": "Dehri",
        "account_balance": 476171.91,
        "created_at": datetime(2025, 7, 19, 5, 41, 2)
    }

    changed_row = base_row.copy()
    changed_row["account_balance"] = 476171.92  # 1 cent difference

    cols = list(base_row.keys())
    assert compute_record_hash(base_row, cols) != compute_record_hash(changed_row, cols)
