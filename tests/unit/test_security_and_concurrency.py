"""
Unit tests for Security, Parameter Binding, Credential Masking, Read-Only Enforcement,
and Multi-Run Concurrency Isolation.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine

from migration_validator.config import PostgresConfig
from migration_validator.connectors.postgres import _quote_ident
from migration_validator.repositories.metadata_repository import MetadataRepository
from migration_validator.validation.models import (
    DiscrepancyType,
    ValidationStatus,
)


@pytest.mark.unit
def test_identifier_quoting_sql_injection_defense():
    """Verify that SQL identifiers with malicious SQL injection payloads are safely quoted and escaped."""
    malicious_table = 'customers"; DROP TABLE customers; --'
    quoted = _quote_ident(malicious_table)
    # Quotes should be doubled: "customers""; DROP TABLE customers; --"
    assert quoted == '"customers""; DROP TABLE customers; --"'

    malicious_column = 'col" OR 1=1 --'
    quoted_col = _quote_ident(malicious_column)
    assert quoted_col == '"col"" OR 1=1 --"'


@pytest.mark.unit
def test_credential_masking():
    """Verify that database credentials and secrets are masked in configs and representations."""
    cfg = PostgresConfig(
        _env_file=None,
        host="db.prod.internal",
        port=5432,
        database="prod_db",
        user="analyst_admin",
        password=SecretStr("SuperSecretP@ssw0rd!123"),
    )

    # 1. safe_connection_url must not contain the plaintext password
    safe_url = cfg.safe_connection_url
    assert "SuperSecretP@ssw0rd!123" not in safe_url
    assert "*****" in safe_url
    assert "analyst_admin:*****@db.prod.internal:5432/prod_db" in safe_url

    # 2. Pydantic repr and str must not expose the raw password
    assert "SuperSecretP@ssw0rd!123" not in repr(cfg)
    assert "SuperSecretP@ssw0rd!123" not in str(cfg)
    assert "SuperSecretP@ssw0rd!123" not in repr(cfg.password)


@pytest.mark.unit
def test_concurrent_run_isolation(tmp_path):
    """
    Verify that two concurrent validation runs operating on the same metadata repository
    remain strictly isolated by run_id without cross-talk or race conditions.
    """
    db_file = tmp_path / "concurrent_meta.db"
    engine = create_engine(f"sqlite:///{db_file}")
    repo = MetadataRepository(engine=engine)
    repo.initialize_schema()

    run_id_1 = "run-isolation-alpha-001"
    run_id_2 = "run-isolation-beta-002"

    def worker_run(run_id: str, prefix: str):
        # Create separate engine/repo handle per thread for SQLite thread safety
        worker_engine = create_engine(f"sqlite:///{db_file}")
        worker_repo = MetadataRepository(engine=worker_engine)
        worker_repo.record_run_start(
            run_id=run_id,
            source_type=f"{prefix}-src",
            source_identifier="accounts",
            dest_type=f"{prefix}-dst",
            dest_identifier="accounts",
            config_dict={"batch_size": 100},
        )

        for i in range(1, 6):
            disc = {
                "type": DiscrepancyType.VALUE_MISMATCH,
                "key": f"{prefix}-key-{i}",
                "column": "balance",
                "source_val": "100.00",
                "dest_val": "999.00",
                "details": {},
            }
            worker_repo.commit_checkpoint_with_discrepancies(
                run_id=run_id,
                batch_index=i,
                last_source_key=f"{prefix}-key-{i}",
                last_dest_key=f"{prefix}-key-{i}",
                records_evaluated=10,
                discrepancies_found=1,
                discrepancies=[disc],
                config_fingerprint="test_fp",
            )

        worker_repo.update_run_status(run_id=run_id, status=ValidationStatus.FAILED.value)

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker_run, run_id_1, "ALPHA")
        f2 = executor.submit(worker_run, run_id_2, "BETA")
        f1.result()
        f2.result()

    # Verify Run 1 discrepancies and checkpoints
    discs_1 = repo.get_discrepancies_for_run(run_id=run_id_1)
    assert len(discs_1) == 5
    assert all("ALPHA" in d["record_key"] for d in discs_1)
    assert not any("BETA" in d["record_key"] for d in discs_1)

    chk_1 = repo.get_latest_checkpoint(run_id=run_id_1)
    assert chk_1 is not None
    assert chk_1["batch_index"] == 5
    assert chk_1["records_evaluated"] == 10
    assert chk_1["last_source_key"] == "ALPHA-key-5"

    # Verify Run 2 discrepancies and checkpoints
    discs_2 = repo.get_discrepancies_for_run(run_id=run_id_2)
    assert len(discs_2) == 5
    assert all("BETA" in d["record_key"] for d in discs_2)
    assert not any("ALPHA" in d["record_key"] for d in discs_2)

    chk_2 = repo.get_latest_checkpoint(run_id=run_id_2)
    assert chk_2 is not None
    assert chk_2["batch_index"] == 5
    assert chk_2["records_evaluated"] == 10
    assert chk_2["last_source_key"] == "BETA-key-5"
