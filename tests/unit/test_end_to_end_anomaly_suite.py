"""
Phase 6 End-to-End Validation Suite:
Tests uninterrupted execution vs. crash-interrupted and resumed execution across a multi-anomaly dataset containing:
- Missing destination records
- Extra destination records
- Modified scalar values
- NULL value differences
- Arbitrary Decimal differences
- Timestamp and timezone differences
- Duplicate business keys

Verifies that the resumed run produces 100% identical discrepancies, counts, and status to the uninterrupted run.
"""

from datetime import datetime, timezone
from decimal import Decimal

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
    ValidationRuleConfig,
    ValidationStatus,
)


class AnomalyTestConnector(BaseConnector):
    """In-memory keyset paginating connector for end-to-end anomaly verification."""

    def __init__(self, df: pd.DataFrame, key_columns: list):
        self.df = df.copy()
        self.key_columns = key_columns

    def test_connection(self):
        return ConnectionTestResult(True, "AnomalyMock", "OK", 0.01)

    def get_databases_or_schemas(self):
        return ["public"]

    def get_tables(self, s=None):
        return ["e2e_table"]

    def get_table_schema(self, table_name, schema_or_db=None):
        cols = {c: ColumnMetadata(c, "varchar", True, c in self.key_columns) for c in self.df.columns}
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
        return KeySummary(len(self.df), int(self.df[pk].nunique()), int(self.df[pk].isnull().sum()), 0)

    def fetch_records_batch(self, table_name, columns, key_columns, batch_size, last_key=None, schema_or_db=None):
        pk = key_columns[0]
        sub = self.df
        if last_key is not None:
            sub = sub[sub[pk] > last_key]
        sub = sub.sort_values(by=pk, ascending=True).head(batch_size)
        if sub.empty:
            return sub[columns], None
        return sub[columns], sub[pk].iloc[-1]

    def close(self):
        pass


@pytest.fixture
def complex_anomaly_dataset():
    """Builds a dataset containing all 7 anomaly types."""
    src_rows = []
    dst_rows = []

    t_base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    for i in range(1, 51):
        s_row = {
            "id": i,
            "name": f"User_{i}",
            "amount": Decimal(f"{i * 10}.50"),
            "status": "ACTIVE",
            "ts": t_base,
            "notes": f"Note_{i}",
        }
        d_row = dict(s_row)

        if i == 5:
            # 1. Missing in destination
            src_rows.append(s_row)
            continue
        elif i == 12:
            # 2. Value mismatch
            d_row["name"] = "User_12_MODIFIED"
        elif i == 18:
            # 3. NULL difference
            d_row["notes"] = None
        elif i == 24:
            # 4. Decimal difference
            d_row["amount"] = Decimal(f"{i * 10}.99")
        elif i == 30:
            # 5. Timestamp difference
            d_row["ts"] = datetime(2026, 1, 1, 18, 0, 0, tzinfo=timezone.utc)
        elif i == 37:
            # 6. Extra in destination
            dst_rows.append(d_row)
            continue
        elif i == 45:
            # 7. Duplicate key in destination
            src_rows.append(s_row)
            dst_rows.append(d_row)
            dup_d = dict(d_row)
            dup_d["name"] = "User_45_DUPLICATE"
            dst_rows.append(dup_d)
            continue

        src_rows.append(s_row)
        dst_rows.append(d_row)

    return pd.DataFrame(src_rows), pd.DataFrame(dst_rows)


