"""
Configuration and secrets management for Data Migration Validator.
Uses Pydantic Settings to load and validate environment variables and .env files.
Prevents plaintext credential exposure through SecretStr.
"""

import os
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AWSAthenaConfig(BaseSettings):
    """Configuration for AWS Athena and underlying S3 source."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    profile: Optional[str] = Field(
        default=os.getenv("AWS_PROFILE", "personal-dev"),
        validation_alias="AWS_PROFILE",
        description="AWS CLI profile name"
    )
    region: str = Field(
        default=os.getenv("AWS_REGION", "ap-south-1"),
        validation_alias="AWS_REGION",
        description="AWS Region"
    )
    athena_database: str = Field(
        default=os.getenv("ATHENA_DATABASE", "migration_validator"),
        validation_alias="ATHENA_DATABASE",
        description="Athena Database name"
    )
    athena_workgroup: str = Field(
        default=os.getenv("ATHENA_WORKGROUP", "primary"),
        validation_alias="ATHENA_WORKGROUP",
        description="Athena WorkGroup"
    )
    s3_output_location: str = Field(
        default=os.getenv("ATHENA_S3_OUTPUT_LOCATION", "s3://loksai-migration-validator-960607644797/Unsaved/"),
        validation_alias="ATHENA_S3_OUTPUT_LOCATION",
        description="S3 output path for Athena query results"
    )
    s3_source_bucket: str = Field(
        default=os.getenv("S3_SOURCE_BUCKET", "loksai-migration-validator-960607644797"),
        validation_alias="S3_SOURCE_BUCKET",
        description="S3 Bucket containing Parquet data"
    )
    s3_source_key: str = Field(
        default=os.getenv("S3_SOURCE_KEY", "raw-data/customers/part-000.parquet"),
        validation_alias="S3_SOURCE_KEY",
        description="S3 Key for Parquet dataset"
    )


class PostgresConfig(BaseSettings):
    """Configuration for destination PostgreSQL database."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    host: str = Field(
        default="localhost",
        validation_alias="POSTGRES_HOST",
        description="PostgreSQL Host"
    )
    port: int = Field(
        default=5432,
        validation_alias="POSTGRES_PORT",
        description="PostgreSQL Port"
    )
    database: str = Field(
        default="migration_validator",
        validation_alias="POSTGRES_DATABASE",
        description="PostgreSQL Database Name"
    )
    user: str = Field(
        default="postgres",
        validation_alias="POSTGRES_USER",
        description="PostgreSQL Username"
    )
    password: Optional[SecretStr] = Field(
        default=None,
        validation_alias="POSTGRES_PASSWORD",
        description="PostgreSQL Password (masked)"
    )

    @property
    def raw_password(self) -> str:
        """Return the unmasked password string for connection strings."""
        if self.password:
            return self.password.get_secret_value()
        return ""

    @property
    def connection_url(self) -> str:
        """SQLAlchemy connection string with Psycopg 3 driver."""
        pw = self.raw_password
        auth = f"{self.user}:{pw}" if pw else self.user
        return f"postgresql+psycopg://{auth}@{self.host}:{self.port}/{self.database}"

    @property
    def safe_connection_url(self) -> str:
        """Masked connection URL safe for logging and reporting."""
        return f"postgresql+psycopg://{self.user}:*****@{self.host}:{self.port}/{self.database}"


class ValidationConfig(BaseSettings):
    """Configuration for validation engine thresholds and parameters."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    batch_size: int = Field(
        default=5000,
        validation_alias="VALIDATION_BATCH_SIZE",
        description="Records per comparison batch"
    )
    numeric_tolerance_abs: float = Field(
        default=0.01,
        validation_alias="VALIDATION_NUMERIC_TOLERANCE_ABS",
        description="Absolute tolerance for financial numeric comparisons"
    )
    numeric_tolerance_rel: float = Field(
        default=0.0001,
        validation_alias="VALIDATION_NUMERIC_TOLERANCE_REL",
        description="Relative tolerance ratio for numeric comparisons"
    )
    query_timeout_seconds: int = Field(
        default=120,
        validation_alias="VALIDATION_QUERY_TIMEOUT_SECONDS",
        description="Database query timeout in seconds"
    )
    log_level: str = Field(
        default="INFO",
        validation_alias="VALIDATION_LOG_LEVEL",
        description="Application log level"
    )


class AppConfig(BaseSettings):
    """Root application configuration aggregating all components."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    aws: AWSAthenaConfig = Field(default_factory=AWSAthenaConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)


def load_config() -> AppConfig:
    """Load and return the validated application configuration."""
    return AppConfig()
