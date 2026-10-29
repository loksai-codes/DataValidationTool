"""
Schema discovery service.
Inspects schemas across source and destination, maps columns,
and determines data type compatibility.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from migration_validator.connectors.base import BaseConnector, TableSchema

# Compatibility mapping: maps source type category to destination type category
COMPATIBLE_TYPE_GROUPS = {
    # Integers
    "bigint": {"bigint", "int8", "integer", "int4", "int64"},
    "integer": {"integer", "int4", "bigint", "int8", "int32"},
    "smallint": {"smallint", "int2", "integer"},
    # Strings
    "string": {"text", "character varying", "varchar", "char", "string", "large_string", "str"},
    "str": {"text", "character varying", "varchar", "char", "string", "large_string", "str"},
    "varchar": {"text", "character varying", "varchar", "string", "str"},
    # Floating point / Decimals
    "double": {"double precision", "float8", "float", "numeric", "decimal"},
    "float": {"float4", "double precision", "float8", "real", "numeric"},
    "decimal": {"numeric", "decimal", "double precision"},
    # Timestamps & Dates
    "timestamp": {
        "timestamp", "timestamp without time zone",
        "timestamp with time zone", "timestamptz",
        "datetime64[ns]", "datetime64[us]"
    },
    "date": {"date"},
    # Booleans
    "boolean": {"boolean", "bool"}
}


@dataclass
class ColumnComparison:
    """Comparison of a single mapped column pair."""
    source_column: str
    dest_column: str
    source_type: str
    dest_type: str
    is_type_compatible: bool
    is_exact_type_match: bool
    is_key: bool = False
    warning: Optional[str] = None


@dataclass
class SchemaComparisonResult:
    """Summary of schema comparison between source and destination tables."""
    source_table: str
    dest_table: str
    is_compatible: bool
    mapped_columns: List[ColumnComparison]
    missing_in_dest: List[str] = field(default_factory=list)
    extra_in_dest: List[str] = field(default_factory=list)
    type_mismatches: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class SchemaDiscoveryService:
    """Orchestrates schema discovery and compatibility evaluation."""

    def __init__(self, source_connector: BaseConnector, dest_connector: BaseConnector):
        self.source = source_connector
        self.dest = dest_connector

    def discover_source_schemas(self) -> List[str]:
        return self.source.get_databases_or_schemas()

    def discover_source_tables(self, schema_or_db: Optional[str] = None) -> List[str]:
        return self.source.get_tables(schema_or_db)

    def discover_dest_schemas(self) -> List[str]:
        return self.dest.get_databases_or_schemas()

    def discover_dest_tables(self, schema: Optional[str] = "public") -> List[str]:
        return self.dest.get_tables(schema)

    def get_source_schema(self, table: str, schema_or_db: Optional[str] = None) -> TableSchema:
        return self.source.get_table_schema(table, schema_or_db)

    def get_dest_schema(self, table: str, schema: Optional[str] = "public") -> TableSchema:
        return self.dest.get_table_schema(table, schema)

    def generate_default_mapping(
        self,
        source_schema: TableSchema,
        dest_schema: TableSchema,
        key_candidates: Optional[List[str]] = None
    ) -> Dict[str, str]:
        """Automatically maps columns having matching lowercase names."""
        mapping: Dict[str, str] = {}
        dest_cols_lower = {name.lower(): name for name in dest_schema.columns.keys()}

        for s_col in source_schema.columns.keys():
            if s_col.lower() in dest_cols_lower:
                mapping[s_col] = dest_cols_lower[s_col.lower()]

        return mapping

    def check_type_compatibility(self, src_type: str, dst_type: str) -> bool:
        """Evaluate whether source and destination types can safely represent the same values."""
        s = src_type.lower().strip()
        d = dst_type.lower().strip()

        if s == d:
            return True

        # Check grouped compatibility
        for group_name, types in COMPATIBLE_TYPE_GROUPS.items():
            if (s in types or s == group_name) and (d in types or d == group_name):
                return True

        return False

    def compare_schemas(
        self,
        source_schema: TableSchema,
        dest_schema: TableSchema,
        column_mapping: Dict[str, str],
        key_columns: List[str]
    ) -> SchemaComparisonResult:
        """Perform comprehensive schema comparison."""
        mapped_comps: List[ColumnComparison] = []
        missing_in_dest: List[str] = []
        extra_in_dest: List[str] = []
        type_mismatches: List[str] = []
        warnings: List[str] = []

        mapped_dest_cols: Set[str] = set()

        # Check key columns presence
        for k in key_columns:
            if k not in column_mapping:
                warnings.append(f"Business key column '{k}' is not mapped.")
            elif column_mapping[k] not in dest_schema.columns:
                warnings.append(f"Business key column '{column_mapping[k]}' does not exist in destination table.")

        for src_col, dest_col in column_mapping.items():
            mapped_dest_cols.add(dest_col)
            src_meta = source_schema.columns.get(src_col)
            dst_meta = dest_schema.columns.get(dest_col)

            if not src_meta:
                warnings.append(f"Source column '{src_col}' not found in source schema.")
                continue
            if not dst_meta:
                missing_in_dest.append(dest_col)
                continue

            is_compat = self.check_type_compatibility(src_meta.data_type, dst_meta.data_type)
            is_exact = (src_meta.data_type.lower() == dst_meta.data_type.lower())
            is_key = (src_col in key_columns)

            col_warn = None
            if not is_compat:
                msg = f"Type mismatch: '{src_col}' ({src_meta.data_type}) -> '{dest_col}' ({dst_meta.data_type})"
                type_mismatches.append(msg)
                col_warn = msg

            mapped_comps.append(
                ColumnComparison(
                    source_column=src_col,
                    dest_column=dest_col,
                    source_type=src_meta.data_type,
                    dest_type=dst_meta.data_type,
                    is_type_compatible=is_compat,
                    is_exact_type_match=is_exact,
                    is_key=is_key,
                    warning=col_warn
                )
            )

        # Detect unmapped columns
        for s_col in source_schema.columns.keys():
            if s_col not in column_mapping:
                missing_in_dest.append(s_col)

        for d_col in dest_schema.columns.keys():
            if d_col not in mapped_dest_cols:
                extra_in_dest.append(d_col)

        is_compatible = (len(type_mismatches) == 0) and (len(missing_in_dest) == 0)

        return SchemaComparisonResult(
            source_table=source_schema.full_name,
            dest_table=dest_schema.full_name,
            is_compatible=is_compatible,
            mapped_columns=mapped_comps,
            missing_in_dest=missing_in_dest,
            extra_in_dest=extra_in_dest,
            type_mismatches=type_mismatches,
            warnings=warnings
        )
