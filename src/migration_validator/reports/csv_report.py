"""
CSV discrepancy report generator.
Exports record-level discrepancies into standard tabular CSV for auditing.
"""

import csv
import io
from typing import Any, Dict, List


def generate_csv_discrepancy_report(
    discrepancies: List[Dict[str, Any]],
    run_id: str
) -> str:
    """Generate CSV text containing all discrepancies for a validation run."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "run_id",
        "discrepancy_type",
        "record_key",
        "column_name",
        "source_value",
        "destination_value",
        "details"
    ])

    for d in discrepancies:
        writer.writerow([
            run_id,
            d.get("discrepancy_type") or d.get("type", "UNKNOWN"),
            d.get("record_key") or d.get("key", ""),
            d.get("column_name") or d.get("column", ""),
            d.get("source_val") or d.get("source_value", ""),
            d.get("dest_val") or d.get("dest_value", ""),
            str(d.get("details_json") or d.get("details", ""))
        ])

    return output.getvalue()
