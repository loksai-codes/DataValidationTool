"""
Advanced unit test suite for Composite Keys and Keyset Streaming Correctness.
Verifies ordering, lexicographical semantics, type alignment, NULL key rejection,
and exact discrepancy identities across complex key configurations.
"""

from datetime import datetime, timezone
from typing import Any, List, Optional

import pandas as pd
import pytest

from migration_validator.connectors.base import (
    BaseConnector,
    ColumnMetadata,
    ConnectionTestResult,
    KeySummary,
    TableSchema,
)
from migration_validator.validation.models import DiscrepancyType
from migration_validator.validation.record_comparison import RecordComparisonEngine


class AdvancedMockConnector(BaseConnector):
    """Configurable mock connector for composite key and streaming tests."""

    def __init__(self, df: pd.DataFrame, key_columns: List[str], chunk_size: Optional[int] = None):
        self.df = df
        self.key_columns = key_columns
        self.chunk_size = chunk_size  # If set, overrides batch_size to test unequal batch sizes

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(is_successful=True, service_name="AdvancedMock", latency_ms=1.0)

    def get_databases_or_schemas(self) -> List[str]:
        return ["public"]

    def get_tables(self, schema_or_db: Optional[str] = None) -> List[str]:
        return ["t"]

    def get_table_schema(self, table_name: str, schema_or_db: Optional[str] = None) -> TableSchema:
        cols = {
            c: ColumnMetadata(c, "varchar" if "str" in c or "id" in c else "bigint", True, c in self.key_columns, idx)
            for idx, c in enumerate(self.df.columns, start=1)
        }
        return TableSchema("mock", "public", table_name, cols)

    def get_record_count(self, table_name, schema_or_db=None):
        return len(self.df)

    def get_null_counts(self, table_name, columns, schema_or_db=None):
        return {c: int(self.df[c].isnull().sum()) for c in columns if c in self.df}

    def get_aggregates(self, table_name, numeric_columns, schema_or_db=None):
        return {}

    def get_distinct_counts(self, table_name, columns, schema_or_db=None):
        return {c: int(self.df[c].nunique()) for c in columns if c in self.df}

    def get_key_summary(self, table_name, key_columns, schema_or_db=None):
        tot = len(self.df)
        dist = int(self.df.drop_duplicates(subset=key_columns).shape[0])
        nulls = int(self.df[key_columns].isnull().any(axis=1).sum())
        return KeySummary(tot, dist, nulls, max(0, tot - nulls - dist), "min", "max")

    def fetch_records_batch(
        self,
        table_name: str,
        columns: List[str],
        key_columns: List[str],
        batch_size: int,
        last_key: Optional[Any] = None,
        schema_or_db: Optional[str] = None,
    ):
        eff_size = self.chunk_size if self.chunk_size is not None else batch_size
        sub = self.df
        avail_cols = [c for c in columns if c in sub.columns]
        if not avail_cols:
            avail_cols = list(sub.columns)

        if last_key is not None:
            if len(key_columns) > 1:
                sub_keys = sub[key_columns].apply(tuple, axis=1)
                sub = sub[sub_keys > last_key]
            else:
                pk = key_columns[0]
                sub = sub[sub[pk] > last_key]

        if len(key_columns) > 1:
            sub = sub.sort_values(by=key_columns, ascending=True).head(eff_size)
            if sub.empty:
                return sub[avail_cols], None
            next_cursor = tuple(sub[key_columns].iloc[-1])
            return sub[avail_cols], next_cursor
        else:
            pk = key_columns[0]
            sub = sub.sort_values(by=pk, ascending=True).head(eff_size)
            if sub.empty:
                return sub[avail_cols], None
            next_cursor = sub[pk].iloc[-1]
            return sub[avail_cols], next_cursor

    def close(self):
        pass


