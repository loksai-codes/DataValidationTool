"""
Unit tests with synthetic datasets covering all migration discrepancy scenarios:
1. Identical datasets
2. Missing rows in destination
3. Extra rows in destination
4. Changed values (column-level mismatches)
5. Duplicate keys
6. NULL value differences
"""

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pytest

from migration_validator.connectors.base import (
    BaseConnector,
    ColumnMetadata,
    ConnectionTestResult,
    KeySummary,
    TableSchema,
)
from migration_validator.validation.keys import validate_keys
from migration_validator.validation.models import DiscrepancyType, ValidationStatus
from migration_validator.validation.nulls import validate_null_counts
from migration_validator.validation.record_comparison import (
    RecordComparisonEngine,
    run_record_comparison_check,
)


class MockInMemoryConnector(BaseConnector):
    """In-memory mock connector serving synthetic pandas DataFrames."""

    def __init__(self, data: pd.DataFrame, service_name: str = "MockDB"):
        self.df = data.copy()
        self.service_name = service_name

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(is_successful=True, service_name=self.service_name, message="Mock OK", latency_ms=1.0)

    def get_databases_or_schemas(self) -> List[str]:
        return ["mock_db"]

    def get_tables(self, schema_or_db: Optional[str] = None) -> List[str]:
        return ["mock_table"]

    def get_table_schema(self, table_name: str, schema_or_db: Optional[str] = None) -> TableSchema:
        cols = {
            c: ColumnMetadata(c, str(dtype))
            for c, dtype in self.df.dtypes.items()
        }
        return TableSchema("mock_db", "mock_db", table_name, cols)

    def get_record_count(self, table_name: str, schema_or_db: Optional[str] = None) -> int:
        return len(self.df)

    def get_null_counts(self, table_name: str, columns: List[str], schema_or_db: Optional[str] = None) -> Dict[str, int]:
        return {col: int(self.df[col].isnull().sum()) for col in columns if col in self.df}

    def get_aggregates(self, table_name: str, numeric_columns: List[str], schema_or_db: Optional[str] = None) -> Dict[str, Dict[str, float]]:
        res = {}
        for c in numeric_columns:
            s = self.df[c].dropna()
            res[c] = {
                "sum": float(s.sum()) if not s.empty else 0.0,
                "avg": float(s.mean()) if not s.empty else 0.0,
                "min": float(s.min()) if not s.empty else 0.0,
                "max": float(s.max()) if not s.empty else 0.0
            }
        return res

    def get_distinct_counts(self, table_name: str, columns: List[str], schema_or_db: Optional[str] = None) -> Dict[str, int]:
        return {col: int(self.df[col].nunique(dropna=True)) for col in columns}

    def get_key_summary(self, table_name: str, key_columns: List[str], schema_or_db: Optional[str] = None) -> KeySummary:
        k = key_columns[0]
        series = self.df[k]
        tot = len(self.df)
        dist = int(series.nunique(dropna=True))
        nulls = int(series.isnull().sum())
        dups = max(0, tot - nulls - dist)
        return KeySummary(tot, dist, nulls, dups, series.min(), series.max())

    def fetch_records_batch(
        self,
        table_name: str,
        columns: List[str],
        key_columns: List[str],
        batch_size: int,
        last_key: Optional[Any] = None,
        schema_or_db: Optional[str] = None
    ) -> Tuple[pd.DataFrame, Optional[Any]]:
        pk = key_columns[0]
        filtered = self.df[self.df[pk] > last_key] if last_key is not None else self.df
        sub = filtered[columns].sort_values(by=pk, ascending=True).head(batch_size)
        if sub.empty:
            return sub, None
        return sub, sub[pk].iloc[-1]

    def close(self) -> None:
        pass


@pytest.fixture
def base_records():
    return [
        {"customer_id": 1, "customer_name": "Alice Smith", "email": "alice@example.com", "account_balance": 1000.50, "city": "Mumbai"},
        {"customer_id": 2, "customer_name": "Bob Jones", "email": "bob@example.com", "account_balance": 2500.00, "city": "Delhi"},
        {"customer_id": 3, "customer_name": "Charlie Brown", "email": "charlie@example.com", "account_balance": 310.25, "city": "Bengaluru"},
        {"customer_id": 4, "customer_name": "Diana Prince", "email": "diana@example.com", "account_balance": 4200.75, "city": "Chennai"},
        {"customer_id": 5, "customer_name": "Evan Wright", "email": "evan@example.com", "account_balance": 500.00, "city": "Pune"},
    ]


@pytest.mark.unit
def test_scenario_identical_datasets(base_records):
    """Case 1: Both source and destination have identical records -> PASSED."""
    src_df = pd.DataFrame(base_records)
    dst_df = pd.DataFrame(base_records)

    src_conn = MockInMemoryConnector(src_df)
    dst_conn = MockInMemoryConnector(dst_df)

    mapping = {c: c for c in src_df.columns}
    engine = RecordComparisonEngine(src_conn, dst_conn, "t", "t", ["customer_id"], mapping, batch_size=2)

    res, discs_count, breakdown = run_record_comparison_check(engine)

    assert res.status == ValidationStatus.PASSED
    assert discs_count == 0
    assert breakdown.get(DiscrepancyType.MISSING_IN_DEST.value) == 0
    assert breakdown.get(DiscrepancyType.EXTRA_IN_DEST.value) == 0
    assert breakdown.get(DiscrepancyType.VALUE_MISMATCH.value) == 0


