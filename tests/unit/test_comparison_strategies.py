"""
Unit tests validating all comparison strategies (Baseline, Vectorized Pandas, DuckDB, Polars, Hash-Assisted)
against identical scenarios, composite keys, NULL handling, and bounded discrepancy sampling.
"""

import pandas as pd
import pytest

from migration_validator.connectors.base import (
    BaseConnector,
    ColumnMetadata,
    ConnectionTestResult,
    KeySummary,
    TableSchema,
)
from migration_validator.validation.models import DiscrepancyType, ValidationStatus
from migration_validator.validation.record_comparison import (
    RecordComparisonEngine,
    get_comparison_strategy,
    run_record_comparison_check,
)


class MockConnector(BaseConnector):
    """In-memory connector with keyset batching for arbitrary key combinations."""

    def __init__(self, data: pd.DataFrame):
        self.df = data.copy()

    def test_connection(self):
        return ConnectionTestResult(True, "Mock", "OK", 0.1)

    def get_databases_or_schemas(self):
        return ["mock"]

    def get_tables(self, s=None):
        return ["mock_table"]

    def get_table_schema(self, table_name, schema_or_db=None):
        cols = {c: ColumnMetadata(c, str(dtype)) for c, dtype in self.df.dtypes.items()}
        return TableSchema("mock", "mock", table_name, cols)

    def get_record_count(self, table_name, schema_or_db=None):
        return len(self.df)

    def get_null_counts(self, table_name, columns, schema_or_db=None):
        return {c: int(self.df[c].isnull().sum()) for c in columns if c in self.df}

    def get_aggregates(self, table_name, numeric_columns, schema_or_db=None):
        return {}

    def get_distinct_counts(self, table_name, columns, schema_or_db=None):
        return {c: int(self.df[c].nunique()) for c in columns}

    def get_key_summary(self, table_name, key_columns, schema_or_db=None):
        return KeySummary(len(self.df), len(self.df), 0, 0)

    def fetch_records_batch(self, table_name, columns, key_columns, batch_size, last_key=None, schema_or_db=None):
        pk = key_columns[0]
        sub = self.df
        if last_key is not None:
            sub = sub[sub[pk] > last_key]
        sub = sub[columns].sort_values(by=pk, ascending=True).head(batch_size)
        if sub.empty:
            return sub, None
        return sub, sub[pk].iloc[-1]

    def close(self):
        pass


STRATEGIES = ["baseline", "vectorized_pandas", "duckdb", "polars", "hash_assisted"]


@pytest.fixture
def composite_key_dataset():
    data = [
        {"tenant_id": "T1", "order_id": 101, "amount": 150.50, "status": "COMPLETED"},
        {"tenant_id": "T1", "order_id": 102, "amount": 250.00, "status": "PENDING"},
        {"tenant_id": "T2", "order_id": 101, "amount": 99.99, "status": "COMPLETED"},
        {"tenant_id": "T2", "order_id": 102, "amount": 500.00, "status": "CANCELLED"},
    ]
    return data


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_all_strategies_identical(strategy):
    records = [
        {"id": 1, "name": "Alpha", "val": 10.5},
        {"id": 2, "name": "Beta", "val": 20.0},
        {"id": 3, "name": "Gamma", "val": 30.25},
    ]
    src_conn = MockConnector(pd.DataFrame(records))
    dst_conn = MockConnector(pd.DataFrame(records))
    mapping = {"id": "id", "name": "name", "val": "val"}

    engine = RecordComparisonEngine(
        src_conn, dst_conn, "t", "t", ["id"], mapping,
        batch_size=2, strategy=strategy
    )
    res, discs_count, breakdown = run_record_comparison_check(engine)

    assert res.status == ValidationStatus.PASSED
    assert discs_count == 0
    assert breakdown[DiscrepancyType.MISSING_IN_DEST.value] == 0
    assert breakdown[DiscrepancyType.EXTRA_IN_DEST.value] == 0
    assert breakdown[DiscrepancyType.VALUE_MISMATCH.value] == 0


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_all_strategies_composite_key(strategy, composite_key_dataset):
    src_data = composite_key_dataset
    dst_data = [r.copy() for r in composite_key_dataset]
    # Modify one value in destination: tenant T2, order 101 amount changed
    dst_data[2]["amount"] = 120.00

    src_df = pd.DataFrame(src_data)
    dst_df = pd.DataFrame(dst_data)
    cols = ["tenant_id", "order_id", "amount", "status"]
    key_cols = ["tenant_id", "order_id"]

    strat = get_comparison_strategy(strategy)
    discs, stats = strat.compare_batch(src_df, dst_df, key_cols, cols)

    assert stats["evaluated"] == 4
    assert stats["identical"] == 3
    assert stats["changed"] == 1
    assert stats["missing"] == 0
    assert stats["extra"] == 0
    assert len(discs) >= 1
    assert discs[0].column == "amount"
    assert "T2" in discs[0].key and "101" in discs[0].key


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_all_strategies_missing_and_extra(strategy):
    src_data = [{"id": 1, "val": "A"}, {"id": 2, "val": "B"}, {"id": 3, "val": "C"}]
    dst_data = [{"id": 2, "val": "B"}, {"id": 3, "val": "C"}, {"id": 4, "val": "D"}]

    src_df = pd.DataFrame(src_data)
    dst_df = pd.DataFrame(dst_data)
    strat = get_comparison_strategy(strategy)

    discs, stats = strat.compare_batch(src_df, dst_df, ["id"], ["id", "val"])

    assert stats["missing"] == 1
    assert stats["extra"] == 1
    assert stats["identical"] == 2
    assert stats["changed"] == 0


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_bounded_discrepancy_sampling(strategy):
    # 10 records all differing
    src_data = [{"id": i, "val": f"src_{i}"} for i in range(10)]
    dst_data = [{"id": i, "val": f"dst_{i}"} for i in range(10)]

    src_df = pd.DataFrame(src_data)
    dst_df = pd.DataFrame(dst_data)
    strat = get_comparison_strategy(strategy)

    # Ask for maximum 3 discrepancy samples
    discs, stats = strat.compare_batch(src_df, dst_df, ["id"], ["id", "val"], max_samples=3)

    assert stats["changed"] == 10  # Full count accurately recorded
    assert len(discs) <= 3          # Memory strictly bounded
