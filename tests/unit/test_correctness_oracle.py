"""
Comprehensive Correctness Oracle Test Suite.
Validates all 5 comparison strategies against independently verifiable ground-truth scenarios:
1. Identical datasets
2. Missing target rows
3. Extra target rows
4. Changed field values
5. Multiple changed columns in one row
6. NULL versus non-NULL (NULL_MISMATCH)
7. NULL versus NULL
8. Empty tables
9. Duplicate records & duplicate primary keys (DUPLICATE_KEY)
10. Composite primary keys
11. Decimal and float precision tolerance
12. Timestamp naive versus UTC timezone-aware
13. Unicode international strings
14. Different column ordering
15. Large strings
16. Unsorted source and target rows
17. Integer versus float numeric equivalence (10 == 10.0)
"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from migration_validator.validation.models import DiscrepancyType
from migration_validator.validation.record_comparison import (
    get_comparison_strategy,
)

STRATEGIES = ["baseline", "vectorized_pandas", "duckdb", "polars", "hash_assisted"]


@pytest.fixture(params=STRATEGIES)
def strategy(request):
    return get_comparison_strategy(request.param)


@pytest.mark.unit
def test_oracle_identical_datasets(strategy):
    src = pd.DataFrame({"id": [1, 2, 3], "val": ["A", "B", "C"], "num": [10.5, 20.0, 30.25]})
    dst = pd.DataFrame({"id": [1, 2, 3], "val": ["A", "B", "C"], "num": [10.5, 20.0, 30.25]})
    cols = ["id", "val", "num"]

    discs, stats = strategy.compare_batch(src, dst, ["id"], cols)

    assert stats["evaluated"] == 3
    assert stats["identical"] == 3
    assert stats["missing"] == 0
    assert stats["extra"] == 0
    assert stats["changed"] == 0
    assert stats["column_diffs"] == 0
    assert len(discs) == 0


@pytest.mark.unit
def test_oracle_missing_target_rows(strategy):
    src = pd.DataFrame({"id": [1, 2, 3], "val": ["A", "B", "C"]})
    dst = pd.DataFrame({"id": [1, 2], "val": ["A", "B"]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "val"])

    assert stats["evaluated"] == 3
    assert stats["identical"] == 2
    assert stats["missing"] == 1
    assert stats["extra"] == 0
    assert stats["changed"] == 0
    assert len(discs) == 1
    assert discs[0].type == DiscrepancyType.MISSING_IN_DEST
    assert discs[0].key == "3"


@pytest.mark.unit
def test_oracle_extra_target_rows(strategy):
    src = pd.DataFrame({"id": [1, 2], "val": ["A", "B"]})
    dst = pd.DataFrame({"id": [1, 2, 3], "val": ["A", "B", "C"]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "val"])

    assert stats["evaluated"] == 3
    assert stats["identical"] == 2
    assert stats["missing"] == 0
    assert stats["extra"] == 1
    assert stats["changed"] == 0
    assert len(discs) == 1
    assert discs[0].type == DiscrepancyType.EXTRA_IN_DEST
    assert discs[0].key == "3"


@pytest.mark.unit
def test_oracle_changed_field_values(strategy):
    src = pd.DataFrame({"id": [1, 2], "val": ["A", "B"]})
    dst = pd.DataFrame({"id": [1, 2], "val": ["A", "MUTATED"]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "val"])

    assert stats["evaluated"] == 2
    assert stats["identical"] == 1
    assert stats["changed"] == 1
    assert stats["column_diffs"] == 1
    assert len(discs) == 1
    assert discs[0].type == DiscrepancyType.VALUE_MISMATCH
    assert discs[0].key == "2"
    assert discs[0].column == "val"
    assert discs[0].source_val == "B"
    assert discs[0].dest_val == "MUTATED"


@pytest.mark.unit
def test_oracle_multiple_changed_columns_in_one_row(strategy):
    src = pd.DataFrame({"id": [1], "col_a": ["A"], "col_b": [100.0], "col_c": ["orig"]})
    dst = pd.DataFrame({"id": [1], "col_a": ["Z"], "col_b": [999.0], "col_c": ["orig"]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "col_a", "col_b", "col_c"])

    assert stats["evaluated"] == 1
    assert stats["identical"] == 0
    assert stats["changed"] == 1
    assert stats["column_diffs"] == 2
    diff_cols = {d.column for d in discs}
    assert diff_cols == {"col_a", "col_b"}


@pytest.mark.unit
def test_oracle_null_versus_non_null(strategy):
    src = pd.DataFrame({"id": [1, 2], "val": [None, "existing"]})
    dst = pd.DataFrame({"id": [1, 2], "val": ["now_populated", None]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "val"])

    assert stats["evaluated"] == 2
    assert stats["identical"] == 0
    assert stats["changed"] == 2
    assert stats["null_diffs"] == 2
    assert stats["column_diffs"] == 2
    for d in discs:
        assert d.type == DiscrepancyType.NULL_MISMATCH


@pytest.mark.unit
def test_oracle_null_versus_null(strategy):
    src = pd.DataFrame({"id": [1, 2], "val": [None, None]})
    dst = pd.DataFrame({"id": [1, 2], "val": [None, None]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "val"])

    assert stats["evaluated"] == 2
    assert stats["identical"] == 2
    assert stats["changed"] == 0
    assert stats["column_diffs"] == 0
    assert len(discs) == 0


@pytest.mark.unit
def test_oracle_empty_tables(strategy):
    src = pd.DataFrame({"id": [], "val": []})
    dst = pd.DataFrame({"id": [], "val": []})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "val"])

    assert stats["evaluated"] == 0
    assert stats["identical"] == 0
    assert stats["missing"] == 0
    assert stats["extra"] == 0
    assert stats["changed"] == 0
    assert len(discs) == 0


@pytest.mark.unit
def test_oracle_composite_primary_keys(strategy):
    src = pd.DataFrame({
        "tenant_id": ["T1", "T1", "T2"],
        "account_id": [101, 102, 101],
        "balance": [500.0, 750.0, 1000.0]
    })
    dst = pd.DataFrame({
        "tenant_id": ["T1", "T1", "T2"],
        "account_id": [101, 102, 101],
        "balance": [500.0, 750.0, 1200.0]
    })
    keys = ["tenant_id", "account_id"]
    cols = ["tenant_id", "account_id", "balance"]

    discs, stats = strategy.compare_batch(src, dst, keys, cols)

    assert stats["evaluated"] == 3
    assert stats["identical"] == 2
    assert stats["changed"] == 1
    assert stats["column_diffs"] == 1
    assert len(discs) == 1
    assert "T2" in discs[0].key and "101" in discs[0].key
    assert discs[0].column == "balance"


@pytest.mark.unit
def test_oracle_duplicate_keys(strategy):
    src = pd.DataFrame({"id": [1, 1, 2], "val": ["A", "DUP", "B"]})
    dst = pd.DataFrame({"id": [1, 2], "val": ["A", "B"]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "val"])

    assert stats["duplicate_keys"] == 2
    dup_discs = [d for d in discs if d.type == DiscrepancyType.DUPLICATE_KEY]
    assert len(dup_discs) > 0


@pytest.mark.unit
def test_oracle_decimal_and_float_precision(strategy):
    src = pd.DataFrame({"id": [1], "val": [10.500000001]})
    dst = pd.DataFrame({"id": [1], "val": [10.500000002]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "val"])

    assert stats["evaluated"] == 1
    assert stats["identical"] == 1
    assert stats["changed"] == 0
    assert stats["column_diffs"] == 0
    assert len(discs) == 0


@pytest.mark.unit
def test_oracle_timestamp_naive_versus_utc_aware(strategy):
    t_naive = datetime(2026, 6, 15, 14, 30, 0)
    t_aware = datetime(2026, 6, 15, 14, 30, 0, tzinfo=timezone.utc)

    src = pd.DataFrame({"id": [1], "ts": [t_naive]})
    dst = pd.DataFrame({"id": [1], "ts": [t_aware]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "ts"])

    assert stats["evaluated"] == 1
    assert stats["identical"] == 1
    assert stats["changed"] == 0
    assert len(discs) == 0


@pytest.mark.unit
def test_oracle_unicode_strings(strategy):
    src = pd.DataFrame({"id": [1], "name": ["José / 東京 / مرحبا / 🚀"]})
    dst = pd.DataFrame({"id": [1], "name": ["José / 東京 / مرحبا / 🚀"]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "name"])

    assert stats["evaluated"] == 1
    assert stats["identical"] == 1
    assert len(discs) == 0


@pytest.mark.unit
def test_oracle_different_column_ordering(strategy):
    src = pd.DataFrame({"id": [1], "col_a": ["A"], "col_b": ["B"]})
    dst = pd.DataFrame({"col_b": ["B"], "id": [1], "col_a": ["A"]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "col_a", "col_b"])

    assert stats["evaluated"] == 1
    assert stats["identical"] == 1
    assert stats["changed"] == 0
    assert len(discs) == 0


@pytest.mark.unit
def test_oracle_large_strings(strategy):
    large_text = "DataValidationMigration" * 2000
    src = pd.DataFrame({"id": [1], "payload": [large_text]})
    dst = pd.DataFrame({"id": [1], "payload": [large_text]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "payload"])

    assert stats["evaluated"] == 1
    assert stats["identical"] == 1
    assert len(discs) == 0


@pytest.mark.unit
def test_oracle_unsorted_source_and_target_rows(strategy):
    src = pd.DataFrame({"id": [3, 1, 2], "val": ["C", "A", "B"]})
    dst = pd.DataFrame({"id": [2, 3, 1], "val": ["B", "C", "A"]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "val"])

    assert stats["evaluated"] == 3
    assert stats["identical"] == 3
    assert stats["changed"] == 0
    assert len(discs) == 0


@pytest.mark.unit
def test_oracle_int_vs_float_equivalence(strategy):
    src = pd.DataFrame({"id": [1, 2], "val": [10, 20]})
    dst = pd.DataFrame({"id": [1, 2], "val": [10.0, 20.0]})

    discs, stats = strategy.compare_batch(src, dst, ["id"], ["id", "val"])

    assert stats["evaluated"] == 2
    assert stats["identical"] == 2
    assert stats["changed"] == 0
    assert len(discs) == 0