@pytest.mark.unit
def test_scenario_missing_destination_rows(base_records):
    """Case 2: Source has customer_id 5, but destination is missing it -> MISSING_IN_DEST."""
    src_df = pd.DataFrame(base_records)
    dst_df = pd.DataFrame(base_records[:4])  # Missing id 5

    src_conn = MockInMemoryConnector(src_df)
    dst_conn = MockInMemoryConnector(dst_df)

    mapping = {c: c for c in src_df.columns}
    engine = RecordComparisonEngine(src_conn, dst_conn, "t", "t", ["customer_id"], mapping, batch_size=2)

    recorded_discs = []
    res, discs_count, breakdown = run_record_comparison_check(
        engine,
        discrepancy_sink=lambda batch: recorded_discs.extend(batch)
    )

    assert res.status == ValidationStatus.FAILED
    assert discs_count == 1
    assert breakdown[DiscrepancyType.MISSING_IN_DEST.value] == 1
    assert recorded_discs[0].type == DiscrepancyType.MISSING_IN_DEST
    assert recorded_discs[0].key == "5"


@pytest.mark.unit
def test_scenario_extra_destination_rows(base_records):
    """Case 3: Destination contains an unexpected record not in source -> EXTRA_IN_DEST."""
    src_df = pd.DataFrame(base_records[:4])
    dst_df = pd.DataFrame(base_records)  # Extra id 5 in destination

    src_conn = MockInMemoryConnector(src_df)
    dst_conn = MockInMemoryConnector(dst_df)

    mapping = {c: c for c in src_df.columns}
    engine = RecordComparisonEngine(src_conn, dst_conn, "t", "t", ["customer_id"], mapping, batch_size=2)

    recorded_discs = []
    res, discs_count, breakdown = run_record_comparison_check(
        engine,
        discrepancy_sink=lambda batch: recorded_discs.extend(batch)
    )

    assert res.status == ValidationStatus.FAILED
    assert discs_count == 1
    assert breakdown[DiscrepancyType.EXTRA_IN_DEST.value] == 1
    assert recorded_discs[0].type == DiscrepancyType.EXTRA_IN_DEST
    assert recorded_discs[0].key == "5"


@pytest.mark.unit
def test_scenario_changed_values_mismatch(base_records):
    """Case 4: Record exists in both, but balance or email was corrupted -> VALUE_MISMATCH."""
    src_df = pd.DataFrame(base_records)
    dst_records = [r.copy() for r in base_records]
    dst_records[1]["account_balance"] = 9999.99  # Bob's balance corrupted
    dst_records[3]["city"] = "Kolkata"           # Diana's city changed
    dst_df = pd.DataFrame(dst_records)

    src_conn = MockInMemoryConnector(src_df)
    dst_conn = MockInMemoryConnector(dst_df)

    mapping = {c: c for c in src_df.columns}
    engine = RecordComparisonEngine(src_conn, dst_conn, "t", "t", ["customer_id"], mapping, batch_size=2)

    recorded_discs = []
    res, discs_count, breakdown = run_record_comparison_check(
        engine,
        discrepancy_sink=lambda batch: recorded_discs.extend(batch)
    )

    assert res.status == ValidationStatus.FAILED
    assert discs_count == 2
    assert breakdown[DiscrepancyType.VALUE_MISMATCH.value] == 2

    keys_mismatched = {d.key: d.column for d in recorded_discs}
    assert keys_mismatched["2"] == "account_balance"
    assert keys_mismatched["4"] == "city"


@pytest.mark.unit
def test_scenario_duplicate_keys(base_records):
    """Case 5: Destination has duplicate primary keys -> DUPLICATE_KEY."""
    dst_records = base_records.copy()
    dst_records.append(base_records[0].copy())  # Duplicate customer_id 1

    src_conn = MockInMemoryConnector(pd.DataFrame(base_records))
    dst_conn = MockInMemoryConnector(pd.DataFrame(dst_records))

    chks = validate_keys(src_conn, dst_conn, "t", "t", ["customer_id"])
    dup_chk = next(c for c in chks if "Uniqueness" in c.check_name)

    assert dup_chk.status == ValidationStatus.FAILED
    assert "Duplicate keys detected" in dup_chk.error_details


@pytest.mark.unit
def test_scenario_null_differences(base_records):
    """Case 6: Destination has unexpected NULLs in a column -> FAILED null parity."""
    src_df = pd.DataFrame(base_records)
    dst_records = [r.copy() for r in base_records]
    dst_records[0]["email"] = None  # NULL in destination
    dst_df = pd.DataFrame(dst_records)

    src_conn = MockInMemoryConnector(src_df)
    dst_conn = MockInMemoryConnector(dst_df)

    mapping = {"email": "email", "city": "city"}
    chks = validate_null_counts(src_conn, dst_conn, "t", "t", mapping)

    email_chk = next(c for c in chks if "email" in c.check_name)
    assert email_chk.status == ValidationStatus.FAILED
    assert email_chk.source_value == "0 NULLs"
    assert email_chk.dest_value == "1 NULLs"
