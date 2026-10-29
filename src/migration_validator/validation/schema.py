"""
Schema validation check.
Evaluates column presence, type compatibility, and structural integrity.
"""

import time
import uuid
from typing import Dict, List

from migration_validator.connectors.base import TableSchema
from migration_validator.services.schema_discovery import SchemaDiscoveryService
from migration_validator.validation.models import CheckResult, CheckType, ValidationStatus


def validate_schema(
    discovery_service: SchemaDiscoveryService,
    source_schema: TableSchema,
    dest_schema: TableSchema,
    column_mapping: Dict[str, str],
    key_columns: List[str]
) -> CheckResult:
    """Execute schema validation rule."""
    start = time.perf_counter()
    check_id = f"CHK-SCHEMA-{uuid.uuid4().hex[:8]}"

    comp_result = discovery_service.compare_schemas(
        source_schema=source_schema,
        dest_schema=dest_schema,
        column_mapping=column_mapping,
        key_columns=key_columns
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    if not comp_result.is_compatible or comp_result.missing_in_dest:
        status = ValidationStatus.FAILED
        err_msg = f"Schema mismatch: {len(comp_result.missing_in_dest)} missing columns, {len(comp_result.type_mismatches)} type errors"
    elif comp_result.warnings:
        status = ValidationStatus.WARNING
        err_msg = "; ".join(comp_result.warnings)
    else:
        status = ValidationStatus.PASSED
        err_msg = None

    return CheckResult(
        check_id=check_id,
        check_type=CheckType.SCHEMA,
        check_name="Schema Structural & Type Compatibility",
        status=status,
        source_value=f"{len(source_schema.columns)} columns ({source_schema.full_name})",
        dest_value=f"{len(dest_schema.columns)} columns ({dest_schema.full_name})",
        difference=f"Missing: {len(comp_result.missing_in_dest)}, Mismatches: {len(comp_result.type_mismatches)}",
        tolerance="Exact compatibility",
        duration_ms=round(elapsed_ms, 2),
        error_details=err_msg,
        metadata={
            "missing_in_dest": comp_result.missing_in_dest,
            "extra_in_dest": comp_result.extra_in_dest,
            "type_mismatches": comp_result.type_mismatches,
            "warnings": comp_result.warnings,
            "mapped_columns": [
                {
                    "src": c.source_column,
                    "dst": c.dest_column,
                    "src_type": c.source_type,
                    "dst_type": c.dest_type,
                    "compatible": c.is_type_compatible
                }
                for c in comp_result.mapped_columns
            ]
        }
    )