@pytest.mark.unit
def test_two_column_composite_key_exact_identities():
    """Verify 2-column composite key (tenant_id, account_id) with repeated first-column values."""
    # Repeated first column: tenant_id='T1' repeated 5 times, 'T2' repeated 5 times
    src_rows = [
        {"tenant_id": "T1", "account_id": 1, "balance": 100.0},
        {"tenant_id": "T1", "account_id": 2, "balance": 200.0},
        {"tenant_id": "T1", "account_id": 3, "balance": 300.0},  # Missing in dest
        {"tenant_id": "T1", "account_id": 4, "balance": 400.0},
        {"tenant_id": "T1", "account_id": 5, "balance": 500.0},
        {"tenant_id": "T2", "account_id": 1, "balance": 600.0},
        {"tenant_id": "T2", "account_id": 2, "balance": 700.0},  # Value mismatch in dest
        {"tenant_id": "T2", "account_id": 3, "balance": 800.0},
    ]
    dst_rows = [
        {"tenant_id": "T1", "account_id": 1, "balance": 100.0},
        {"tenant_id": "T1", "account_id": 2, "balance": 200.0},
        # (T1, 3) missing
        {"tenant_id": "T1", "account_id": 4, "balance": 400.0},
        {"tenant_id": "T1", "account_id": 5, "balance": 500.0},
        {"tenant_id": "T2", "account_id": 1, "balance": 600.0},
        {"tenant_id": "T2", "account_id": 2, "balance": 999.0},  # Mismatch
        {"tenant_id": "T2", "account_id": 3, "balance": 800.0},
        {"tenant_id": "T2", "account_id": 4, "balance": 900.0},  # Extra in dest
    ]

    key_cols = ["tenant_id", "account_id"]
    cols = ["tenant_id", "account_id", "balance"]
    col_map = {c: c for c in cols}

    src_conn = AdvancedMockConnector(pd.DataFrame(src_rows), key_cols)
    dst_conn = AdvancedMockConnector(pd.DataFrame(dst_rows), key_cols)

    engine = RecordComparisonEngine(
        source_connector=src_conn,
        dest_connector=dst_conn,
        source_table="accounts",
        dest_table="accounts",
        key_columns=key_cols,
        column_mapping=col_map,
        batch_size=3,
        strategy="duckdb",
    )

    collected_discs = []
    tot_eval = 0
    for b_discs, stats in engine.stream_compare():
        collected_discs.extend(b_discs)
        tot_eval += stats["evaluated"]

    assert tot_eval == 9
    assert len(collected_discs) == 3

    # Exact discrepancy identity checks
    missing = [d for d in collected_discs if d.type == DiscrepancyType.MISSING_IN_DEST]
    assert len(missing) == 1
    assert missing[0].key == "(tenant_id=T1, account_id=3)"

    mismatches = [d for d in collected_discs if d.type == DiscrepancyType.VALUE_MISMATCH]
    assert len(mismatches) == 1
    assert mismatches[0].key == "(tenant_id=T2, account_id=2)"
    assert mismatches[0].column == "balance"
    assert mismatches[0].source_val == "700"
    assert mismatches[0].dest_val == "999"

    extras = [d for d in collected_discs if d.type == DiscrepancyType.EXTRA_IN_DEST]
    assert len(extras) == 1
    assert extras[0].key == "(tenant_id=T2, account_id=4)"


@pytest.mark.unit
def test_three_column_composite_key_exact_identities():
    """Verify 3-column composite key (region, tenant_id, account_id) across batches."""
    src_rows = [
        {"region": "EU", "tenant_id": "T1", "account_id": 10, "status": "ACTIVE"},
        {"region": "EU", "tenant_id": "T1", "account_id": 20, "status": "PENDING"},
        {"region": "US", "tenant_id": "T1", "account_id": 10, "status": "ACTIVE"},
        {"region": "US", "tenant_id": "T2", "account_id": 30, "status": "SUSPENDED"},
    ]
    dst_rows = [
        {"region": "EU", "tenant_id": "T1", "account_id": 10, "status": "ACTIVE"},
        {"region": "EU", "tenant_id": "T1", "account_id": 20, "status": "ACTIVE"},  # Changed status
        {"region": "US", "tenant_id": "T1", "account_id": 10, "status": "ACTIVE"},
        {"region": "US", "tenant_id": "T2", "account_id": 30, "status": "SUSPENDED"},
    ]

    key_cols = ["region", "tenant_id", "account_id"]
    cols = ["region", "tenant_id", "account_id", "status"]
    col_map = {c: c for c in cols}

    src_conn = AdvancedMockConnector(pd.DataFrame(src_rows), key_cols)
    dst_conn = AdvancedMockConnector(pd.DataFrame(dst_rows), key_cols)

    engine = RecordComparisonEngine(
        source_connector=src_conn,
        dest_connector=dst_conn,
        source_table="accounts",
        dest_table="accounts",
        key_columns=key_cols,
        column_mapping=col_map,
        batch_size=2,
        strategy="duckdb",
    )

    collected_discs = []
    tot_eval = 0
    for b_discs, stats in engine.stream_compare():
        collected_discs.extend(b_discs)
        tot_eval += stats["evaluated"]

    assert tot_eval == 4
    assert len(collected_discs) == 1
    assert collected_discs[0].type == DiscrepancyType.VALUE_MISMATCH
    assert collected_discs[0].key == "(region=EU, tenant_id=T1, account_id=20)"
    assert collected_discs[0].column == "status"
    assert collected_discs[0].source_val == "PENDING"
    assert collected_discs[0].dest_val == "ACTIVE"


