"""Pydantic v2 models for validation configuration."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from data_validator.security.identifiers import validate_identifier


class ProviderType(str, Enum):
    AWS = "aws"
    ALIBABA = "alibaba"
    AZURE = "azure"
    GCP = "gcp"
    OTHER = "other"


class EngineType(str, Enum):
    ATHENA = "athena"
    POSTGRESQL = "postgresql"
    SYNTHETIC = "synthetic"
    MYSQL = "mysql"
    MSSQL = "mssql"
    MAXCOMPUTE = "maxcompute"


class PartitionStrategy(str, Enum):
    KEY_RANGE = "key_range"
    DATE_RANGE = "date_range"
    KEYSET_PAGINATION = "keyset_pagination"
    CONNECTOR_NATIVE = "connector_native"
    BOUNDED_FULL_SCAN = "bounded_full_scan"


class FilterOperator(str, Enum):
    EQUALS = "="
    NOT_EQUALS = "!="
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUAL = ">="
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="
    IN = "IN"
    IS_NULL = "IS NULL"
    IS_NOT_NULL = "IS NOT NULL"
    BETWEEN = "BETWEEN"


class FilterCondition(BaseModel):
    """Structured filter applied equivalently to source and target."""
    model_config = ConfigDict(extra="forbid")

    column: str
    operator: FilterOperator = FilterOperator.EQUALS
    value: Any = None

    @field_validator("column")
    @classmethod
    def validate_column_identifier(cls, v: str) -> str:
        return validate_identifier(v)


class TableEndpointConfig(BaseModel):
    """Configuration for either source or target table endpoint."""
    model_config = ConfigDict(extra="forbid")

    provider: ProviderType = ProviderType.AWS
    engine: EngineType = EngineType.POSTGRESQL
    connection_id: str = Field(..., description="ID referencing named connection")
    catalog: str | None = None
    database: str = Field(..., description="Database or database schema name")
    schema_: str | None = Field(default=None, alias="schema")
    table: str = Field(..., description="Table name")

    @field_validator("table")
    @classmethod
    def validate_tbl(cls, v: str) -> str:
        return validate_identifier(v)

    @field_validator("schema_")
    @classmethod
    def validate_sch(cls, v: str | None) -> str | None:
        if v:
            return validate_identifier(v)
        return v

    @field_validator("catalog")
    @classmethod
    def validate_cat(cls, v: str | None) -> str | None:
        if v:
            return validate_identifier(v)
        return v


class ChecksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_check: bool = Field(default=True, alias="schema")
    row_count: bool = True
    duplicate_keys: bool = True
    nulls: bool = True
    data_level: bool = True
    aggregates: bool = False


class PartitionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: PartitionStrategy = PartitionStrategy.KEY_RANGE
    column: str | None = None
    range_size: int | None = Field(default=25000, ge=1)
    date_interval: Literal["day", "month", "hour"] | None = "day"
    include_null_partition: bool = True
    allow_full_scan_fallback: bool = False

    @field_validator("column")
    @classmethod
    def validate_col(cls, v: str | None) -> str | None:
        if v:
            return validate_identifier(v)
        return v

    @model_validator(mode="after")
    def validate_strategy_requirements(self) -> PartitionConfig:
        # Note: if column is None here, ComparisonConfig validator will populate from unique_keys[0] if possible
        return self


class NormalizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trim_strings: bool = False
    case_sensitive_strings: bool = True
    decimal_tolerance: str = "0"
    timestamp_timezone: str = "UTC"
    ignore_whitespace_diffs: bool = False


class ComparisonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unique_keys: list[str] = Field(..., min_length=1)
    filters: list[FilterCondition] = Field(default_factory=list)
    checks: ChecksConfig = Field(default_factory=ChecksConfig)
    partition: PartitionConfig = Field(default_factory=PartitionConfig)
    ignored_columns: list[str] = Field(default_factory=list)
    normalization: NormalizationConfig = Field(default_factory=NormalizationConfig)

    @field_validator("unique_keys")
    @classmethod
    def validate_keys(cls, v: list[str]) -> list[str]:
        for k in v:
            validate_identifier(k)
        return v

    @field_validator("ignored_columns")
    @classmethod
    def validate_ignored(cls, v: list[str]) -> list[str]:
        for c in v:
            validate_identifier(c)
        return v

    @model_validator(mode="after")
    def populate_and_check_partition_column(self) -> ComparisonConfig:
        if self.partition.strategy in (
            PartitionStrategy.KEY_RANGE,
            PartitionStrategy.DATE_RANGE,
            PartitionStrategy.KEYSET_PAGINATION,
        ) and not self.partition.column:
            if self.unique_keys:
                self.partition.column = self.unique_keys[0]
            else:
                raise ValueError(
                    f"Partition strategy '{self.partition.strategy.value}' requires a column or at least one unique key."
                )
        return self


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["local", "spark"] = "local"
    batch_size: int = Field(default=5000, ge=1, le=100000)
    max_workers: int = Field(default=4, ge=1, le=32)
    retry_attempts: int = Field(default=3, ge=0, le=10)
    query_timeout_seconds: int = Field(default=300, ge=5, le=3600)
    lease_duration_seconds: int = Field(default=60, ge=10, le=600)


class ReportingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formats: list[Literal["json", "csv", "html"]] = Field(
        default_factory=lambda: ["json", "csv", "html"]
    )
    mask_sensitive_values: bool = True
    output_dir: str = "reports"


class ValidationJobConfig(BaseModel):
    """Complete validation configuration for a single job."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    config_version: int = Field(default=1, ge=1)
    job_name: str = Field(..., min_length=1)
    source: TableEndpointConfig
    target: TableEndpointConfig
    comparison: ComparisonConfig
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)

    @field_validator("job_name")
    @classmethod
    def validate_job_name(cls, v: str) -> str:
        return validate_identifier(v)


class BatchManifestRow(BaseModel):
    """Model representing a row in a multi-job CSV manifest."""
    model_config = ConfigDict(extra="ignore")

    job_name: str
    source_engine: EngineType
    source_connection_id: str
    source_database: str
    source_schema: str | None = None
    source_table: str
    target_engine: EngineType
    target_connection_id: str
    target_database: str
    target_schema: str | None = None
    target_table: str
    unique_key: str
    config_file: str | None = None
