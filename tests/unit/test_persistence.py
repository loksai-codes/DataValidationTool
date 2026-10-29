"""Unit tests for SQLite persistence, transactional commits, leases, and resume logic."""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from data_validator.persistence.models import (
    CheckRecord,
    CheckStatus,
    DiscrepancyRecord,
    DiscrepancyType,
    PartitionRecord,
    PartitionStatus,
    RunRecord,
    RunStatus,
)
from data_validator.persistence.sqlite_repository import SQLiteRunRepository


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_metadata.db"
        yield SQLiteRunRepository(db_path)


def test_create_and_get_run(repo):
    run = RunRecord(
        run_id="run_test_001",
        job_name="test_job",
        status=RunStatus.RUNNING,
        config_json='{"job_name": "test_job"}',
    )
    repo.create_run(run)
    fetched = repo.get_run("run_test_001")
    assert fetched is not None
    assert fetched.run_id == "run_test_001"
    assert fetched.job_name == "test_job"
    assert fetched.status == RunStatus.RUNNING


def test_checks_persistence(repo):
    run = RunRecord(
        run_id="run_chk_001",
        job_name="test_job",
        status=RunStatus.RUNNING,
        config_json="{}",
    )
    repo.create_run(run)

    checks = [
        CheckRecord("run_chk_001_schema", "run_chk_001", "schema", CheckStatus.PASSED, "Schema matched"),
        CheckRecord("run_chk_001_count", "run_chk_001", "row_count", CheckStatus.FAILED, "Count delta: 5"),
    ]
    repo.save_checks(checks)

    fetched = repo.get_checks("run_chk_001")
    assert len(fetched) == 2
    assert fetched[0].check_name == "schema"
    assert fetched[0].status == CheckStatus.PASSED
    assert fetched[1].check_name == "row_count"
    assert fetched[1].status == CheckStatus.FAILED


def test_partition_leases_and_recovery(repo):
    run = RunRecord(
        run_id="run_part_001",
        job_name="test_job",
        status=RunStatus.RUNNING,
        config_json="{}",
    )
    repo.create_run(run)

    partitions = [
        PartitionRecord(partition_id="part_1", run_id="run_part_001", partition_index=0, lower_bound="1", upper_bound="100"),
        PartitionRecord(partition_id="part_2", run_id="run_part_001", partition_index=1, lower_bound="101", upper_bound="200"),
    ]
    repo.save_partitions(partitions)

    # Worker 1 acquires part_1 lease
    acquired = repo.acquire_partition_lease("part_1", worker_id="worker_A", lease_duration_seconds=60)
    assert acquired is True

    # Worker 2 attempts to acquire part_1 lease while active -> should fail
    acquired_again = repo.acquire_partition_lease("part_1", worker_id="worker_B", lease_duration_seconds=60)
    assert acquired_again is False

    # Simulate expired lease on part_1
    with repo._connection() as conn:
        conn.execute(
            "UPDATE partitions SET lease_expires_at = ? WHERE partition_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(), "part_1"),
        )
        conn.commit()

    # Recovery should recover 1 stale partition
    recovered = repo.recover_stale_leases(timeout_seconds=0)
    assert recovered >= 1

    # Now worker B can acquire it
    acquired_b = repo.acquire_partition_lease("part_1", worker_id="worker_B", lease_duration_seconds=60)
    assert acquired_b is True


def test_atomic_commit_and_idempotency(repo):
    run = RunRecord(
        run_id="run_disc_001",
        job_name="test_job",
        status=RunStatus.RUNNING,
        config_json="{}",
    )
    repo.create_run(run)

    partitions = [
        PartitionRecord(partition_id="part_commit_1", run_id="run_disc_001", partition_index=0),
        PartitionRecord(partition_id="part_commit_2", run_id="run_disc_001", partition_index=1),
    ]
    repo.save_partitions(partitions)

    discrepancies = [
        DiscrepancyRecord(
            run_id="run_disc_001",
            partition_id="part_commit_1",
            discrepancy_type=DiscrepancyType.MISSING_IN_TARGET,
            key_values='{"id": 10}',
            column_name=None,
            source_value=None,
            target_value=None,
        ),
        DiscrepancyRecord(
            run_id="run_disc_001",
            partition_id="part_commit_1",
            discrepancy_type=DiscrepancyType.CHANGED_RECORD,
            key_values='{"id": 20}',
            column_name="email",
            source_value="a@test.com",
            target_value="b@test.com",
        ),
    ]

    # Commit results for part_commit_1
    repo.commit_partition_results(
        partition_id="part_commit_1",
        status=PartitionStatus.COMPLETED,
        discrepancies=discrepancies,
        rows_source=100,
        rows_target=99,
        rows_compared=100,
        missing_count=1,
        extra_count=0,
        changed_count=1,
    )

    # Check that part_commit_1 is completed and part_commit_2 is unfinished
    unfinished = repo.get_unfinished_partitions("run_disc_001")
    assert len(unfinished) == 1
    assert unfinished[0].partition_id == "part_commit_2"

    disc_list = repo.get_discrepancies("run_disc_001")
    assert len(disc_list) == 2

    # Retrying the exact same commit must be idempotent (no duplicate discrepancies)
    repo.commit_partition_results(
        partition_id="part_commit_1",
        status=PartitionStatus.COMPLETED,
        discrepancies=discrepancies,
        rows_source=100,
        rows_target=99,
        rows_compared=100,
        missing_count=1,
        extra_count=0,
        changed_count=1,
    )

    disc_list_after = repo.get_discrepancies("run_disc_001")
    assert len(disc_list_after) == 2, "Duplicate discrepancies must not be inserted on retry"