@pytest.mark.unit
def test_uninterrupted_vs_resumed_exact_parity(tmp_path, complex_anomaly_dataset):
    """
    Execute full uninterrupted validation run on the complex dataset,
    then execute an interrupted run, restart process, resume, and verify 100% exact parity.
    """
    src_df, dst_df = complex_anomaly_dataset
    cols = ["id", "name", "amount", "status", "ts", "notes"]
    col_map = {c: c for c in cols}

    # ----------------------------------------------------
    # RUN 1: Uninterrupted Execution
    # ----------------------------------------------------
    db_clean = tmp_path / "clean_meta.db"
    eng_clean = create_engine(f"sqlite:///{db_clean}")
    repo_clean = MetadataRepository(engine=eng_clean)
    repo_clean.initialize_schema()

    src_conn_1 = AnomalyTestConnector(src_df, ["id"])
    dst_conn_1 = AnomalyTestConnector(dst_df, ["id"])
    orch_clean = ValidationOrchestrator(src_conn_1, dst_conn_1, metadata_repo=repo_clean)

    rule_cfg = ValidationRuleConfig(
        key_columns=["id"],
        column_mapping=col_map,
        batch_size=10,  # 50 records / 10 = 5 batches
        comparison_strategy="duckdb",
        run_schema_validation=False,
        run_record_count=False,
        run_key_integrity=False,
        run_null_checks=False,
        run_aggregates=False,
        run_distinct_counts=False,
        run_record_comparison=True,
    )

    clean_summary = orch_clean.execute_validation_run(
        source_table="e2e_table",
        dest_table="e2e_table",
        rule_config=rule_cfg,
    )
    clean_discs = repo_clean.get_discrepancies_for_run(run_id=clean_summary.run_id, limit=100)

    # ----------------------------------------------------
    # RUN 2: Interrupted Execution after Batch 2
    # ----------------------------------------------------
    db_resumed = tmp_path / "resumed_meta.db"
    eng_resumed_1 = create_engine(f"sqlite:///{db_resumed}")
    repo_resumed_1 = MetadataRepository(engine=eng_resumed_1)
    repo_resumed_1.initialize_schema()

    src_conn_2 = AnomalyTestConnector(src_df, ["id"])
    dst_conn_2 = AnomalyTestConnector(dst_df, ["id"])
    orch_resumed_1 = ValidationOrchestrator(src_conn_2, dst_conn_2, metadata_repo=repo_resumed_1)

    batch_counter = 0

    def cancel_after_2_batches():
        nonlocal batch_counter
        return batch_counter >= 2

    def progress_tracker(stage, ratio, msg):
        nonlocal batch_counter
        if "Validated" in (msg or ""):
            batch_counter += 1

    resumed_summary_1 = orch_resumed_1.execute_validation_run(
        source_table="e2e_table",
        dest_table="e2e_table",
        rule_config=rule_cfg,
        progress_callback=progress_tracker,
        cancel_check=cancel_after_2_batches,
    )
    assert resumed_summary_1.overall_status in (ValidationStatus.CANCELLED, ValidationStatus.ABORTED, ValidationStatus.FAILED)
    interrupted_run_id = resumed_summary_1.run_id

    # Simulate Process Death: dispose engine and tear down memory
    eng_resumed_1.dispose()
    del repo_resumed_1
    del orch_resumed_1
    del eng_resumed_1

    # ----------------------------------------------------
    # RUN 2 (Resumed): Restart process, connect to DB, resume interrupted_run_id
    # ----------------------------------------------------
    eng_resumed_2 = create_engine(f"sqlite:///{db_resumed}")
    repo_resumed_2 = MetadataRepository(engine=eng_resumed_2)

    src_conn_3 = AnomalyTestConnector(src_df, ["id"])
    dst_conn_3 = AnomalyTestConnector(dst_df, ["id"])
    orch_resumed_2 = ValidationOrchestrator(src_conn_3, dst_conn_3, metadata_repo=repo_resumed_2)

    final_resumed_summary = orch_resumed_2.execute_validation_run(
        source_table="e2e_table",
        dest_table="e2e_table",
        rule_config=rule_cfg,
        resume_run_id=interrupted_run_id,
    )
    resumed_discs = repo_resumed_2.get_discrepancies_for_run(run_id=interrupted_run_id, limit=100)

    # ----------------------------------------------------
    # EXACT PARITY ASSERTIONS: Clean vs. Resumed
    # ----------------------------------------------------
    assert final_resumed_summary.overall_status == clean_summary.overall_status == ValidationStatus.FAILED
    assert final_resumed_summary.total_records_evaluated == clean_summary.total_records_evaluated
    assert final_resumed_summary.total_discrepancies == clean_summary.total_discrepancies
    assert len(resumed_discs) == len(clean_discs)

    # Check that discrepancy identities and fields match identically
    clean_tuples = sorted([(d["record_key"], d["discrepancy_type"], d["column_name"]) for d in clean_discs])
    resumed_tuples = sorted([(d["record_key"], d["discrepancy_type"], d["column_name"]) for d in resumed_discs])
    assert clean_tuples == resumed_tuples
