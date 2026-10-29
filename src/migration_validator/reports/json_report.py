"""
JSON validation report generator.
Generates a structured, machine-readable validation report without credentials.
"""

import json
from typing import Any, Dict, List, Optional

from migration_validator.validation.models import ValidationRunSummary


def generate_json_report(
    summary: ValidationRunSummary,
    source_identifier: str,
    dest_identifier: str,
    discrepancies_sample: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Generate comprehensive JSON report string."""
    report_dict = {
        "metadata": {
            "generator": "Data Migration Validator v1.0.0",
            "run_id": summary.run_id,
            "overall_status": summary.overall_status.value,
            "started_at": summary.started_at,
            "completed_at": summary.completed_at,
            "duration_seconds": summary.duration_seconds,
            "source_system": source_identifier,
            "destination_system": dest_identifier,
        },
        "kpis": {
            "total_checks": summary.total_checks,
            "passed_checks": summary.passed_checks,
            "failed_checks": summary.failed_checks,
            "warning_checks": summary.warning_checks,
            "inconclusive_checks": summary.inconclusive_checks,
            "records_evaluated": summary.total_records_evaluated,
            "total_discrepancies": summary.total_discrepancies,
            "discrepancy_breakdown": summary.discrepancy_counts,
        },
        "validation_checks": [
            {
                "check_id": c.check_id,
                "check_type": c.check_type.value,
                "name": c.check_name,
                "status": c.status.value,
                "source_value": c.source_value,
                "dest_value": c.dest_value,
                "difference": c.difference,
                "tolerance": c.tolerance,
                "duration_ms": c.duration_ms,
                "error_details": c.error_details,
                "metadata": c.metadata,
            }
            for c in summary.checks
        ],
        "discrepancies_sample": discrepancies_sample or []
    }

    return json.dumps(report_dict, indent=2, default=str)