@pytest.mark.unit
def test_null_key_values_explicitly_rejected():
    """Verify that NULL values in key columns are rejected explicitly with ValueError."""
    rows_with_null_key = [
        {"tenant_id": "T1", "account_id": 1, "balance": 100.0},
        {"tenant_id": None, "account_id": 2, "balance": 200.0},  # Illegal NULL key
    ]
    key_cols = ["tenant_id", "account_id"]
    col_map = {c: c for c in ["tenant_id", "account_id", "balance"]}

    src_conn = AdvancedMockConnector(pd.DataFrame(rows_with_null_key), key_cols)
    dst_conn = AdvancedMockConnector(pd.DataFrame(rows_with_null_key), key_cols)

    engine = RecordComparisonEngine(
        source_connector=src_conn,
        dest_connector=dst_conn,
        source_table="t",
        dest_table="t",
        key_columns=key_cols,
        column_mapping=col_map,
        batch_size=5,
    )

    with pytest.raises(ValueError, match="key columns cannot contain NULL values"):
        for _ in engine.stream_compare():
            pass


@pytest.mark.unit
def test_timestamp_keys_with_timezone_and_precision():
    """Verify composite keys with Timestamp columns across batches."""
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 12, 0, 10, tzinfo=timezone.utc)

    src_rows = [
        {"device_id": "DEV-01", "ts": t0, "metric": 42.0},
        {"device_id": "DEV-01", "ts": t1, "metric": 43.0},
        {"device_id": "DEV-02", "ts": t2, "metric": 44.0},
    ]
    dst_rows = [
        {"device_id": "DEV-01", "ts": t0, "metric": 42.0},
        {"device_id": "DEV-01", "ts": t1, "metric": 99.0},  # Changed metric
        {"device_id": "DEV-02", "ts": t2, "metric": 44.0},
    ]

    key_cols = ["device_id", "ts"]
    col_map = {c: c for c in ["device_id", "ts", "metric"]}

    src_conn = AdvancedMockConnector(pd.DataFrame(src_rows), key_cols)
    dst_conn = AdvancedMockConnector(pd.DataFrame(dst_rows), key_cols)

    engine = RecordComparisonEngine(
        source_connector=src_conn,
        dest_connector=dst_conn,
        source_table="telemetry",
        dest_table="telemetry",
        key_columns=key_cols,
        column_mapping=col_map,
        batch_size=2,
        strategy="duckdb",
    )

    collected_discs = []
    tot_eval = 0
    for b_discs, stats in engine.stream_compare():
        collected_discs.extend(b_discs)
        tot_eval += stats["evaluated"]

    assert tot_eval == 3
    assert len(collected_discs) == 1
    assert collected_discs[0].type == DiscrepancyType.VALUE_MISMATCH
    assert "DEV-01" in collected_discs[0].key


@pytest.mark.unit
def test_unequal_batch_sizes_and_watermarks():
    """Verify two-pointer watermark synchronization when source batch size != target batch size."""
    # Source fetches in chunks of 3, Destination fetches in chunks of 7
    src_rows = [{"id": i, "val": i * 10} for i in range(1, 21)]
    dst_rows = [{"id": i, "val": i * 10} for i in range(1, 21)]
    # Anomaly at id=12
    dst_rows[11]["val"] = 9999

    col_map = {"id": "id", "val": "val"}
    src_conn = AdvancedMockConnector(pd.DataFrame(src_rows), ["id"], chunk_size=3)
    dst_conn = AdvancedMockConnector(pd.DataFrame(dst_rows), ["id"], chunk_size=7)

    engine = RecordComparisonEngine(
        source_connector=src_conn,
        dest_connector=dst_conn,
        source_table="t",
        dest_table="t",
        key_columns=["id"],
        column_mapping=col_map,
        batch_size=5,
        strategy="duckdb",
    )

    collected_discs = []
    tot_eval = 0
    for b_discs, stats in engine.stream_compare():
        collected_discs.extend(b_discs)
        tot_eval += stats["evaluated"]

    assert tot_eval == 20
    assert len(collected_discs) == 1
    assert collected_discs[0].key == "12"
    assert collected_discs[0].type == DiscrepancyType.VALUE_MISMATCH



