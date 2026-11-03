"""
Comprehensive unit test suite for Data Type and Comparison Semantics.
Verifies all strategies against the compatibility matrix:
- Integer and BigInt (> 2^53 precision preservation)
- Decimal (arbitrary precision)
- Float
- Boolean
- String and Unicode (multilingual, emojis, special characters)
- Date
- Timestamp and Timestamp with Timezone
- NULL semantics (NULL vs empty string, NULL vs 0, NULL vs NULL)
- JSON (canonical dict formatting and unordered key equivalence)
- Binary data (bytes comparison and hex representation)
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from migration_validator.validation.models import DiscrepancyType
from migration_validator.validation.record_comparison import (
    BaselinePythonStrategy,
    DuckDBStrategy,
    HashAssistedStrategy,
    PolarsStrategy,
    VectorizedPandasStrategy,
)
from migration_validator.validation.serialization import canonical_value_str


@pytest.fixture(params=["duckdb", "pandas", "polars", "hash", "baseline"])
def comparison_strategy(request):
    """Parametrized fixture providing each comparison engine."""
    strat_name = request.param
    if strat_name == "duckdb":
        return DuckDBStrategy()
    elif strat_name == "pandas":
        return VectorizedPandasStrategy()
    elif strat_name == "polars":
        return PolarsStrategy()
    elif strat_name == "hash":
        return HashAssistedStrategy()
    elif strat_name == "baseline":
        return BaselinePythonStrategy()
    raise ValueError(f"Unknown strategy: {strat_name}")


@pytest.mark.unit
def test_bigint_precision_preservation(comparison_strategy):
    """Verify BigInt values exceeding 2^53 (9,007,199,254,740,992) do not suffer IEEE 754 float precision loss."""
    big_id_1 = 9_007_199_254_740_993
    big_id_2 = 9_007_199_254_740_995  # Differs by 2 at precision beyond float53

    # If converted to float64, both become 9007199254740992.0 or 9007199254740996.0 losing distinction
    src_df = pd.DataFrame([{"id": 1, "big_val": big_id_1}])
    dst_df_identical = pd.DataFrame([{"id": 1, "big_val": big_id_1}])
    dst_df_diff = pd.DataFrame([{"id": 1, "big_val": big_id_2}])

    # 1. Identical check
    discs, stats = comparison_strategy.compare_batch(src_df, dst_df_identical, ["id"], ["id", "big_val"])
    assert stats["identical"] == 1
    assert stats["changed"] == 0
    assert len(discs) == 0

    # 2. Distinct check
    discs, stats = comparison_strategy.compare_batch(src_df, dst_df_diff, ["id"], ["id", "big_val"])
    assert stats["changed"] == 1
    assert len(discs) == 1
    assert discs[0].type == DiscrepancyType.VALUE_MISMATCH
    assert discs[0].column == "big_val"
    assert str(big_id_1) in discs[0].source_val
    assert str(big_id_2) in discs[0].dest_val


@pytest.mark.unit
def test_integer_float_equivalence(comparison_strategy):
    """Verify numeric equivalence: 42 is equivalent to 42.0 across canonical serialization."""
    src_df = pd.DataFrame([{"id": 1, "num_val": 42}])
    dst_df = pd.DataFrame([{"id": 1, "num_val": 42.0}])

    discs, stats = comparison_strategy.compare_batch(src_df, dst_df, ["id"], ["id", "num_val"])
    assert stats["identical"] == 1
    assert stats["changed"] == 0
    assert len(discs) == 0


@pytest.mark.unit
def test_decimal_arbitrary_precision(comparison_strategy):
    """Verify Decimal values with high precision."""
    d1 = Decimal("123456789.1234")
    d2 = Decimal("123456789.9999")

    src_df = pd.DataFrame([{"id": 1, "dec_val": d1}])
    dst_df_identical = pd.DataFrame([{"id": 1, "dec_val": d1}])
    dst_df_diff = pd.DataFrame([{"id": 1, "dec_val": d2}])

    # Identical
    discs, stats = comparison_strategy.compare_batch(src_df, dst_df_identical, ["id"], ["id", "dec_val"])
    assert stats["identical"] == 1
    assert stats["changed"] == 0

    # Distinct
    discs, stats = comparison_strategy.compare_batch(src_df, dst_df_diff, ["id"], ["id", "dec_val"])
    assert stats["changed"] == 1
    assert len(discs) == 1
    assert discs[0].column == "dec_val"


@pytest.mark.unit
def test_boolean_semantics(comparison_strategy):
    """Verify Boolean values and distinction from integers 0 and 1."""
    src_df = pd.DataFrame([
        {"id": 1, "flag": True},
        {"id": 2, "flag": False},
    ])
    dst_df = pd.DataFrame([
        {"id": 1, "flag": True},
        {"id": 2, "flag": True},  # Discrepancy
    ])

    discs, stats = comparison_strategy.compare_batch(src_df, dst_df, ["id"], ["id", "flag"])
    assert stats["identical"] == 1
    assert stats["changed"] == 1
    assert len(discs) == 1
    assert discs[0].key == "2"
    assert discs[0].source_val == "false"
    assert discs[0].dest_val == "true"


@pytest.mark.unit
def test_unicode_and_multilingual_strings(comparison_strategy):
    """Verify UTF-8 unicode handling including Japanese, German umlauts, and emojis."""
    src_df = pd.DataFrame([
        {"id": 1, "text": "こんにちは世界"},
        {"id": 2, "text": "Übergrößenträger"},
        {"id": 3, "text": "🚀 Launching to Mars ✨"},
    ])
    dst_df = pd.DataFrame([
        {"id": 1, "text": "こんにちは世界"},
        {"id": 2, "text": "Uebergroessentraeger"},  # Discrepancy
        {"id": 3, "text": "🚀 Launching to Mars ✨"},
    ])

    discs, stats = comparison_strategy.compare_batch(src_df, dst_df, ["id"], ["id", "text"])
    assert stats["identical"] == 2
    assert stats["changed"] == 1
    assert len(discs) == 1
    assert discs[0].key == "2"
    assert discs[0].source_val == "Übergrößenträger"
    assert discs[0].dest_val == "Uebergroessentraeger"


@pytest.mark.unit
def test_date_and_timestamp_semantics(comparison_strategy):
    """Verify Date and Timestamp comparisons."""
    d = date(2026, 6, 15)
    t1 = datetime(2026, 6, 15, 10, 30, 0)
    t2 = datetime(2026, 6, 15, 11, 30, 0)

    src_df = pd.DataFrame([{"id": 1, "d_col": d, "ts_col": t1}])
    dst_df = pd.DataFrame([{"id": 1, "d_col": d, "ts_col": t2}])

    discs, stats = comparison_strategy.compare_batch(src_df, dst_df, ["id"], ["id", "d_col", "ts_col"])
    assert stats["identical"] == 0
    assert stats["changed"] == 1
    assert len(discs) == 1
    assert discs[0].column == "ts_col"


@pytest.mark.unit
def test_timestamp_timezone_normalization():
    """Verify timezone offset normalization: UTC vs +02:00 representing the exact same instant."""
    t_utc = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    tz_plus2 = timezone(timedelta(hours=2))
    t_plus2 = datetime(2026, 1, 1, 14, 0, 0, tzinfo=tz_plus2)

    # Canonical string representations must match because they represent the exact same moment in time
    c_utc = canonical_value_str(t_utc)
    c_plus2 = canonical_value_str(t_plus2)
    assert c_utc == c_plus2 == "2026-01-01 12:00:00"


@pytest.mark.unit
def test_null_semantics(comparison_strategy):
    """
    Verify NULL semantics:
    - NULL == NULL -> Match
    - NULL != empty string ("") -> NULL_MISMATCH
    - NULL != 0 -> NULL_MISMATCH
    """
    src_df = pd.DataFrame([
        {"id": 1, "val": None},
        {"id": 2, "val": None},
        {"id": 3, "val": None},
    ])
    dst_df = pd.DataFrame([
        {"id": 1, "val": None},   # Match
        {"id": 2, "val": ""},     # NULL vs empty string -> Mismatch
        {"id": 3, "val": 0},      # NULL vs 0 -> Mismatch
    ])

    discs, stats = comparison_strategy.compare_batch(src_df, dst_df, ["id"], ["id", "val"])
    assert stats["identical"] == 1
    assert stats["changed"] == 2
    assert stats["null_diffs"] == 2

    null_discs = [d for d in discs if d.type == DiscrepancyType.NULL_MISMATCH]
    assert len(null_discs) == 2
    keys = {d.key for d in null_discs}
    assert keys == {"2", "3"}


@pytest.mark.unit
def test_json_unordered_key_equivalence():
    """Verify JSON canonical serialization treats unordered keys as identical."""
    json_str_1 = '{"status": "OK", "code": 200, "meta": {"owner": "admin", "retry": false}}'
    json_str_2 = '{"meta": {"retry": false, "owner": "admin"}, "code": 200, "status": "OK"}'

    canon_1 = canonical_value_str(json_str_1)
    canon_2 = canonical_value_str(json_str_2)
    assert canon_1 == canon_2

    # Also test Python dict object
    dict_val = {"code": 200, "status": "OK", "meta": {"retry": False, "owner": "admin"}}
    canon_dict = canonical_value_str(dict_val)
    assert canon_dict == canon_1


@pytest.mark.unit
def test_binary_data_hex_serialization():
    """Verify bytes / binary data is canonicalized to deterministic hexadecimal strings."""
    b_data = bytes([0x00, 0x7F, 0xFF, 0x42])
    canon = canonical_value_str(b_data)
    assert canon == "007fff42"
