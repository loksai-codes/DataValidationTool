"""Unit tests for configuration validation, SQL identifiers, and secret redaction."""
import pytest
from pydantic import ValidationError

from data_validator.config.loader import export_json_schema, load_batch_manifest, load_job_config
from data_validator.config.models import (
    ComparisonConfig,
    FilterCondition,
    FilterOperator,
    PartitionConfig,
    PartitionStrategy,
    ValidationJobConfig,
)
from data_validator.security.identifiers import quote_identifier, validate_identifier
from data_validator.security.redaction import is_secret_field, mask_value, redact_secrets_dict


def test_sql_identifier_validation():
    # Valid identifiers
    assert validate_identifier("customers") == "customers"
    assert validate_identifier("valid_col_123") == "valid_col_123"
    assert validate_identifier('"quoted_col"') == '"quoted_col"'

    # Disallowed / dangerous SQL injection tokens
    with pytest.raises(ValueError):
        validate_identifier("customers; DROP TABLE users;--")
    with pytest.raises(ValueError):
        validate_identifier("col/*comment*/")
    with pytest.raises(ValueError):
        validate_identifier("123invalid_start")


def test_sql_identifier_quoting():
    assert quote_identifier("users", dialect="postgres") == '"users"'
    assert quote_identifier("users", dialect="mysql") == "`users`"
    assert quote_identifier("users", dialect="mssql") == "[users]"


def test_redaction_and_masking():
    assert is_secret_field("password") is True
    assert is_secret_field("AWS_SECRET_ACCESS_KEY") is True
    assert is_secret_field("username") is False

    secrets_dict = {
        "host": "localhost",
        "user": "admin",
        "password": "supersecretpassword",
        "aws_secret_key": "secret123",
    }
    redacted = redact_secrets_dict(secrets_dict)
    assert redacted["password"] == "******"
    assert redacted["aws_secret_key"] == "******"
    assert redacted["host"] == "localhost"

    # Value masking
    assert mask_value("john.doe@example.com") == "j***@example.com"
    assert mask_value("123456789") == "12*****89"
    assert mask_value(None) == "<NULL>"


def test_load_job_config_example():
    cfg = load_job_config("configs/examples/athena_to_postgres.json")
    assert cfg.config_version == 1
    assert cfg.job_name == "athena_to_postgres_customers"
    assert cfg.source.engine == "athena"
    assert cfg.target.engine == "postgresql"
    assert cfg.comparison.unique_keys == ["customer_id"]


def test_load_manifest_example():
    jobs = load_batch_manifest("configs/examples/jobs.csv")
    assert len(jobs) >= 1
    assert jobs[0].job_name == "customers"
    assert jobs[0].source.engine == "athena"
    assert jobs[0].target.engine == "postgresql"
