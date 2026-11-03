"""
Phase 2 Checkpoint and Concurrency Hardening Test Suite.
Verifies:
1. Unique database constraint protects discrepancy deduplication.
2. Concurrent workers cannot insert duplicate discrepancies for the same run (concurrent batch replay).
3. Replaying a committed batch is strictly idempotent.
4. Independent cursors for multiple partitions.
5. Process restart with identical run ID.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from migration_validator.repositories.metadata_repository import MetadataRepository
from migration_validator.validation.models import DiscrepancyType, ValidationStatus


@pytest.mark.unit
def test_unique_database_constraint_protects_discrepancies(tmp_path):
    """Verify that a unique database constraint on (run_id, discrepancy_hash) physically enforces deduplication."""
    db_file = tmp_path / "unique_constraint_test.db"
    engine = create_engine(f"sqlite:///{db_file}")
    repo = MetadataRepository(engine=engine)
    repo.initialize_schema()

    # Verify that the unique index uq_discrepancies_run_hash exists in SQLite metadata
    insp = inspect(engine)
    indices = insp.get_indexes("validator_meta__discrepancies")
    idx_names = [i["name"] for i in indices]
    unique_indices = [i["name"] for i in indices if i.get("unique")]

    assert "uq_discrepancies_run_hash" in idx_names
    assert "uq_discrepancies_run_hash" in unique_indices

    # Direct insert violating unique constraint must raise IntegrityError
    run_id = "test-uniq-run-1"
    repo.record_run_start(
        run_id=run_id,
        source_type="mock",
        source_identifier="s",
        dest_type="mock",
        dest_identifier="d",
        config_dict={},
    )

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO validator_meta__discrepancies (
                    run_id, discrepancy_type, record_key, column_name,
                    source_val, dest_val, details_json, discrepancy_hash
                ) VALUES (
                    :run_id, 'VALUE_MISMATCH', 'key1', 'col1',
                    '100', '200', '{}', 'hash_unique_12345'
                );
            """),
            {"run_id": run_id},
        )

    # Attempting to insert the exact same run_id and discrepancy_hash directly must fail at the database engine level
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO validator_meta__discrepancies (
                        run_id, discrepancy_type, record_key, column_name,
                        source_val, dest_val, details_json, discrepancy_hash
                    ) VALUES (
                        :run_id, 'VALUE_MISMATCH', 'key1', 'col1',
                        '100', '200', '{}', 'hash_unique_12345'
                    );
                """),
                {"run_id": run_id},
            )


@pytest.mark.unit
def test_concurrent_replay_of_same_batch_is_idempotent(tmp_path):
    """
    Verify that 5 concurrent workers replaying the exact same batch at the exact same millisecond
    cannot create duplicate discrepancies or corrupt checkpoint state.
    """
    db_file = tmp_path / "concurrent_batch_replay.db"
    engine = create_engine(f"sqlite:///{db_file}")
    repo = MetadataRepository(engine=engine)
    repo.initialize_schema()

    run_id = "concurrent-batch-replay-run"
    repo.record_run_start(
        run_id=run_id,
        source_type="mock",
        source_identifier="s",
        dest_type="mock",
        dest_identifier="d",
        config_dict={"batch_size": 10},
    )

    discs = [
        {
            "type": DiscrepancyType.VALUE_MISMATCH,
            "key": f"key-{i}",
            "column": "balance",
            "source_val": f"{i * 100}.00",
            "dest_val": f"{i * 100 + 5}.00",
            "details": {"test": True},
        }
        for i in range(1, 6)
    ]

    def worker_replay(worker_idx: int):
        worker_engine = create_engine(f"sqlite:///{db_file}", connect_args={"timeout": 30.0})
        worker_repo = MetadataRepository(engine=worker_engine)
        worker_repo.commit_checkpoint_with_discrepancies(
            run_id=run_id,
            batch_index=1,
            last_source_key="key-5",
            last_dest_key="key-5",
            records_evaluated=10,
            discrepancies_found=5,
            discrepancies=discs,
            config_fingerprint="fp-replay",
        )

    # 5 workers simultaneously attempt to commit and persist the exact same batch 1
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker_replay, w) for w in range(5)]
        for f in futures:
            f.result()

    # Verify that exactly 5 discrepancy rows exist in the database (ZERO duplicates)
    saved_discs = repo.get_discrepancies_for_run(run_id=run_id)
    assert len(saved_discs) == 5

    # Verify checkpoint is recorded cleanly
    chk = repo.get_latest_checkpoint(run_id=run_id)
    assert chk is not None
    assert chk["batch_index"] == 1
    assert chk["records_evaluated"] == 10
    assert chk["discrepancies_found"] == 5


@pytest.mark.unit
def test_multiple_partitions_maintain_independent_cursors(tmp_path):
    """Verify that multiple data partitions maintain isolated cursors and checkpoints within the same run."""
    db_file = tmp_path / "partition_isolation.db"
    engine = create_engine(f"sqlite:///{db_file}")
    repo = MetadataRepository(engine=engine)
    repo.initialize_schema()

    run_id = "multi-partition-run"
    repo.record_run_start(
        run_id=run_id,
        source_type="mock",
        source_identifier="s",
        dest_type="mock",
        dest_identifier="d",
        config_dict={},
    )

    # Partition 0 commit
    chk_id_p0 = f"CHKPT-{run_id}-part0-000001"
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO validator_meta__partition_checkpoints (
                    checkpoint_id, run_id, batch_index, last_source_key, last_dest_key,
                    records_evaluated, discrepancies_found, config_fingerprint, status
                ) VALUES (
                    :chk_id, :run_id, 1, 'p0-key-100', 'p0-key-100', 100, 2, 'fp1', 'COMMITTED'
                );
            """),
            {"chk_id": chk_id_p0, "run_id": run_id},
        )

    # Partition 1 commit
    chk_id_p1 = f"CHKPT-{run_id}-part1-000001"
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO validator_meta__partition_checkpoints (
                    checkpoint_id, run_id, batch_index, last_source_key, last_dest_key,
                    records_evaluated, discrepancies_found, config_fingerprint, status
                ) VALUES (
                    :chk_id, :run_id, 1, 'p1-key-500', 'p1-key-500', 500, 0, 'fp1', 'COMMITTED'
                );
            """),
            {"chk_id": chk_id_p1, "run_id": run_id},
        )

    checkpoints = repo.get_checkpoints_for_run(run_id=run_id)
    assert len(checkpoints) == 2
    chk_dict = {c["checkpoint_id"]: c for c in checkpoints}
    assert chk_dict[chk_id_p0]["last_source_key"] == "p0-key-100"
    assert chk_dict[chk_id_p0]["records_evaluated"] == 100
    assert chk_dict[chk_id_p1]["last_source_key"] == "p1-key-500"
    assert chk_dict[chk_id_p1]["records_evaluated"] == 500


@pytest.mark.unit
def test_process_restart_clean_recovery(tmp_path):
    """Simulate complete process death, restart, and state resumption using file-backed database."""
    db_file = tmp_path / "process_restart.db"
    run_id = "restart-recovery-run"

    # Process 1: Starts run, commits batch 1 and batch 2
    eng_1 = create_engine(f"sqlite:///{db_file}")
    repo_1 = MetadataRepository(engine=eng_1)
    repo_1.initialize_schema()
    repo_1.record_run_start(
        run_id=run_id,
        source_type="athena",
        source_identifier="customers",
        dest_type="postgres",
        dest_identifier="customers",
        config_dict={"batch_size": 50},
    )

    repo_1.commit_checkpoint_with_discrepancies(
        run_id=run_id,
        batch_index=1,
        last_source_key=50,
        last_dest_key=50,
        records_evaluated=50,
        discrepancies_found=1,
        discrepancies=[{
            "type": DiscrepancyType.VALUE_MISMATCH,
            "key": "25",
            "column": "email",
            "source_val": "a@x.com",
            "dest_val": "b@x.com",
        }],
        config_fingerprint="fp_stable",
    )

    repo_1.commit_checkpoint_with_discrepancies(
        run_id=run_id,
        batch_index=2,
        last_source_key=100,
        last_dest_key=100,
        records_evaluated=50,
        discrepancies_found=0,
        discrepancies=[],
        config_fingerprint="fp_stable",
    )

    # Process 1 abruptly terminates: engine disposed, process memory gone
    eng_1.dispose()
    del repo_1
    del eng_1

    # Process 2: Starts up anew, reconnects to disk database
    eng_2 = create_engine(f"sqlite:///{db_file}")
    repo_2 = MetadataRepository(engine=eng_2)

    # Recover state
    latest_chk = repo_2.get_latest_checkpoint(run_id=run_id)
    assert latest_chk is not None
    assert latest_chk["batch_index"] == 2
    assert latest_chk["last_source_key"] == 100
    assert latest_chk["last_dest_key"] == 100

    discs = repo_2.get_discrepancies_for_run(run_id=run_id)
    assert len(discs) == 1
    assert discs[0]["record_key"] == "25"

    # Process 2 continues with batch 3
    repo_2.commit_checkpoint_with_discrepancies(
        run_id=run_id,
        batch_index=3,
        last_source_key=150,
        last_dest_key=150,
        records_evaluated=50,
        discrepancies_found=1,
        discrepancies=[{
            "type": DiscrepancyType.EXTRA_IN_DEST,
            "key": "142",
            "column": None,
            "source_val": None,
            "dest_val": "142",
        }],
        config_fingerprint="fp_stable",
    )

    final_chk = repo_2.get_latest_checkpoint(run_id=run_id)
    assert final_chk["batch_index"] == 3
    assert final_chk["last_source_key"] == 150
    assert len(repo_2.get_discrepancies_for_run(run_id=run_id)) == 2
    repo_2.update_run_status(run_id=run_id, status=ValidationStatus.FAILED.value)

    eng_2.dispose()
