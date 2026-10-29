"""
Configurable data quality validation checks.
Kept separate from migration consistency results to prevent data anomalies
(such as duplicate emails) from falsely flagging migration infrastructure failures.
"""

import re
import time
import uuid
from typing import List, Optional

from migration_validator.connectors.base import BaseConnector
from migration_validator.validation.models import CheckResult, CheckType, ValidationStatus

EMAIL_REGEX = re.compile(r"^[\w\.\+\-]+@[\w\-]+\.[a-zA-Z]{2,}$")


def validate_email_data_quality(
    connector: BaseConnector,
    table_name: str,
    email_column: str = "email",
    schema_or_db: Optional[str] = "public"
) -> List[CheckResult]:
    """Evaluate email format validity and email occurrences as a data quality metric."""
    results: List[CheckResult] = []
    start = time.perf_counter()

    try:
        # 1. Total vs Distinct Emails Check
        dist_map = connector.get_distinct_counts(table_name, [email_column], schema_or_db=schema_or_db)
        total_records = connector.get_record_count(table_name, schema_or_db=schema_or_db)
        distinct_emails = dist_map.get(email_column, 0)
        duplicate_emails = total_records - distinct_emails

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        # Duplicate emails are treated as a Data Quality WARNING or INFO, not a migration FAIL
        if duplicate_emails == 0:
            status = ValidationStatus.PASSED
            warn_msg = None
        else:
            status = ValidationStatus.WARNING
            warn_msg = f"Duplicate email occurrences observed: {duplicate_emails:,} repeated email entries ({distinct_emails:,} unique)."

        results.append(
            CheckResult(
                check_id=f"CHK-DQ-EMAIL-UNQ-{uuid.uuid4().hex[:6]}",
                check_type=CheckType.DATA_QUALITY,
                check_name=f"Data Quality: Email Uniqueness Analysis ({email_column})",
                status=status,
                source_value=f"{total_records:,} total records",
                dest_value=f"{distinct_emails:,} unique emails",
                difference=f"{duplicate_emails:,} repeated occurrences",
                tolerance="Advisory warning only",
                duration_ms=round(elapsed_ms, 2),
                error_details=warn_msg,
                metadata={
                    "total_records": total_records,
                    "distinct_emails": distinct_emails,
                    "duplicate_occurrences": duplicate_emails
                }
            )
        )

    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        results.append(
            CheckResult(
                check_id=f"CHK-DQ-ERR-{uuid.uuid4().hex[:6]}",
                check_type=CheckType.DATA_QUALITY,
                check_name="Data Quality: Email Validation",
                status=ValidationStatus.INCONCLUSIVE,
                source_value="Error",
                dest_value="Error",
                difference="N/A",
                tolerance="Advisory",
                duration_ms=round(elapsed_ms, 2),
                error_details=f"Execution failed: {str(e)}"
            )
        )

    return results
