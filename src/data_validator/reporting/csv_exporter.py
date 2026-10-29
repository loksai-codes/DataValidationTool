"""CSV report exporter for discrepancies and summary."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from data_validator.reporting.models import ValidationReportData


def export_csv_report(
    report_data: ValidationReportData, output_dir: str | Path = "reports"
) -> Path:
    """Export discrepancies list to a clean, well-formatted CSV file."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{report_data.summary.run_id}_discrepancies.csv"
    filepath = out_dir / filename

    fieldnames = [
        "discrepancy_id",
        "partition_id",
        "discrepancy_type",
        "key_values",
        "column_name",
        "source_value",
        "target_value",
        "detected_at",
    ]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for disc in report_data.discrepancies:
            writer.writerow(
                {
                    "discrepancy_id": disc.get("id"),
                    "partition_id": disc.get("partition_id"),
                    "discrepancy_type": disc.get("type"),
                    "key_values": disc.get("key"),
                    "column_name": disc.get("column"),
                    "source_value": disc.get("source_value"),
                    "target_value": disc.get("target_value"),
                    "detected_at": disc.get("detected_at"),
                }
            )

    return filepath
