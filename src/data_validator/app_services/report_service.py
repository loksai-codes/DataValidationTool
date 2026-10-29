"""Application service for generating and retrieving validation reports."""
from __future__ import annotations

from pathlib import Path

from data_validator.persistence.models import ReportReference
from data_validator.persistence.repository import BaseRunRepository
from data_validator.persistence.sqlite_repository import SQLiteRunRepository
from data_validator.reporting.builder import build_report_data
from data_validator.reporting.csv_exporter import export_csv_report
from data_validator.reporting.html_exporter import export_html_report
from data_validator.reporting.json_exporter import export_json_report


class ReportService:
    """Service to export and retrieve reports from persisted run state."""

    def __init__(self, repository: BaseRunRepository | None = None) -> None:
        self.repo = repository or SQLiteRunRepository()

    def get_or_create_report(
        self,
        run_id: str,
        fmt: str,
        output_dir: str = "reports",
        mask_sensitive: bool = True,
    ) -> Path:
        """Export report in the specified format (csv, json, html) directly from saved state.

        Never re-runs validation!
        """
        fmt_clean = fmt.lower().strip()
        report_data = build_report_data(self.repo, run_id, mask_sensitive=mask_sensitive)

        if fmt_clean == "csv":
            path = export_csv_report(report_data, output_dir=output_dir)
        elif fmt_clean == "json":
            path = export_json_report(report_data, output_dir=output_dir)
        elif fmt_clean == "html":
            path = export_html_report(report_data, output_dir=output_dir)
        else:
            raise ValueError(f"Unsupported report format: {fmt}")

        self.repo.save_report_reference(
            ReportReference(run_id=run_id, format=fmt_clean, file_path=str(path))
        )
        return path

    def list_reports_for_run(self, run_id: str) -> list[ReportReference]:
        return self.repo.get_report_references(run_id)
