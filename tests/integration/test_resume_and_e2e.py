"""Integration test for synthetic end-to-end validation, partial interruption, and durable resume."""
import json
import tempfile
from pathlib import Path
import pytest

from data_validator.config.models import (
    ComparisonConfig,
    ExecutionConfig,
    PartitionConfig,
    PartitionStrategy,
    ReportingConfig,
    TableEndpointConfig,
    ValidationJobConfig,
)
from data_validator.connectors.synthetic import SyntheticConnector
from data_validator.execution.coordinator import ValidationCoordinator
from data_validator.persistence.models import PartitionStatus, RunStatus
from data_validator.persistence.sqlite_repository import SQLiteRunRepository
from data_validator.security.secrets import SecretStore


@pytest.fixture
def env_setup():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "metadata.db"
        repo = SQLiteRunRepository(db_path)
        secret_store = SecretStore(connections_file=tmp_path / "connections.json")
        secret_store.set_memory_override("synthetic_src", {"provider": "other", "engine": "synthetic"})
        secret_store.set_memory_override("synthetic_tgt", {"provider": "other", "engine": "synthetic"})

        # Create source and target synthetic databases
        source_conn = SyntheticConnector("synthetic_src")
        target_conn = SyntheticConnector("synthetic_tgt")

        # Source data: records 1 to 30 except 22 (so 22 will be extra in target)
        src_data = [
            {"id": i, "name": f"User_{i}", "balance": float(i * 100), "category": "Retail"}
            for i in range(1, 31) if i != 22
        ]
        source_conn.seed_table("customers", src_data, primary_keys=["id"])

        # Target data:
        # Part 0 (1-15):
        # - ID 5 missing in target
        # - ID 10 changed balance (1000.0 -> 9999.0)
        # Part 1 (16-30):
        # - ID 20 missing in target
        # - ID 22 extra in target
        tgt_data = []
        for r in src_data:
            if r["id"] in (5, 20):
                continue  # missing in target
            elif r["id"] == 10:
                tgt_data.append({"id": 10, "name": "User_10", "balance": 9999.0, "category": "Retail"})
            else:
                tgt_data.append(dict(r))
        tgt_data.append({"id": 22, "name": "Extra_User_22", "balance": 500.0, "category": "Special"})
        target_conn.seed_table("customers", tgt_data, primary_keys=["id"])

        config = ValidationJobConfig(
            config_version=1,
            job_name="synthetic_customers_validation",
            source=TableEndpointConfig(
                connection_id="synthetic_src",
                database="default_db",
                table="customers",
            ),
            target=TableEndpointConfig(
                connection_id="synthetic_tgt",
                database="default_db",
                table="customers",
            ),
            comparison=ComparisonConfig(
                unique_keys=["id"],
                partition=PartitionConfig(
                    strategy=PartitionStrategy.KEY_RANGE,
                    column="id",
                    range_size=15,
                    include_null_partition=False,
                ),
            ),
            execution=ExecutionConfig(
                batch_size=50,
                lease_duration_seconds=30,
            ),
            reporting=ReportingConfig(
                formats=["json", "csv", "html"],
                output_dir=str(tmp_path / "reports"),
            ),
        )

        yield {
            "db_path": db_path,
            "repo": repo,
            "secret_store": secret_store,
            "source_conn": source_conn,
            "target_conn": target_conn,
            "config": config,
            "reports_dir": tmp_path / "reports",
        }


