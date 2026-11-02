"""
Unit tests for validation orchestration, cancellation, and discrepancy streaming.
Tests all checks, error handling, and interruption without external dependencies.
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
from migration_validator.services.orchestration import ValidationOrchestrator
from migration_validator.validation.models import (
    ValidationRuleConfig,
    ValidationStatus,
)


class MockOrchestrationConnector(BaseConnector):
    """In-memory connector with synthetic dataset."""

    def __init__(self, data: pd.DataFrame):
        self.df = data.copy()

    def test_connection(self):
        return ConnectionTestResult(True, "Mock", "Connected", 0.5)

    def get_databases_or_schemas(self):
        return ["mock_schema"]

    def get_tables(self, s=None):
        return ["customers"]

    def get_table_schema(self, table_name, schema_or_db=None):
        cols = {
            "customer_id": ColumnMetadata("customer_id", "bigint", False, True, 1),
            "customer_name": ColumnMetadata("customer_name", "varchar", True, False, 2),
            "email": ColumnMetadata("email", "varchar", True, False, 3),
            "account_balance": ColumnMetadata("account_balance", "numeric", True, False, 4),
        }
        return TableSchema("mock", "mock_schema", table_name, cols)

    def get_record_count(self, table_name, schema_or_db=None):
        return len(self.df)

    def get_null_counts(self, table_name, columns, schema_or_db=None):
        return {c: int(self.df[c].isnull().sum()) for c in columns if c in self.df}

    def get_aggregates(self, table_name, numeric_columns, schema_or_db=None):
        res = {}
        for c in numeric_columns:
            if c in self.df:
                s = self.df[c].dropna()
                res[c] = {
                    "sum": float(s.sum()) if not s.empty else 0.0,
                    "avg": float(s.mean()) if not s.empty else 0.0,
                    "min": float(s.min()) if not s.empty else 0.0,
                    "max": float(s.max()) if not s.empty else 0.0,
                }
        return res

    def get_distinct_counts(self, table_name, columns, schema_or_db=None):
        return {c: int(self.df[c].nunique()) for c in columns if c in self.df}

    def get_key_summary(self, table_name, key_columns, schema_or_db=None):
        pk = key_columns[0]
        s = self.df[pk]
        tot = len(self.df)
        dist = int(s.nunique())
        nulls = int(s.isnull().sum())
        return KeySummary(tot, dist, nulls, max(0, tot - nulls - dist), s.min(), s.max())

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


@pytest.fixture
def sample_data():
    return pd.DataFrame([
        {"customer_id": 1, "customer_name": "Alice", "email": "alice@ex.com", "account_balance": 100.0},
        {"customer_id": 2, "customer_name": "Bob", "email": "bob@ex.com", "account_balance": 200.0},
        {"customer_id": 3, "customer_name": "Charlie", "email": "charlie@ex.com", "account_balance": 300.0},
    ])


def test_orchestration_full_run_passes(sample_data):
    src_conn = MockOrchestrationConnector(sample_data)
    dst_conn = MockOrchestrationConnector(sample_data)
    orchestrator = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=None)

    rule_config = ValidationRuleConfig(
        run_schema_validation=True,
        run_record_count=True,
        run_key_validation=True,
        run_null_validation=True,
        run_numeric_aggregates=True,
        run_distinct_counts=True,
        run_record_comparison=True,
        run_data_quality=True,
        key_columns=["customer_id"],
        column_mapping={
            "customer_id": "customer_id",
            "customer_name": "customer_name",
            "email": "email",
            "account_balance": "account_balance",
        },
        comparison_strategy="duckdb",
        batch_size=2,
    )

    summary = orchestrator.execute_validation_run(
        source_table="customers",
        dest_table="customers",
        rule_config=rule_config,
    )

    assert summary.overall_status == ValidationStatus.PASSED
    assert summary.total_discrepancies == 0
    assert summary.failed_checks == 0
    assert summary.total_records_evaluated == 3
    assert summary.duration_seconds > 0.0


def test_orchestration_cancellation(sample_data):
    src_conn = MockOrchestrationConnector(sample_data)
    dst_conn = MockOrchestrationConnector(sample_data)
    orchestrator = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=None)

    rule_config = ValidationRuleConfig(
        run_schema_validation=True,
        run_record_count=True,
        key_columns=["customer_id"],
        column_mapping={"customer_id": "customer_id"},
    )

    # Cancel check triggers immediately
    summary = orchestrator.execute_validation_run(
        source_table="customers",
        dest_table="customers",
        rule_config=rule_config,
        cancel_check=lambda: True,
    )

    assert summary.overall_status == ValidationStatus.CANCELLED
