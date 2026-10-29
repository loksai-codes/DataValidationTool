"""Schema comparison check for source and target tables."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from data_validator.config.models import ComparisonConfig


class SchemaDiffSeverity(str, Enum):
    COMPATIBLE = "Compatible"
    WARNING = "Warning"
    BLOCKING = "Blocking"


@dataclass
class ColumnDiff:
    column_name: str
    source_type: str | None
    target_type: str | None
    source_nullable: bool | None
    target_nullable: bool | None
    severity: SchemaDiffSeverity
    message: str


@dataclass
class SchemaCheckResult:
    passed: bool
    severity: SchemaDiffSeverity
    summary: str
    diffs: list[ColumnDiff] = field(default_factory=list)


# Type compatibility equivalences
COMPATIBLE_TYPE_GROUPS = [
    {"int", "integer", "int4", "int8", "bigint", "smallint", "tinyint"},
    {"float", "double", "real", "double precision", "float4", "float8"},
    {"numeric", "decimal"},
    {"varchar", "text", "character varying", "char", "string"},
    {"timestamp", "timestamptz", "timestamp with time zone", "timestamp without time zone", "datetime"},
    {"date"},
    {"boolean", "bool"},
]


def _normalize_type_name(t: str | None) -> str:
    if not t:
        return "unknown"
    t_clean = t.lower().split("(")[0].strip()
    return t_clean


def are_types_compatible(t1: str | None, t2: str | None) -> bool:
    if not t1 or not t2:
        return True
    n1 = _normalize_type_name(t1)
    n2 = _normalize_type_name(t2)
    if n1 == n2:
        return True
    for group in COMPATIBLE_TYPE_GROUPS:
        if n1 in group and n2 in group:
            return True
    return False


def run_schema_check(
    source_columns: list[dict[str, Any]],
    target_columns: list[dict[str, Any]],
    config: ComparisonConfig,
) -> SchemaCheckResult:
    """Compare column schemas between source and target tables.

    Columns in ignored_columns are excluded from comparison.
    """
    ignored = {col.lower() for col in config.ignored_columns}
    unique_keys = {k.lower() for k in config.unique_keys}

    s_map = {c["name"].lower(): c for c in source_columns if c["name"].lower() not in ignored}
    t_map = {c["name"].lower(): c for c in target_columns if c["name"].lower() not in ignored}

    diffs: list[ColumnDiff] = []
    has_blocking = False
    has_warning = False

    # Check key presence first
    for k in unique_keys:
        if k not in s_map:
            diffs.append(
                ColumnDiff(
                    column_name=k,
                    source_type=None,
                    target_type=t_map.get(k, {}).get("type"),
                    source_nullable=None,
                    target_nullable=t_map.get(k, {}).get("nullable"),
                    severity=SchemaDiffSeverity.BLOCKING,
                    message=f"Unique key '{k}' is missing from source table.",
                )
            )
            has_blocking = True
        if k not in t_map:
            diffs.append(
                ColumnDiff(
                    column_name=k,
                    source_type=s_map.get(k, {}).get("type"),
                    target_type=None,
                    source_nullable=s_map.get(k, {}).get("nullable"),
                    target_nullable=None,
                    severity=SchemaDiffSeverity.BLOCKING,
                    message=f"Unique key '{k}' is missing from target table.",
                )
            )
            has_blocking = True

    # Compare all columns in source
    for col_lower, s_col in s_map.items():
        if col_lower in t_map:
            t_col = t_map[col_lower]
            s_type = s_col.get("type", "")
            t_type = t_col.get("type", "")

            # Check type compatibility
            if not are_types_compatible(s_type, t_type):
                diffs.append(
                    ColumnDiff(
                        column_name=s_col["name"],
                        source_type=s_type,
                        target_type=t_type,
                        source_nullable=s_col.get("nullable"),
                        target_nullable=t_col.get("nullable"),
                        severity=SchemaDiffSeverity.BLOCKING,
                        message=f"Incompatible data types: source is '{s_type}', target is '{t_type}'.",
                    )
                )
                has_blocking = True
            elif _normalize_type_name(s_type) != _normalize_type_name(t_type):
                diffs.append(
                    ColumnDiff(
                        column_name=s_col["name"],
                        source_type=s_type,
                        target_type=t_type,
                        source_nullable=s_col.get("nullable"),
                        target_nullable=t_col.get("nullable"),
                        severity=SchemaDiffSeverity.WARNING,
                        message=f"Compatible type variance: source '{s_type}' vs target '{t_type}'.",
                    )
                )
                has_warning = True

            # Check nullability risk
            if s_col.get("nullable") and not t_col.get("nullable"):
                diffs.append(
                    ColumnDiff(
                        column_name=s_col["name"],
                        source_type=s_type,
                        target_type=t_type,
                        source_nullable=True,
                        target_nullable=False,
                        severity=SchemaDiffSeverity.WARNING,
                        message="Source is nullable but target is NOT NULL (null value risk).",
                    )
                )
                has_warning = True
        else:
            if col_lower not in unique_keys:
                diffs.append(
                    ColumnDiff(
                        column_name=s_col["name"],
                        source_type=s_col.get("type"),
                        target_type=None,
                        source_nullable=s_col.get("nullable"),
                        target_nullable=None,
                        severity=SchemaDiffSeverity.WARNING,
                        message="Column exists in source but is missing in target.",
                    )
                )
                has_warning = True

    # Check columns in target but not in source
    for col_lower, t_col in t_map.items():
        if col_lower not in s_map and col_lower not in unique_keys:
            diffs.append(
                ColumnDiff(
                    column_name=t_col["name"],
                    source_type=None,
                    target_type=t_col.get("type"),
                    source_nullable=None,
                    target_nullable=t_col.get("nullable"),
                    severity=SchemaDiffSeverity.WARNING,
                    message="Column exists in target but not in source.",
                )
            )
            has_warning = True

    if has_blocking:
        return SchemaCheckResult(
            passed=False,
            severity=SchemaDiffSeverity.BLOCKING,
            summary=f"Schema validation failed with {len([d for d in diffs if d.severity == SchemaDiffSeverity.BLOCKING])} blocking issues.",
            diffs=diffs,
        )
    elif has_warning:
        return SchemaCheckResult(
            passed=True,
            severity=SchemaDiffSeverity.WARNING,
            summary=f"Schema validation passed with {len(diffs)} warnings.",
            diffs=diffs,
        )
    else:
        return SchemaCheckResult(
            passed=True,
            severity=SchemaDiffSeverity.COMPATIBLE,
            summary="Schema matches exactly across compared columns.",
            diffs=[],
        )
