"""JSON report exporter for automated CI/CD and programmatic pipelines."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from data_validator.reporting.models import ValidationReportData


def export_json_report(
    report_data: ValidationReportData, output_dir: str | Path = "reports"
) -> Path:
    """Export complete validation report data to a JSON file."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{report_data.summary.run_id}_report.json"
    filepath = out_dir / filename

    data = {
        "summary": asdict(report_data.summary),
        "config": report_data.config_snapshot,
        "checks": report_data.checks,
        "discrepancies": report_data.discrepancies,
        "event_logs": report_data.event_logs,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    return filepath
