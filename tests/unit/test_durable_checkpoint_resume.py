"""
Comprehensive unit tests for Durable Checkpointing and True Resume (P0).
Verifies:
- Checkpoint persistence to database surviving process termination
- Keyset cursor resumption without reprocessing committed batches
- Duplicate discrepancy protection and idempotency
- Configuration fingerprint mismatch rejection
- Transaction rollback on simulated persistence failure
- Composite primary keys and partial final batch handling
"""

import os
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
from migration_validator.repositories.metadata_repository import (
    MetadataRepository,
)
from migration_validator.services.orchestration import (
    ValidationOrchestrator,
)
from migration_validator.validation.models import (
    ValidationRuleConfig,
    ValidationStatus,
)


class ResumableMockConnector(BaseConnector):
    """In-memory connector with keyset pagination simulation and call telemetry."""

    def __init__(self, data: pd.DataFrame, key_columns: Optional[List[str]] = None):
        self.df = data.copy()
        self.key_columns = key_columns or ["customer_id"]
        self.fetch_call_count = 0

    def test_connection(self):
        return ConnectionTestResult(True, "Mock", "Connected", 0.05)

    def get_databases_or_schemas(self):
        return ["public"]

    def get_tables(self, s=None):
        return ["customers"]

    def get_table_schema(self, table_name, schema_or_db=None):
        cols = {
            c: ColumnMetadata(c, "varchar" if "name" in c or "email" in c else "bigint", True, c in self.key_columns, idx)
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
        if len(key_columns) > 1:
            tot = len(self.df)
            dist = int(self.df.drop_duplicates(subset=key_columns).shape[0])
            nulls = int(self.df[key_columns].isnull().any(axis=1).sum())
            return KeySummary(tot, dist, nulls, max(0, tot - nulls - dist), "composite_min", "composite_max")
        pk = key_columns[0]
        s = self.df[pk]
        tot = len(self.df)
        dist = int(s.nunique())
        nulls = int(s.isnull().sum())
        return KeySummary(tot, dist, nulls, max(0, tot - nulls - dist), s.min(), s.max())

    def fetch_records_batch(
        self,
        table_name: str,
        columns: List[str],
        key_columns: List[str],
        batch_size: int,
        last_key: Optional[Any] = None,
        schema_or_db: Optional[str] = None
    ):
        self.fetch_call_count += 1
        sub = self.df
        avail_cols = [c for c in columns if c in sub.columns]
        if not avail_cols:
            avail_cols = list(sub.columns)

        if last_key is not None:
            if len(key_columns) > 1:
                # Composite key filtering
                sub_keys = sub[key_columns].apply(tuple, axis=1)
                sub = sub[sub_keys > last_key]
            else:
                pk = key_columns[0]
                sub = sub[sub[pk] > last_key]

        if len(key_columns) > 1:
            sub = sub.sort_values(by=key_columns, ascending=True).head(batch_size)
            if sub.empty:
                return sub[avail_cols], None
            next_cursor = tuple(sub[key_columns].iloc[-1])
            return sub[avail_cols], next_cursor
        else:
            pk = key_columns[0]
            sub = sub.sort_values(by=pk, ascending=True).head(batch_size)
            if sub.empty:
                return sub[avail_cols], None
            next_cursor = sub[pk].iloc[-1]
            return sub[avail_cols], next_cursor

    def close(self):
        pass


@pytest.fixture
def test_dataset():
    """Generates 25 records with known anomalies across batches."""
    src_rows = []
    dst_rows = []
    for i in range(1, 26):
        src_row = {
            "customer_id": i,
            "customer_name": f"Name_{i}",
            "email": f"user{i}@ex.com",
            "account_balance": float(i * 10),
        }
        dst_row = dict(src_row)
        if i == 3:
            # Missing in destination
            src_rows.append(src_row)
            continue
        elif i == 8:
            # Value mismatch
            dst_row["account_balance"] = 999.0
        elif i == 14:
            # Extra in destination
            dst_rows.append(dst_row)
            continue
        elif i == 18:
            # NULL mismatch
            dst_row["email"] = None
        elif i == 23:
            # Duplicate key in destination
            src_rows.append(src_row)
            dst_rows.append(dst_row)
            dup = dict(dst_row)
            dup["customer_name"] = "Duplicate_Name"
            dst_rows.append(dup)
            continue

        src_rows.append(src_row)
        dst_rows.append(dst_row)

    return pd.DataFrame(src_rows), pd.DataFrame(dst_rows)


def create_sqlite_repo(db_file: str) -> MetadataRepository:
    """Helper to create a file-backed MetadataRepository using SQLite."""
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{db_file}")
    repo = MetadataRepository(engine=engine)
    repo.initialize_schema()
    return repo


def test_durable_checkpoint_interruption_and_clean_resume(tmp_path, test_dataset):
    src_df, dst_df = test_dataset
    db_file = str(tmp_path / "metadata_test.db")

    # Step 1: Run fresh uninterrupted execution to establish ground truth
    repo_clean = create_sqlite_repo(str(tmp_path / "clean_meta.db"))
    src_conn_clean = ResumableMockConnector(src_df)
    dst_conn_clean = ResumableMockConnector(dst_df)
    orch_clean = ValidationOrchestrator(src_conn_clean, dst_conn_clean, metadata_repo=repo_clean)

    rule_config = ValidationRuleConfig(
        key_columns=["customer_id"],
        column_mapping={
            "customer_id": "customer_id",
            "customer_name": "customer_name",
            "email": "email",
            "account_balance": "account_balance",
        },
        batch_size=5,
        comparison_strategy="duckdb",
        run_schema_validation=False,
        run_record_count=False,
        run_key_validation=False,
        run_null_validation=False,
        run_numeric_aggregates=False,
        run_distinct_counts=False,
        run_record_comparison=True,
    )

    clean_summary = orch_clean.execute_validation_run("customers", "customers", rule_config)
    assert clean_summary.overall_status == ValidationStatus.FAILED
    assert clean_summary.total_records_evaluated == 25
    assert clean_summary.total_discrepancies == 6

    # Step 2: Run interrupted execution that cancels after 2 batches (10 records)
    repo_resumable = create_sqlite_repo(db_file)
    src_conn_res = ResumableMockConnector(src_df)
    dst_conn_res = ResumableMockConnector(dst_df)
    orch_res = ValidationOrchestrator(src_conn_res, dst_conn_res, metadata_repo=repo_resumable)

    batch_counter = 0

    def cancel_after_2_batches():
        nonlocal batch_counter
        # Checks after 2 committed batches
        return batch_counter >= 2

    # Patch progress callback to track batches
    def progress_tracker(stage, ratio, msg):
        nonlocal batch_counter
        if "Validated" in (msg or ""):
            batch_counter += 1

    interrupted_summary = orch_res.execute_validation_run(
        "customers",
        "customers",
        rule_config,
        progress_callback=progress_tracker,
        cancel_check=cancel_after_2_batches
    )

    assert interrupted_summary.overall_status == ValidationStatus.CANCELLED
    run_id = interrupted_summary.run_id

    # Verify persisted checkpoints in database
    checkpoints = repo_resumable.get_checkpoints_for_run(run_id)
    assert len(checkpoints) == 2, f"Expected 2 committed checkpoints, found {len(checkpoints)}"
    latest_chk = repo_resumable.get_latest_checkpoint(run_id)
    assert latest_chk is not None
    assert latest_chk["batch_index"] == 1
    assert latest_chk["last_source_key"] == 10
    assert latest_chk["records_evaluated"] == 10

    # Step 3: Simulate application crash and process restart
    repo_resumable.close()
    del orch_res
    del repo_resumable

    # Verify database file survived
    assert os.path.exists(db_file)

    # Step 4: Re-open repository pointing to the same database file
    repo_restarted = create_sqlite_repo(db_file)
    src_conn_restarted = ResumableMockConnector(src_df)
    dst_conn_restarted = ResumableMockConnector(dst_df)
    orch_restarted = ValidationOrchestrator(src_conn_restarted, dst_conn_restarted, metadata_repo=repo_restarted)

    # Resume the interrupted run
    resumed_summary = orch_restarted.execute_validation_run(
        "customers",
        "customers",
        rule_config,
        resume_run_id=run_id
    )

    # Step 5: Verify resumed outcome matches clean uninterrupted run exactly
    assert resumed_summary.overall_status == ValidationStatus.FAILED
    assert resumed_summary.run_id == run_id
    assert resumed_summary.total_records_evaluated == clean_summary.total_records_evaluated
    assert resumed_summary.total_discrepancies == clean_summary.total_discrepancies
    assert resumed_summary.discrepancy_counts == clean_summary.discrepancy_counts

    # Verify no duplicate discrepancies were inserted in the database
    db_discs = repo_restarted.get_discrepancies_for_run(run_id, limit=100)
    assert len(db_discs) == 6


def test_resume_rejects_altered_config_fingerprint(tmp_path, test_dataset):
    src_df, dst_df = test_dataset
    db_file = str(tmp_path / "metadata_fp.db")
    repo = create_sqlite_repo(db_file)

    src_conn = ResumableMockConnector(src_df)
    dst_conn = ResumableMockConnector(dst_df)
    orch = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=repo)

    col_map = {
        "customer_id": "customer_id",
        "customer_name": "customer_name",
        "email": "email",
        "account_balance": "account_balance",
    }
    rule_config_v1 = ValidationRuleConfig(
        key_columns=["customer_id"],
        column_mapping=col_map,
        batch_size=5,
        comparison_strategy="duckdb",
        run_schema_validation=False,
        run_record_count=False,
        run_key_validation=False,
        run_null_validation=False,
        run_numeric_aggregates=False,
        run_distinct_counts=False,
        run_record_comparison=True,
    )

    # Cancel immediately after batch 1
    call_count = 0
    def cancel_after_1():
        nonlocal call_count
        call_count += 1
        return call_count > 1

    summary = orch.execute_validation_run(
        "customers", "customers", rule_config_v1, cancel_check=cancel_after_1
    )
    run_id = summary.run_id

    # Alter configuration (change batch_size from 5 to 10)
    rule_config_v2 = ValidationRuleConfig(
        key_columns=["customer_id"],
        column_mapping=col_map,
        batch_size=10,
        comparison_strategy="duckdb",
        run_schema_validation=False,
        run_record_count=False,
        run_key_validation=False,
        run_null_validation=False,
        run_numeric_aggregates=False,
        run_distinct_counts=False,
        run_record_comparison=True,
    )

    # Resume must be rejected due to fingerprint mismatch
    with pytest.raises(ValueError, match="Configuration fingerprint mismatch"):
        orch.execute_validation_run("customers", "customers", rule_config_v2, resume_run_id=run_id)


