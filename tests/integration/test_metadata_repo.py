"""
Integration tests for MetadataRepository managing validator_meta schema.
"""

import uuid

import pytest

from migration_validator.config import load_config
from migration_validator.repositories.metadata_repository import MetadataRepository


@pytest.fixture(scope="module")
def repo():
    config = load_config()
    repository = MetadataRepository(config.postgres)
    repository.initialize_schema()
    yield repository
    repository.close()


@pytest.mark.integration
def test_metadata_run_lifecycle(repo):
    run_id = f"TEST-RUN-{uuid.uuid4().hex[:8]}"

    # 1. Record Start
    repo.record_run_start(
        run_id=run_id,
        source_type="AWS_S3",
        source_identifier="athena.customers",
        dest_type="POSTGRES",
        dest_identifier="public.customers",
        config_dict={"batch_size": 1000}
    )

    run = repo.get_run_by_id(run_id)
    assert run is not None
    assert run["status"] == "RUNNING"

    # 2. Record Checks
    checks = [
        {
            "check_id": f"CHK-1-{run_id}",
            "check_type": "RECORD_COUNT",
            "check_name": "Count Parity",
            "status": "PASSED",
            "source_value": "100",
            "dest_value": "100",
            "difference": "0",
            "tolerance": "0",
            "duration_ms": 10.0,
            "error_details": None,
            "metadata": {}
        }
    ]
    repo.record_checks(run_id, checks)

    saved_checks = repo.get_checks_for_run(run_id)
    assert len(saved_checks) == 1
    assert saved_checks[0]["status"] == "PASSED"

    # 3. Record Discrepancies
    discs = [
        {
            "type": "VALUE_MISMATCH",
            "key": "123",
            "column": "account_balance",
            "source_val": "100.0",
            "dest_val": "200.0",
            "details": {}
        }
    ]
    repo.record_discrepancies(run_id, discs)

    saved_discs = repo.get_discrepancies_for_run(run_id)
    assert len(saved_discs) == 1
    assert saved_discs[0]["record_key"] == "123"

    # 4. Record Finish
    repo.record_run_finish(
        run_id=run_id,
        status="PASSED",
        total_checks=1,
        passed_checks=1,
        failed_checks=0,
        warning_checks=0,
        inconclusive_checks=0,
        total_records_evaluated=100,
        total_discrepancies=1,
        duration_seconds=1.25
    )

    updated_run = repo.get_run_by_id(run_id)
    assert updated_run["status"] == "PASSED"
    assert updated_run["total_discrepancies"] == 1
