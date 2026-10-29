"""Unit tests for reporting exporters without re-running validation."""
import json
import tempfile
from pathlib import Path

from data_validator.persistence.models import (
    CheckRecord,
    CheckStatus,
    DiscrepancyRecord,
    DiscrepancyType,
    PartitionRecord,
    RunRecord,
    RunStatus,
)
from data_validator.persistence.sqlite_repository import SQLiteRunRepository
from data_validator.reporting.builder import build_report_data
from data_validator.reporting.csv_exporter import export_csv_report
from data_validator.reporting.html_exporter import export_html_report
from data_validator.reporting.json_exporter import export_json_report


def test_reporting_exporters():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test_report.db"
        reports_dir = tmp_path / "reports"

        repo = SQLiteRunRepository(db_path)

        run = RunRecord(
            run_id="run_rep_001",
            job_name="report_test_job",
            status=RunStatus.COMPLETED_WITH_DISCREPANCIES,
            config_json=json.dumps(
                {
                    "source": {"database": "db1", "table": "users"},
                    "target": {"database": "db2", "table": "users"},
                }
            ),
            rows_source=100,
            rows_target=100,
            rows_compared=100,
            missing_count=1,
            changed_count=1,
        )
        repo.create_run(run)

        repo.save_checks(
            [
                CheckRecord("chk_1", "run_rep_001", "schema", CheckStatus.PASSED, "Schema matched"),
                CheckRecord("chk_2", "run_rep_001", "row_count", CheckStatus.PASSED, "Counts matched"),
            ]
        )

        repo.save_partitions(
            [
                PartitionRecord(partition_id="part_1", run_id="run_rep_001", partition_index=0)
            ]
        )

        repo.commit_partition_results(
            partition_id="part_1",
            status=RunStatus.COMPLETED,  # type: ignore
            discrepancies=[
                DiscrepancyRecord(
                    run_id="run_rep_001",
                    partition_id="part_1",
                    discrepancy_type=DiscrepancyType.MISSING_IN_TARGET,
                    key_values='{"id": 42}',
                    source_value='{"id": 42, "name": "SecretUser"}',
                ),
                DiscrepancyRecord(
                    run_id="run_rep_001",
                    partition_id="part_1",
                    discrepancy_type=DiscrepancyType.CHANGED_RECORD,
                    key_values='{"id": 99}',
                    column_name="email",
                    source_value="john@corp.com",
                    target_value="john.new@corp.com",
                ),
            ],
            rows_source=100,
            rows_target=100,
            rows_compared=100,
            missing_count=1,
            extra_count=0,
            changed_count=1,
        )

        # Build report data
        rep_data = build_report_data(repo, "run_rep_001", mask_sensitive=True)
        assert rep_data.summary.job_name == "report_test_job"
        assert len(rep_data.discrepancies) == 2

        # 1. Export CSV
        csv_path = export_csv_report(rep_data, output_dir=reports_dir)
        assert csv_path.exists()
        with open(csv_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "discrepancy_type" in content
            assert "missing_in_target" in content
            assert "changed_record" in content

        # 2. Export JSON
        json_path = export_json_report(rep_data, output_dir=reports_dir)
        assert json_path.exists()
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["summary"]["job_name"] == "report_test_job"
            assert len(data["discrepancies"]) == 2

        # 3. Export HTML
        html_path = export_html_report(rep_data, output_dir=reports_dir)
        assert html_path.exists()
        with open(html_path, "r", encoding="utf-8") as f:
            html_txt = f.read()
            assert "<!DOCTYPE html>" in html_txt
            assert "report_test_job" in html_txt
            assert "missing_in_target" in html_txt