def test_interruption_and_durable_resume(env_setup):
    repo = env_setup["repo"]
    source_conn = env_setup["source_conn"]
    target_conn = env_setup["target_conn"]
    config = env_setup["config"]
    secret_store = env_setup["secret_store"]
    db_path = env_setup["db_path"]

    # 1. Step 1: Inject simulated failure after partition 0 completes
    # We will simulate an interruption while partition 1 is running
    coord1 = ValidationCoordinator(
        config=config,
        repository=repo,
        secret_store=secret_store,
        source_connector=source_conn,
        target_connector=target_conn,
        worker_id="worker_initial",
    )

    # Let's run partition 0 normally, then simulate process crash
    # First plan partitions
    col_stats = source_conn.get_column_stats("customers", "id")
    planned = [
        # Part 0: 1 to 15
        # Part 1: 16 to 30
    ]
    # Run the coordinator, but simulate crash right after Part 0
    run_rec = coord1.run()
    run_id = run_rec.run_id

    # The coordinator completed both partitions if no error was injected.
    # Now let's test a true simulated interruption:
    # We set partition 1 back to Failed, run status back to Interrupted
    partitions = repo.get_partitions(run_id)
    assert len(partitions) == 2
    part_0 = partitions[0]
    part_1 = partitions[1]

    # Check Part 0 has discrepancies from Part 0 (ID 5 missing, ID 10 changed)
    discs_before = repo.get_discrepancies(run_id)
    part_0_discs_before = [d for d in discs_before if d.partition_id == part_0.partition_id]
    assert len(part_0_discs_before) == 2  # ID 5 missing, ID 10 changed

    # Force Part 1 into FAILED status to simulate partial interruption
    with repo._connection() as conn:
        conn.execute(
            "UPDATE partitions SET status = ?, completed_at = NULL WHERE partition_id = ?",
            (PartitionStatus.FAILED.value, part_1.partition_id),
        )
        conn.execute(
            "UPDATE runs SET status = ?, completed_at = NULL WHERE run_id = ?",
            (RunStatus.INTERRUPTED.value, run_id),
        )
        # Delete Part 1 discrepancies to simulate failure before Part 1 committed
        conn.execute(
            "DELETE FROM discrepancies WHERE partition_id = ?",
            (part_1.partition_id,),
        )
        conn.commit()

    # Verify unfinished partitions: ONLY partition 1 should be unfinished
    unfinished = repo.get_unfinished_partitions(run_id)
    assert len(unfinished) == 1
    assert unfinished[0].partition_id == part_1.partition_id

    # 2. Step 2: Simulate complete process restart (new repo connection, new coordinator)
    restarted_repo = SQLiteRunRepository(db_path)
    resumed_source = SyntheticConnector("synthetic_src")
    resumed_source.seed_table("customers", [
        {"id": i, "name": f"User_{i}", "balance": float(i * 100), "category": "Retail"}
        for i in range(1, 31) if i != 22
    ], primary_keys=["id"])

    resumed_target = SyntheticConnector("synthetic_tgt")
    # Same target data
    tgt_data = []
    for i in range(1, 31):
        if i in (5, 20):
            continue
        elif i == 10:
            tgt_data.append({"id": 10, "name": "User_10", "balance": 9999.0, "category": "Retail"})
        elif i != 22:
            tgt_data.append({"id": i, "name": f"User_{i}", "balance": float(i * 100), "category": "Retail"})
    tgt_data.append({"id": 22, "name": "Extra_User_22", "balance": 500.0, "category": "Special"})
    resumed_target.seed_table("customers", tgt_data, primary_keys=["id"])

    coord_resumed = ValidationCoordinator(
        config=config,
        repository=restarted_repo,
        secret_store=secret_store,
        source_connector=resumed_source,
        target_connector=resumed_target,
        worker_id="worker_resumed",
    )

    # 3. Step 3: Resume the run
    resumed_run = coord_resumed.run(resume_run_id=run_id)

    # 4. Verify outcomes:
    # - Run status is Completed with discrepancies
    assert resumed_run.status == RunStatus.COMPLETED_WITH_DISCREPANCIES

    # - Both partitions are now Completed
    all_parts = restarted_repo.get_partitions(run_id)
    assert len(all_parts) == 2
    assert all_parts[0].status == PartitionStatus.COMPLETED
    assert all_parts[1].status == PartitionStatus.COMPLETED

    # - Part 0 was NOT duplicated!
    discs_after = restarted_repo.get_discrepancies(run_id)
    part_0_discs_after = [d for d in discs_after if d.partition_id == part_0.partition_id]
    assert len(part_0_discs_after) == 2, "Committed Part 0 must not duplicate discrepancies on resume"

    # - Part 1 has its discrepancies (ID 20 missing, ID 99 extra)
    part_1_discs = [d for d in discs_after if d.partition_id == part_1.partition_id]
    assert len(part_1_discs) >= 2

    # - Total discrepancies should be 4 (ID 5 missing, ID 10 changed, ID 20 missing, ID 99 extra)
    assert resumed_run.missing_count == 2
    assert resumed_run.changed_count == 1
    assert resumed_run.extra_count == 1

    # - Reports were generated on resume
    refs = restarted_repo.get_report_references(run_id)
    assert len(refs) == 3
    fmts = {r.format for r in refs}
    assert fmts == {"json", "csv", "html"}

    # Verify report files actually exist on disk
    for r in refs:
        assert Path(r.file_path).exists()
