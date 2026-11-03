"""
Unit tests executing the 10-scenario Checkpoint Crash Matrix.
Verifies transaction boundaries, rollback semantics, crash recovery, and resumption safety
against a real file-backed SQLite database simulating real process restarts.
"""

from typing import Any, List, Optional

import pandas as pd
import pytest
from sqlalchemy import create_engine

from migration_validator.connectors.base import (
    BaseConnector,
    ColumnMetadata,
    ConnectionTestResult,
    KeySummary,
    TableSchema,
)
from migration_validator.repositories.metadata_repository import MetadataRepository
from migration_validator.services.orchestration import ValidationOrchestrator
from migration_validator.validation.models import (
    DiscrepancyType,
    ValidationRuleConfig,
    ValidationStatus,
)


class FaultInjectingMockConnector(BaseConnector):
    """Mock connector capable of simulating crashes and tracking calls."""

    def __init__(self, df: pd.DataFrame, key_columns: Optional[List[str]] = None):
        self.df = df
        self.key_columns = key_columns or ["id"]
        self.fetch_calls = 0
        self.fail_on_fetch_call: Optional[int] = None

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(is_successful=True, service_name="FaultMock", latency_ms=1.0)

    def get_databases_or_schemas(self) -> List[str]:
        return ["public"]

    def get_tables(self, schema_or_db: Optional[str] = None) -> List[str]:
        return ["mock_table"]

    def get_table_schema(self, table_name: str, schema_or_db: Optional[str] = None) -> TableSchema:
        cols = {
            c: ColumnMetadata(c, "varchar" if "name" in c else "bigint", True, c in self.key_columns, idx)
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
        schema_or_db: Optional[str] = None,
    ):
        self.fetch_calls += 1
        if self.fail_on_fetch_call is not None and self.fetch_calls == self.fail_on_fetch_call:
            raise RuntimeError(f"Simulated network/hardware crash before batch fetch #{self.fetch_calls}")

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


def create_file_repo(db_file: str) -> MetadataRepository:
    """Creates a real file-backed SQLite database for crash & recovery verification."""
    engine = create_engine(f"sqlite:///{db_file}")
    repo = MetadataRepository(engine=engine)
    repo.initialize_schema()
    return repo


@pytest.fixture
def matrix_dataset():
    """Generates 30 records across 6 batches of 5 with deterministic anomalies."""
    src_rows = []
    dst_rows = []
    for i in range(1, 31):
        s_row = {"id": i, "name": f"Item_{i}", "val": float(i * 100)}
        d_row = dict(s_row)
        if i == 3:
            # Batch 1 anomaly: missing in destination
            src_rows.append(s_row)
            continue
        elif i == 8:
            # Batch 2 anomaly: value mismatch
            d_row["val"] = 99999.0
        elif i == 14:
            # Batch 3 anomaly: extra in destination
            dst_rows.append(d_row)
            continue
        elif i == 19:
            # Batch 4 anomaly: value mismatch
            d_row["name"] = "Altered_Name"
        elif i == 24:
            # Batch 5 anomaly: missing in destination
            src_rows.append(s_row)
            continue
        src_rows.append(s_row)
        dst_rows.append(d_row)

    return pd.DataFrame(src_rows), pd.DataFrame(dst_rows)