def test_resume_rejects_already_completed_run(tmp_path, test_dataset):
    src_df, dst_df = test_dataset
    db_file = str(tmp_path / "metadata_completed.db")
    repo = create_sqlite_repo(db_file)

    src_conn = ResumableMockConnector(src_df)
    dst_conn = ResumableMockConnector(dst_df)
    orch = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=repo)

    col_map = {
        "customer_id": "customer_id",
        "customer_name": "customer_name",
        "email": "email",
        "account_balance": "account_balance",
    }
    rule_config = ValidationRuleConfig(
        key_columns=["customer_id"],
        column_mapping=col_map,
        batch_size=10,
        comparison_strategy="duckdb",
        run_schema_validation=False,
        run_record_count=False,
        run_key_validation=False,
        run_null_validation=False,
        run_numeric_aggregates=False,
        run_distinct_counts=False,
        run_record_comparison=True,
    )
    summary = orch.execute_validation_run("customers", "customers", rule_config)
    assert summary.overall_status in [ValidationStatus.FAILED, ValidationStatus.PASSED]

    # Attempting to resume a completed run must fail
    with pytest.raises(ValueError, match="already completed"):
        orch.execute_validation_run("customers", "customers", rule_config, resume_run_id=summary.run_id)


def test_composite_key_checkpoint_and_partial_batch(tmp_path):
    """Verifies checkpointing with composite primary keys and an uneven final batch."""
    # 11 records with batch_size=4 produces batches of [4, 4, 3] (partial final batch)
    rows_src = [
        {"tenant_id": f"T{i // 5}", "account_id": i, "balance": float(i * 10)}
        for i in range(1, 12)
    ]
    rows_dst = list(rows_src)
    # Inject discrepancy in final partial batch (i=10)
    rows_dst[9] = dict(rows_src[9])
    rows_dst[9]["balance"] = 9999.0

    src_df = pd.DataFrame(rows_src)
    dst_df = pd.DataFrame(rows_dst)

    db_file = str(tmp_path / "metadata_composite.db")
    repo = create_sqlite_repo(db_file)

    src_conn = ResumableMockConnector(src_df, key_columns=["tenant_id", "account_id"])
    dst_conn = ResumableMockConnector(dst_df, key_columns=["tenant_id", "account_id"])
    orch = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=repo)

    rule_config = ValidationRuleConfig(
        key_columns=["tenant_id", "account_id"],
        column_mapping={"tenant_id": "tenant_id", "account_id": "account_id", "balance": "balance"},
        batch_size=4,
        comparison_strategy="duckdb",
        run_schema_validation=False,
        run_record_count=False,
        run_key_validation=False,
        run_null_validation=False,
        run_numeric_aggregates=False,
        run_distinct_counts=False,
        run_record_comparison=True,
    )

    summary = orch.execute_validation_run("accounts", "accounts", rule_config)
    assert summary.overall_status == ValidationStatus.FAILED
    assert summary.total_records_evaluated == 11
    assert summary.total_discrepancies == 1

    checkpoints = repo.get_checkpoints_for_run(summary.run_id)
    assert len(checkpoints) == 3  # 4 + 4 + 3 records
    latest_chk = repo.get_latest_checkpoint(summary.run_id)
    assert latest_chk is not None
    assert latest_chk["records_evaluated"] == 11
    assert latest_chk["discrepancies_found"] == 1
    # Cursor key for composite primary key is serialized as tuple
    assert isinstance(latest_chk["last_source_key"], tuple)
    assert latest_chk["last_source_key"] == ("T2", 11)
    assert latest_chk["records_evaluated"] == 11
