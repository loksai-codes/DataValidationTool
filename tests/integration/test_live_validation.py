"""
Live integration test executing full validation against the real 100,000-record dataset.
Validates end-to-end orchestration, S3 source, PostgreSQL destination, and metadata persistence.
"""

import pytest

from migration_validator.config import load_config
from migration_validator.connectors.athena import AthenaConnector
from migration_validator.connectors.postgres import PostgresConnector
from migration_validator.repositories.metadata_repository import MetadataRepository
from migration_validator.services.orchestration import ValidationOrchestrator
from migration_validator.validation.models import ValidationRuleConfig, ValidationStatus


@pytest.mark.integration
def test_live_100k_validation_run():
    """Run full validation against the 100,000 customer records in S3 and PostgreSQL."""
    config = load_config()

    athena_conn = AthenaConnector(config.aws)
    pg_conn = PostgresConnector(config.postgres)
    repo = MetadataRepository(config.postgres)

    orchestrator = ValidationOrchestrator(athena_conn, pg_conn, repo)

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
            "city": "city",
            "account_balance": "account_balance",
            "created_at": "created_at"
        },
        numeric_tolerance_abs=0.01,
        numeric_tolerance_rel=0.0001,
        use_sampling=False,  # Full 100,000 records
        batch_size=10000
    )

    try:
        summary = orchestrator.execute_validation_run(
            source_table="customers",
            dest_table="customers",
            rule_config=rule_config,
            source_db="migration_validator",
            dest_schema="public"
        )

        print("\n--- LIVE VALIDATION RUN SUMMARY ---")
        print(f"Run ID: {summary.run_id}")
        print(f"Overall Status: {summary.overall_status.value}")
        print(f"Records Evaluated: {summary.total_records_evaluated:,}")
        print(f"Passed Checks: {summary.passed_checks} / {summary.total_checks}")
        print(f"Failed Checks: {summary.failed_checks}")
        print(f"Warnings: {summary.warning_checks}")
        print(f"Total Discrepancies: {summary.total_discrepancies}")
        print(f"Duration: {summary.duration_seconds:.2f}s")

        assert summary.total_records_evaluated == 100000
        assert summary.failed_checks == 0
        assert summary.total_discrepancies == 0
        # Warnings may be 1 due to data quality check on duplicate emails (9,014 duplicate emails as observed in baseline)
        assert summary.overall_status in [ValidationStatus.PASSED, ValidationStatus.WARNING]

    finally:
        athena_conn.close()
        pg_conn.close()
        repo.close()
