"""
Integration tests for PostgreSQL connector against local PostgreSQL 18 instance.
"""

import pytest

from migration_validator.config import load_config
from migration_validator.connectors.postgres import PostgresConnector


@pytest.fixture(scope="module")
def pg_connector():
    config = load_config()
    connector = PostgresConnector(config.postgres)
    yield connector
    connector.close()


@pytest.mark.integration
def test_postgres_connection_test(pg_connector):
    res = pg_connector.test_connection()
    assert res.is_successful is True
    assert "PostgreSQL" in res.service_name
    assert res.latency_ms > 0


@pytest.mark.integration
def test_postgres_schema_inspection(pg_connector):
    schema = pg_connector.get_table_schema("customers", "public")
    assert schema.table_name == "customers"
    assert "customer_id" in schema.columns
    assert "email" in schema.columns
    assert "account_balance" in schema.columns
    assert schema.columns["customer_id"].data_type == "bigint"


@pytest.mark.integration
def test_postgres_dynamic_count(pg_connector):
    count = pg_connector.get_record_count("customers", "public")
    assert count == 100000


@pytest.mark.integration
def test_postgres_null_counts(pg_connector):
    cols = ["customer_id", "customer_name", "email", "city", "account_balance", "created_at"]
    nulls = pg_connector.get_null_counts("customers", cols, "public")
    assert all(val == 0 for val in nulls.values())


@pytest.mark.integration
def test_postgres_aggregates(pg_connector):
    aggs = pg_connector.get_aggregates("customers", ["account_balance"], "public")
    assert "account_balance" in aggs
    bal_sum = aggs["account_balance"]["sum"]
    # Verify baseline observed balance: ~24,889,406,323.58
    assert abs(bal_sum - 24889406323.57966) < 1.0


@pytest.mark.integration
def test_postgres_keyset_batching(pg_connector):
    cols = ["customer_id", "email"]
    batch, cursor = pg_connector.fetch_records_batch(
        table_name="customers",
        columns=cols,
        key_columns=["customer_id"],
        batch_size=10,
        last_key=None,
        schema_or_db="public"
    )
    assert len(batch) == 10
    assert cursor == batch["customer_id"].iloc[-1]