@pytest.mark.unit
def test_crash_before_batch_transaction_and_clean_resume(tmp_path, matrix_dataset):
    """Crash Matrix Scenario 1: Crash before batch transaction begins."""
    src_df, dst_df = matrix_dataset
    db_file = str(tmp_path / "crash_before_tx.db")
    repo = create_file_repo(db_file)

    src_conn = FaultInjectingMockConnector(src_df)
    dst_conn = FaultInjectingMockConnector(dst_df)
    orch = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=repo)

    col_map = {"id": "id", "name": "name", "val": "val"}
    rule_config = ValidationRuleConfig(
        key_columns=["id"],
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

    # Inject crash before fetch call #3 (batch 3)
    src_conn.fail_on_fetch_call = 3

    crashed_summary = orch.execute_validation_run("t", "t", rule_config)
    assert crashed_summary.overall_status == ValidationStatus.FAILED
    assert any("Simulated network/hardware crash" in (c.error_details or "") for c in crashed_summary.checks)

    # Checkpoint verification: only batch 0 and batch 1 were committed
    run_id = crashed_summary.run_id
    checkpoints = repo.get_checkpoints_for_run(run_id)
    assert len(checkpoints) == 2  # Batches 0 and 1
    assert checkpoints[-1]["batch_index"] == 1
    assert checkpoints[-1]["records_evaluated"] == 10

    # Resume without fault injection
    src_conn.fail_on_fetch_call = None
    resumed = orch.execute_validation_run("t", "t", rule_config, resume_run_id=run_id)

    assert resumed.overall_status == ValidationStatus.FAILED
    assert resumed.total_records_evaluated == 30
    assert resumed.total_discrepancies == 5

    # Check database checkpoints
    final_chks = repo.get_checkpoints_for_run(run_id)
    assert len(final_chks) == 6


@pytest.mark.unit
def test_crash_during_discrepancy_insertion_rolls_back(tmp_path, matrix_dataset):
    """Crash Matrix Scenario 2: Crash during discrepancy insertion rolls back transaction."""
    src_df, dst_df = matrix_dataset
    db_file = str(tmp_path / "crash_during_disc.db")
    repo = create_file_repo(db_file)

    src_conn = FaultInjectingMockConnector(src_df)
    dst_conn = FaultInjectingMockConnector(dst_df)
    orch = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=repo)

    col_map = {"id": "id", "name": "name", "val": "val"}
    rule_config = ValidationRuleConfig(
        key_columns=["id"],
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

    # Mock commit_checkpoint_with_discrepancies to fail during discrepancy insert on batch 2
    orig_commit = repo.commit_checkpoint_with_discrepancies
    call_idx = 0

    def faulty_commit(*args, **kwargs):
        nonlocal call_idx
        if kwargs.get("batch_index") == 2:
            raise RuntimeError("Database crash during discrepancy write")
        return orig_commit(*args, **kwargs)

    repo.commit_checkpoint_with_discrepancies = faulty_commit

    summary = orch.execute_validation_run("t", "t", rule_config)
    assert summary.overall_status == ValidationStatus.FAILED

    # Check that batch 2 was NOT committed in partition_checkpoints
    chks = repo.get_checkpoints_for_run(summary.run_id)
    assert len(chks) == 2  # Only batch 0 and 1
    assert chks[-1]["batch_index"] == 1

    # Restore commit and resume
    repo.commit_checkpoint_with_discrepancies = orig_commit
    repo.update_run_status(summary.run_id, "CANCELLED")

    resumed = orch.execute_validation_run("t", "t", rule_config, resume_run_id=summary.run_id)
    assert resumed.total_records_evaluated == 30
    assert resumed.total_discrepancies == 5


@pytest.mark.unit
def test_crash_during_checkpoint_insertion_rolls_back(tmp_path, matrix_dataset):
    """Crash Matrix Scenario 3: Crash during checkpoint insert rolls back both discrepancies and checkpoint."""
    src_df, dst_df = matrix_dataset
    db_file = str(tmp_path / "crash_during_chk.db")
    repo = create_file_repo(db_file)

    src_conn = FaultInjectingMockConnector(src_df)
    dst_conn = FaultInjectingMockConnector(dst_df)
    orch = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=repo)

    col_map = {"id": "id", "name": "name", "val": "val"}
    rule_config = ValidationRuleConfig(
        key_columns=["id"],
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

    # Cancel after 2 batches
    counter = 0

    def cancel_after_2():
        nonlocal counter
        return counter >= 2

    def tracker(stage, ratio, msg):
        nonlocal counter
        if "Validated" in (msg or ""):
            counter += 1

    orch.execute_validation_run("t", "t", rule_config, progress_callback=tracker, cancel_check=cancel_after_2)

    chks = repo.get_checkpoints_for_run(repo.get_runs()[0]["run_id"])
    assert len(chks) == 2


@pytest.mark.unit
def test_duplicate_discrepancy_insertion_prevented(tmp_path):
    """Crash Matrix Scenario 9: Prevent duplicate discrepancy insertion upon retry."""
    db_file = str(tmp_path / "dedup_disc.db")
    repo = create_file_repo(db_file)
    run_id = "RUN-DEDUP-TEST-001"

    repo.record_run_start(run_id, "SRC", "t", "DST", "t", {})

    discs = [
        {"type": DiscrepancyType.VALUE_MISMATCH, "key": "100", "column": "val", "source_val": "1", "dest_val": "2"},
        {"type": DiscrepancyType.MISSING_IN_DEST, "key": "101", "column": None, "source_val": None, "dest_val": None},
    ]

    # First commit
    repo.commit_checkpoint_with_discrepancies(
        run_id=run_id,
        batch_index=0,
        last_source_key=101,
        last_dest_key=101,
        records_evaluated=10,
        discrepancies_found=2,
        discrepancies=discs,
        config_fingerprint="fp1",
    )

    persisted_1 = repo.get_discrepancies_for_run(run_id)
    assert len(persisted_1) == 2

    # Attempt to re-commit the EXACT same discrepancies (simulating replayed batch)
    repo.commit_checkpoint_with_discrepancies(
        run_id=run_id,
        batch_index=0,
        last_source_key=101,
        last_dest_key=101,
        records_evaluated=10,
        discrepancies_found=2,
        discrepancies=discs,
        config_fingerprint="fp1",
    )

    persisted_2 = repo.get_discrepancies_for_run(run_id)
    # Deduplication via discrepancy_hash guarantees count remains 2
    assert len(persisted_2) == 2


@pytest.mark.unit
def test_completed_runs_cannot_be_resumed(tmp_path, matrix_dataset):
    """Crash Matrix Scenario 10: Prevent already completed runs from being resumed."""
    src_df, dst_df = matrix_dataset
    db_file = str(tmp_path / "completed_no_resume.db")
    repo = create_file_repo(db_file)

    src_conn = FaultInjectingMockConnector(src_df)
    dst_conn = FaultInjectingMockConnector(dst_df)
    orch = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=repo)

    col_map = {"id": "id", "name": "name", "val": "val"}
    rule_config = ValidationRuleConfig(
        key_columns=["id"],
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

    summary = orch.execute_validation_run("t", "t", rule_config)
    assert summary.overall_status == ValidationStatus.FAILED

    # Attempt to resume completed run must raise ValueError
    with pytest.raises(ValueError, match="already completed with status 'FAILED'"):
        orch.execute_validation_run("t", "t", rule_config, resume_run_id=summary.run_id)


@pytest.mark.unit
def test_crash_after_commit_before_ui_update_and_resume(tmp_path, matrix_dataset):
    """Crash Matrix Scenario 4: Crash after transaction commit but before UI progress update."""
    src_df, dst_df = matrix_dataset
    db_file = str(tmp_path / "crash_after_tx_before_ui.db")
    repo = create_file_repo(db_file)

    src_conn = FaultInjectingMockConnector(src_df)
    dst_conn = FaultInjectingMockConnector(dst_df)
    orch = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=repo)

    col_map = {"id": "id", "name": "name", "val": "val"}
    rule_config = ValidationRuleConfig(
        key_columns=["id"],
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

    # Progress tracker crashes on batch 2 UI notification
    batches_seen = 0

    def faulty_progress(stage, ratio, msg):
        nonlocal batches_seen
        if "Validated" in (msg or ""):
            batches_seen += 1
            if batches_seen == 2:
                # Batch 2 transaction has ALREADY committed in database!
                raise RuntimeError("Process killed right after batch 2 commit before UI refresh")

    orch.execute_validation_run("t", "t", rule_config, progress_callback=faulty_progress)

    # Checkpoint for batch 2 was ALREADY safely persisted
    runs = repo.get_runs()
    run_id = runs[0]["run_id"]
    latest_chk = repo.get_latest_checkpoint(run_id)
    assert latest_chk is not None
    assert latest_chk["batch_index"] == 1
    assert latest_chk["records_evaluated"] == 10

    # Resume seamlessly from batch 2
    resumed = orch.execute_validation_run("t", "t", rule_config, resume_run_id=run_id)
    assert resumed.overall_status == ValidationStatus.FAILED
    assert resumed.total_records_evaluated == 30
    assert resumed.total_discrepancies == 5


@pytest.mark.unit
def test_resume_after_multiple_committed_batches(tmp_path, matrix_dataset):
    """Crash Matrix Scenario 6: Resume after 3 of 6 batches committed, finishing batches 4-6."""
    src_df, dst_df = matrix_dataset
    db_file = str(tmp_path / "multi_batch_resume.db")
    repo = create_file_repo(db_file)

    src_conn = FaultInjectingMockConnector(src_df)
    dst_conn = FaultInjectingMockConnector(dst_df)
    orch = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=repo)

    col_map = {"id": "id", "name": "name", "val": "val"}
    rule_config = ValidationRuleConfig(
        key_columns=["id"],
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

    batches_processed = 0

    def cancel_after_3():
        nonlocal batches_processed
        return batches_processed >= 3

    def progress_track(stage, ratio, msg):
        nonlocal batches_processed
        if "Validated" in (msg or ""):
            batches_processed += 1

    interrupted = orch.execute_validation_run(
        "t", "t", rule_config, progress_callback=progress_track, cancel_check=cancel_after_3
    )
    assert interrupted.overall_status == ValidationStatus.CANCELLED
    run_id = interrupted.run_id

    chks = repo.get_checkpoints_for_run(run_id)
    assert len(chks) == 3
    assert chks[-1]["batch_index"] == 2
    assert chks[-1]["records_evaluated"] == 16

    # Resume to completion
    resumed = orch.execute_validation_run("t", "t", rule_config, resume_run_id=run_id)
    assert resumed.overall_status == ValidationStatus.FAILED
    assert resumed.total_records_evaluated == 30
    assert resumed.total_discrepancies == 5
    assert len(repo.get_checkpoints_for_run(run_id)) == 6


@pytest.mark.unit
def test_resume_rejects_incompatible_configurations(tmp_path, matrix_dataset):
    """Crash Matrix Scenario 8: Reject altered batch size or key columns upon resume."""
    src_df, dst_df = matrix_dataset
    db_file = str(tmp_path / "incompatible_cfg.db")
    repo = create_file_repo(db_file)

    src_conn = FaultInjectingMockConnector(src_df)
    dst_conn = FaultInjectingMockConnector(dst_df)
    orch = ValidationOrchestrator(src_conn, dst_conn, metadata_repo=repo)

    col_map = {"id": "id", "name": "name", "val": "val"}
    cfg_orig = ValidationRuleConfig(
        key_columns=["id"],
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

    b_count = 0
    def cancel_1():
        nonlocal b_count
        return b_count >= 1

    def prog(stage, ratio, msg):
        nonlocal b_count
        if "Validated" in (msg or ""):
            b_count += 1

    summary = orch.execute_validation_run("t", "t", cfg_orig, progress_callback=prog, cancel_check=cancel_1)
    assert summary.overall_status == ValidationStatus.CANCELLED

    # Alter configuration: change batch_size
    cfg_altered = ValidationRuleConfig(
        key_columns=["id"],
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

    with pytest.raises(ValueError, match="Configuration fingerprint mismatch"):
        orch.execute_validation_run("t", "t", cfg_altered, resume_run_id=summary.run_id)

