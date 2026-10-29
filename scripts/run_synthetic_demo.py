"""Synthetic demo script showcasing end-to-end data validation with rich discrepancies."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure UTF-8 output on Windows terminal
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add src to pythonpath
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from data_validator.config.models import (
    ComparisonConfig,
    ExecutionConfig,
    NormalizationConfig,
    PartitionConfig,
    PartitionStrategy,
    ReportingConfig,
    TableEndpointConfig,
    ValidationJobConfig,
)
from data_validator.connectors.synthetic import SyntheticConnector
from data_validator.execution.coordinator import ValidationCoordinator
from data_validator.persistence.sqlite_repository import SQLiteRunRepository
from data_validator.security.secrets import SecretStore


def main() -> None:
    print("=" * 70)
    print("🚀 Running Synthetic End-to-End Migration Validation Demo")
    print("=" * 70)

    db_path = Path("data/demo_validation_metadata.db")
    src_db = Path("data/demo_source.db")
    tgt_db = Path("data/demo_target.db")
    for f in (db_path, src_db, tgt_db):
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass

    reports_dir = Path("reports/demo")
    reports_dir.mkdir(parents=True, exist_ok=True)

    repo = SQLiteRunRepository(db_path)
    secret_store = SecretStore("configs/connections.json")
    secret_store.set_memory_override("synthetic_src", {"provider": "other", "engine": "synthetic"})
    secret_store.set_memory_override("synthetic_tgt", {"provider": "other", "engine": "synthetic"})

    # Initialize synthetic source and target connectors
    src_conn = SyntheticConnector("synthetic_src", {"db_path": "data/demo_source.db"})
    tgt_conn = SyntheticConnector("synthetic_tgt", {"db_path": "data/demo_target.db"})

    # Seed source dataset: 100 customer records
    print("📦 Seeding Source Table (100 customer records)...")
    src_rows = []
    for i in range(1, 101):
        src_rows.append(
            {
                "customer_id": i,
                "name": f"Customer_{i}",
                "email": f"cust{i}@example.com",
                "tier": "Gold" if i % 5 == 0 else "Silver" if i % 2 == 0 else "Standard",
                "balance": float(i * 150.25),
                "created_at": f"2026-01-{(i % 28) + 1:02d}T10:00:00Z",
            }
        )
    # Add a duplicate key row in source
    src_rows.append(
        {
            "customer_id": 42,
            "name": "Duplicate_42",
            "email": "dup42@example.com",
            "tier": "Standard",
            "balance": 0.0,
            "created_at": "2026-01-15T12:00:00Z",
        }
    )
    src_conn.seed_table("customers", src_rows, primary_keys=None)

    # Seed target dataset with intentional migration discrepancies:
    # 1. Missing in target: IDs 15, 30, 75
    # 2. Changed values: ID 10 (balance modified), ID 50 (tier modified)
    # 3. Extra in target: ID 105, 106
    # 4. Normalization demo: ID 20 (timestamp with different offset)
    print("📦 Seeding Target Table with intentional migration discrepancies...")
    tgt_rows = []
    for r in src_rows:
        cid = r["customer_id"]
        if cid in (15, 30, 75):
            continue  # Missing in target
        elif cid == 10:
            changed_r = dict(r)
            changed_r["balance"] = 99999.99
            tgt_rows.append(changed_r)
        elif cid == 50:
            changed_r = dict(r)
            changed_r["tier"] = "Platinum"
            tgt_rows.append(changed_r)
        else:
            tgt_rows.append(dict(r))

    # Add extra rows in target
    tgt_rows.append(
        {
            "customer_id": 105,
            "name": "Target_Only_User_105",
            "email": "extra105@example.com",
            "tier": "Standard",
            "balance": 250.0,
            "created_at": "2026-02-01T08:00:00Z",
        }
    )
    tgt_rows.append(
        {
            "customer_id": 106,
            "name": "Target_Only_User_106",
            "email": "extra106@example.com",
            "tier": "Gold",
            "balance": 1500.0,
            "created_at": "2026-02-02T08:00:00Z",
        }
    )
    tgt_conn.seed_table("customers", tgt_rows, primary_keys=None)

    # Validation Job Configuration
    job_cfg = ValidationJobConfig(
        config_version=1,
        job_name="synthetic_customers_e2e_demo",
        source=TableEndpointConfig(
            provider="other",  # type: ignore
            engine="synthetic",  # type: ignore
            connection_id="synthetic_src",
            database="demo_source",
            table="customers",
        ),
        target=TableEndpointConfig(
            provider="other",  # type: ignore
            engine="synthetic",  # type: ignore
            connection_id="synthetic_tgt",
            database="demo_target",
            table="customers",
        ),
        comparison=ComparisonConfig(
            unique_keys=["customer_id"],
            partition=PartitionConfig(
                strategy=PartitionStrategy.KEY_RANGE,
                column="customer_id",
                range_size=25,
                include_null_partition=True,
            ),
            ignored_columns=[],
            normalization=NormalizationConfig(
                trim_strings=True,
                case_sensitive_strings=True,
                decimal_tolerance="0.01",
                timestamp_timezone="UTC",
            ),
        ),
        execution=ExecutionConfig(
            batch_size=50,
            max_workers=2,
            retry_attempts=3,
        ),
        reporting=ReportingConfig(
            formats=["json", "csv", "html"],
            output_dir=str(reports_dir),
            mask_sensitive_values=True,
        ),
    )

    print("\n⚙️ Starting Coordinator Execution...")
    coord = ValidationCoordinator(
        config=job_cfg,
        repository=repo,
        secret_store=secret_store,
        source_connector=src_conn,
        target_connector=tgt_conn,
        worker_id="demo_worker",
    )

    result_run = coord.run()

    print("\n" + "=" * 70)
    print(f"🏁 VALIDATION RUN COMPLETED: {result_run.status.value}")
    print("=" * 70)
    print(f"Run ID:                  {result_run.run_id}")
    print(f"Total Partitions:        {result_run.total_partitions}")
    print(f"Completed Partitions:    {result_run.completed_partitions}")
    print(f"Rows Compared:           {result_run.rows_compared:,}")
    print(f"Missing in Target:       {result_run.missing_count:,}")
    print(f"Extra in Target:         {result_run.extra_count:,}")
    print(f"Changed Records:         {result_run.changed_count:,}")
    print(f"Duplicate Keys Detected: {result_run.duplicate_key_count:,}")
    print(f"NULL Keys Detected:      {result_run.null_key_count:,}")

    # Check generated reports
    refs = repo.get_report_references(result_run.run_id)
    print("\n📄 Exported Audit Reports:")
    for ref in refs:
        print(f" - [{ref.format.upper()}] {ref.file_path}")

    print("\n✅ Synthetic demo completed successfully!")


if __name__ == "__main__":
    main()
