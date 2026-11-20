"""
Project configuration (YAML) models.

A *project* file describes connections and the list of table pairs to validate.
The same file format is used for every migration — only connection details and table
lists change — which is what makes validation repeatable across projects.

See ``examples/aws_to_alibaba.yaml`` for a complete, commented example.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from datavalidator.secrets import resolve

ALL_CHECKS = ("schema", "row_count", "keys", "nulls", "aggregates", "distinct", "data", "custom")
DEFAULT_CHECKS = ["schema", "row_count", "keys", "nulls", "aggregates", "data", "custom"]

# Friendly connection "type" -> (dialect, driver). Lets teams write the product name they know.
CONNECTION_TYPES: Dict[str, tuple] = {
    # ---- PostgreSQL protocol family
    "postgres": ("postgres", "postgres"),
    "postgresql": ("postgres", "postgres"),
    "aws-rds-postgres": ("postgres", "postgres"),
    "aws-aurora-postgres": ("postgres", "postgres"),
    "aws-redshift": ("redshift", "postgres"),
    "redshift": ("redshift", "postgres"),
    "alibaba-rds-postgres": ("postgres", "postgres"),
    "alibaba-polardb-pg": ("postgres", "postgres"),
    "alibaba-adb-pg": ("postgres", "postgres"),
    "alibaba-hologres": ("hologres", "postgres"),
    # ---- MySQL protocol family
    "mysql": ("mysql", "mysql"),
    "mariadb": ("mysql", "mysql"),
    "aws-rds-mysql": ("mysql", "mysql"),
    "aws-aurora-mysql": ("mysql", "mysql"),
    "aws-rds-mariadb": ("mysql", "mysql"),
    "alibaba-rds-mysql": ("mysql", "mysql"),
    "alibaba-polardb-mysql": ("mysql", "mysql"),
    "alibaba-polardb-x": ("mysql", "mysql"),
    "alibaba-adb-mysql": ("mysql", "mysql"),
    # ---- Lakehouse / warehouse
    "athena": ("athena", "athena"),
    "aws-athena": ("athena", "athena"),
    "maxcompute": ("maxcompute", "maxcompute"),
    "alibaba-maxcompute": ("maxcompute", "maxcompute"),
    "odps": ("maxcompute", "maxcompute"),
    # ---- Files (Parquet / CSV / JSON) queried with DuckDB
    "files": ("duckdb", "files"),
    "aws-s3": ("duckdb", "files"),
    "alibaba-oss": ("duckdb", "files"),
    "local-files": ("duckdb", "files"),
    # ---- Local / testing
    "sqlite": ("sqlite", "sqlite"),
    "duckdb": ("duckdb", "duckdb"),
}


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FileTable(_Base):
    """A logical table backed by files (only for files / aws-s3 / alibaba-oss connections)."""
    path: Union[str, List[str]]
    format: Literal["parquet", "csv", "json"] = "parquet"
    options: Dict[str, Any] = Field(default_factory=dict)


class ConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: str
    cloud: Optional[str] = None  # "aws" | "alibaba" | "onprem" (informational, shown in reports)
    description: Optional[str] = None

    # Network databases
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    schema_: Optional[str] = Field(default=None, alias="schema")
    ssl: Union[bool, Dict[str, Any], None] = None
    connect_timeout: int = 15
    query_timeout_seconds: int = 1800
    session_init: List[str] = Field(default_factory=list)
    #: IANA zone in which *naive* timestamps stored in this system are expressed.
    #: AWS defaults are usually UTC; Alibaba Cloud defaults are often Asia/Shanghai (UTC+8).
    timezone: str = "UTC"
    max_connections: int = 4

    # Cloud credentials / endpoints (AWS + Alibaba)
    region: Optional[str] = None
    profile: Optional[str] = None
    access_key_id: Optional[str] = None
    access_key_secret: Optional[str] = None
    session_token: Optional[str] = None
    endpoint: Optional[str] = None

    # Athena
    workgroup: Optional[str] = None
    s3_staging_dir: Optional[str] = None
    catalog: Optional[str] = None

    # MaxCompute
    project: Optional[str] = None
    tunnel_endpoint: Optional[str] = None
    hints: Dict[str, str] = Field(default_factory=dict)

    # Files
    tables: Dict[str, FileTable] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in CONNECTION_TYPES:
            raise ValueError(f"unknown connection type '{v}'. Supported: {', '.join(sorted(CONNECTION_TYPES))}")
        return v

    @property
    def dialect(self) -> str:
        return CONNECTION_TYPES[self.type][0]

    @property
    def driver(self) -> str:
        return CONNECTION_TYPES[self.type][1]

    @property
    def default_schema(self) -> Optional[str]:
        return self.schema_


class DatasetRef(_Base):
    connection: str
    table: Optional[str] = None            # "table", "schema.table" or "project.schema.table"
    schema_: Optional[str] = Field(default=None, alias="schema")
    query: Optional[str] = None            # custom SELECT used as the dataset instead of a table
    filter: Optional[str] = None           # SQL predicate, e.g. "dt = '2026-11-01'" (partition pruning)
    #: optional per-side SQL expression overriding the text used for hash bucketing
    bucket_key_expr: Optional[str] = None

    @model_validator(mode="after")
    def _table_or_query(self) -> "DatasetRef":
        if not self.table and not self.query:
            raise ValueError("dataset needs either 'table' or 'query'")
        if self.table and "." in self.table and not self.schema_:
            parts = self.table.split(".")
            self.schema_ = ".".join(parts[:-1])
            self.table = parts[-1]
        return self

    @property
    def schema(self) -> Optional[str]:
        return self.schema_


class Tolerances(_Base):
    numeric_abs: float = 0.0
    numeric_rel: float = 0.0
    float_rel: float = 1e-9                  # applied to approximate (float/double) columns
    timestamp_precision: Literal["s", "ms", "us"] = "us"
    row_count_abs: int = 0
    row_count_pct: float = 0.0
    columns: Dict[str, Dict[str, float]] = Field(default_factory=dict)  # per-column {abs, rel}


class Normalization(_Base):
    trim_strings: bool = False
    rtrim_strings: bool = False               # CHAR(n) padding differences
    case_insensitive_columns: List[str] = Field(default_factory=list)
    empty_string_as_null: bool = False
    ignore_columns: List[str] = Field(default_factory=list)


class CompareOptions(_Base):
    #: auto    -> full if row count <= full_max_rows else profile
    #: full    -> fetch and compare every row (exact, highest egress)
    #: profile -> compare in-database bucket checksums, fetch rows only for mismatching buckets
    #: sample  -> full comparison of a deterministic hash-sample of buckets (same rows both sides)
    #: none    -> skip row-level comparison
    mode: Literal["auto", "full", "profile", "sample", "none"] = "auto"
    full_max_rows: int = 2_000_000
    sample_pct: float = 5.0
    #: target rows fetched per drill-down/full batch (controls memory & query size)
    fetch_batch_rows: int = 200_000
    #: a bucket is fetched directly once it has <= this many rows, otherwise it is subdivided
    leaf_bucket_rows: int = 50_000
    initial_bucket_hex_digits: int = 2   # 16^2 = 256 buckets in first pass
    max_bucket_hex_digits: int = 5
    max_discrepancy_samples: int = 1000
    #: profile string columns with an MD5-derived checksum (exact) in addition to lengths
    string_checksums: bool = True
    #: profile date / timestamp columns via epoch-second sums (time-zone corrected)
    temporal_checksums: bool = True


class CustomCheck(_Base):
    name: str
    source_sql: str
    target_sql: str
    #: compare whole result sets (sorted) — or only a single scalar if both return one cell
    tolerance_abs: float = 0.0
    tolerance_rel: float = 0.0
    severity: Literal["fail", "warn"] = "fail"


class TableDefaults(_Base):
    checks: List[str] = Field(default_factory=lambda: list(DEFAULT_CHECKS))
    tolerances: Tolerances = Field(default_factory=Tolerances)
    normalize: Normalization = Field(default_factory=Normalization)
    compare: CompareOptions = Field(default_factory=CompareOptions)
    distinct_columns: List[str] = Field(default_factory=list)
    fail_on_schema_warnings: bool = False
    #: extra target columns are reported; set True to fail the schema check on them
    fail_on_extra_target_columns: bool = False

    @field_validator("checks")
    @classmethod
    def _valid_checks(cls, v: List[str]) -> List[str]:
        bad = [c for c in v if c not in ALL_CHECKS]
        if bad:
            raise ValueError(f"unknown checks {bad}; valid: {ALL_CHECKS}")
        return v


class TableConfig(TableDefaults):
    name: str
    source: DatasetRef
    target: DatasetRef
    keys: List[str] = Field(default_factory=list)
    #: source column -> target column. Missing columns are auto-mapped by case-insensitive name.
    columns: Dict[str, str] = Field(default_factory=dict)
    auto_map_columns: bool = True
    exclude_columns: List[str] = Field(default_factory=list)
    custom_checks: List[CustomCheck] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class ProjectConfig(_Base):
    project: str
    description: Optional[str] = None
    connections: Dict[str, ConnectionConfig]
    defaults: Dict[str, Any] = Field(default_factory=dict)
    tables: List[TableConfig]
    parallel_tables: int = 4
    output_dir: str = "dv-reports"

    @model_validator(mode="after")
    def _refs_exist(self) -> "ProjectConfig":
        names = set(self.connections)
        seen = set()
        for t in self.tables:
            if t.name in seen:
                raise ValueError(f"duplicate table name '{t.name}'")
            seen.add(t.name)
            for side in (t.source, t.target):
                if side.connection not in names:
                    raise ValueError(f"table '{t.name}': unknown connection '{side.connection}'")
        return self


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_project(path: str, resolve_secrets: bool = True) -> ProjectConfig:
    """Load a project YAML (``include:`` lists of extra table files are supported)."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    base_dir = os.path.dirname(os.path.abspath(path))

    tables = list(raw.get("tables") or [])
    for inc in raw.pop("include", []) or []:
        inc_path = inc if os.path.isabs(inc) else os.path.join(base_dir, inc)
        with open(inc_path, "r", encoding="utf-8") as fh:
            extra = yaml.safe_load(fh) or {}
        tables.extend(extra.get("tables", extra if isinstance(extra, list) else []))
    raw["tables"] = tables

    if resolve_secrets:
        raw["connections"] = resolve(raw.get("connections", {}))

    defaults = raw.get("defaults") or {}
    raw["tables"] = [_deep_merge(defaults, t) for t in tables]
    return ProjectConfig.model_validate(raw)
