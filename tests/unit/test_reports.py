"""
Unit tests for JSON, CSV, and HTML report generation.
"""

import json

import pytest

from migration_validator.reports.csv_report import generate_csv_discrepancy_report
from migration_validator.reports.html_report import generate_html_report
from migration_validator.reports.json_report import generate_json_report
from migration_validator.validation.models import (
    CheckResult,
    CheckType,
    ValidationRunSummary,
    ValidationStatus,
)


@pytest.fixture
def sample_summary():
    checks = [
        CheckResult(
            check_id="CHK-1",
            check_type=CheckType.RECORD_COUNT,
            check_name="Total Record Count Match",
            status=ValidationStatus.PASSED,
            source_value="100,000",
            dest_value="100,000",
            difference="0 records",
            tolerance="0 records",
            duration_ms=12.5
        ),
        CheckResult(
            check_id="CHK-2",
            check_type=CheckType.AGGREGATE,
            check_name="Numeric Aggregate: SUM(account_balance)",
            status=ValidationStatus.PASSED,
            source_value="24,889,406,323.58",
            dest_value="24,889,406,323.58",
            difference="0.00",
            tolerance="abs=0.01",
            duration_ms=45.2
        )
    ]
    return ValidationRunSummary(
        run_id="RUN-TEST-001",
        overall_status=ValidationStatus.PASSED,
        started_at="2026-10-29T12:00:00",
        completed_at="2026-10-29T12:00:05",
        duration_seconds=5.0,
        total_checks=2,
        passed_checks=2,
        failed_checks=0,
        total_records_evaluated=100000,
        total_discrepancies=0,
        checks=checks
    )


@pytest.mark.unit
def test_json_report_generation(sample_summary):
    json_str = generate_json_report(
        sample_summary,
        source_identifier="athena.customers",
        dest_identifier="postgres.customers"
    )
    data = json.loads(json_str)

    assert data["metadata"]["run_id"] == "RUN-TEST-001"
    assert data["metadata"]["overall_status"] == "PASSED"
    assert data["kpis"]["records_evaluated"] == 100000
    assert len(data["validation_checks"]) == 2
    # Ensure no secrets exist in the json
    assert "password" not in json_str.lower()
    assert "secret" not in json_str.lower()


@pytest.mark.unit
def test_csv_report_generation():
    discs = [
        {
            "discrepancy_type": "VALUE_MISMATCH",
            "record_key": "42",
            "column_name": "account_balance",
            "source_val": "100.50",
            "dest_val": "200.75",
            "details": {"diff": 100.25}
        }
    ]
    csv_str = generate_csv_discrepancy_report(discs, "RUN-TEST-001")
    lines = csv_str.strip().split("\r\n") if "\r\n" in csv_str else csv_str.strip().split("\n")

    assert len(lines) == 2  # Header + 1 record
    assert "VALUE_MISMATCH" in lines[1]
    assert "42" in lines[1]
    assert "account_balance" in lines[1]


@pytest.mark.unit
def test_html_report_generation(sample_summary):
    html = generate_html_report(
        sample_summary,
        source_identifier="athena.customers",
        dest_identifier="postgres.customers"
    )

    assert "<!DOCTYPE html>" in html
    assert "RUN-TEST-001" in html
    assert "Total Record Count Match" in html
    assert "PASSED" in html
    assert "100,000" in html