@pytest.mark.unit
def test_mixed_ascending_descending_ordering_safely_rejected():
    """Verify that mixed sort directions (ASC/DESC) are actively rejected rather than corrupting watermark sync."""
    df = pd.DataFrame([{"tenant_id": "T1", "account_id": 1, "val": 10}])
    key_cols = ["tenant_id", "account_id"]
    col_map = {"tenant_id": "tenant_id", "account_id": "account_id", "val": "val"}
    conn = AdvancedMockConnector(df, key_cols)

    with pytest.raises(ValueError, match="uniform ascending"):
        RecordComparisonEngine(
            source_connector=conn,
            dest_connector=conn,
            source_table="t",
            dest_table="t",
            key_columns=key_cols,
            column_mapping=col_map,
            sort_directions={"tenant_id": "ASC", "account_id": "DESC"},
        )


@pytest.mark.unit
def test_missing_keys_at_batch_boundaries():
    """Verify missing keys that occur exactly at batch boundaries are not lost during cursor pagination."""
    # Batch size is 4. Anomaly at key (T1, 4) [end of batch 1] and (T2, 1) [start of batch 2]
    src_rows = [
        {"tenant_id": "T1", "account_id": 1, "balance": 100},
        {"tenant_id": "T1", "account_id": 2, "balance": 200},
        {"tenant_id": "T1", "account_id": 3, "balance": 300},
        {"tenant_id": "T1", "account_id": 4, "balance": 400},  # Missing in dest (boundary!)
        # (T2, 1) missing in src (boundary!)
        {"tenant_id": "T2", "account_id": 2, "balance": 600},
        {"tenant_id": "T2", "account_id": 3, "balance": 700},
    ]
    dst_rows = [
        {"tenant_id": "T1", "account_id": 1, "balance": 100},
        {"tenant_id": "T1", "account_id": 2, "balance": 200},
        {"tenant_id": "T1", "account_id": 3, "balance": 300},
        # (T1, 4) missing
        {"tenant_id": "T2", "account_id": 1, "balance": 500},  # Extra in dest (boundary!)
        {"tenant_id": "T2", "account_id": 2, "balance": 600},
        {"tenant_id": "T2", "account_id": 3, "balance": 700},
    ]

    key_cols = ["tenant_id", "account_id"]
    col_map = {c: c for c in ["tenant_id", "account_id", "balance"]}

    src_conn = AdvancedMockConnector(pd.DataFrame(src_rows), key_cols)
    dst_conn = AdvancedMockConnector(pd.DataFrame(dst_rows), key_cols)

    engine = RecordComparisonEngine(
        source_connector=src_conn,
        dest_connector=dst_conn,
        source_table="t",
        dest_table="t",
        key_columns=key_cols,
        column_mapping=col_map,
        batch_size=4,
        strategy="duckdb",
    )

    collected_discs = []
    tot_eval = 0
    for b_discs, stats in engine.stream_compare():
        collected_discs.extend(b_discs)
        tot_eval += stats["evaluated"]

    assert tot_eval == 7
    assert len(collected_discs) == 2

    missing = [d for d in collected_discs if d.type == DiscrepancyType.MISSING_IN_DEST]
    assert len(missing) == 1
    assert missing[0].key == "(tenant_id=T1, account_id=4)"

    extras = [d for d in collected_discs if d.type == DiscrepancyType.EXTRA_IN_DEST]
    assert len(extras) == 1
    assert extras[0].key == "(tenant_id=T2, account_id=1)"


@pytest.mark.unit
def test_decimal_composite_keys():
    """Verify composite keys containing Decimal columns."""
    from decimal import Decimal

    src_rows = [
        {"store_id": 1, "price": Decimal("19.99"), "qty": 10},
        {"store_id": 1, "price": Decimal("29.99"), "qty": 20},
        {"store_id": 2, "price": Decimal("19.99"), "qty": 30},
    ]
    dst_rows = [
        {"store_id": 1, "price": Decimal("19.99"), "qty": 10},
        {"store_id": 1, "price": Decimal("29.99"), "qty": 99},  # Changed qty
        {"store_id": 2, "price": Decimal("19.99"), "qty": 30},
    ]

    key_cols = ["store_id", "price"]
    col_map = {"store_id": "store_id", "price": "price", "qty": "qty"}

    src_conn = AdvancedMockConnector(pd.DataFrame(src_rows), key_cols)
    dst_conn = AdvancedMockConnector(pd.DataFrame(dst_rows), key_cols)

    engine = RecordComparisonEngine(
        source_connector=src_conn,
        dest_connector=dst_conn,
        source_table="t",
        dest_table="t",
        key_columns=key_cols,
        column_mapping=col_map,
        batch_size=2,
        strategy="duckdb",
    )

    collected_discs = []
    for b_discs, _ in engine.stream_compare():
        collected_discs.extend(b_discs)

    assert len(collected_discs) == 1
    assert collected_discs[0].type == DiscrepancyType.VALUE_MISMATCH
    assert "(store_id=1, price=29.99)" in collected_discs[0].key
