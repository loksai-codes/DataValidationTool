"""
Unit tests for schema type compatibility and mapping validation.
"""

import pytest

from migration_validator.connectors.base import ColumnMetadata, TableSchema
from migration_validator.services.schema_discovery import SchemaDiscoveryService


class DummyConnector:
    """Minimal dummy connector for testing discovery service methods."""
    def get_databases_or_schemas(self): return []
    def get_tables(self, s=None): return []
    def get_table_schema(self, t, s=None): pass
    def get_record_count(self, t, s=None): return 0
    def get_null_counts(self, t, c, s=None): return {}
    def get_aggregates(self, t, n, s=None): return {}
    def get_distinct_counts(self, t, c, s=None): return {}
    def get_key_summary(self, t, k, s=None): pass
    def fetch_records_batch(self, t, c, k, b, last_key=None, s=None): return None, None
    def test_connection(self): pass
    def close(self): pass


@pytest.fixture
def discovery():
    return SchemaDiscoveryService(DummyConnector(), DummyConnector())


@pytest.mark.unit
def test_compatible_type_pairs(discovery):
    # Athena string -> PostgreSQL text
    assert discovery.check_type_compatibility("string", "text") is True
    # Athena bigint -> PostgreSQL bigint
    assert discovery.check_type_compatibility("bigint", "bigint") is True
    # Athena double -> PostgreSQL double precision
    assert discovery.check_type_compatibility("double", "double precision") is True
    # Athena timestamp -> PostgreSQL timestamp without time zone
    assert discovery.check_type_compatibility("timestamp", "timestamp without time zone") is True


@pytest.mark.unit
def test_incompatible_type_pairs(discovery):
    # Incompatible: bigint to text
    assert discovery.check_type_compatibility("bigint", "text") is False
    # Incompatible: boolean to timestamp
    assert discovery.check_type_compatibility("boolean", "timestamp") is False


@pytest.mark.unit
def test_schema_comparison_detection(discovery):
    src_schema = TableSchema(
        database_name="migration_validator",
        schema_name="migration_validator",
        table_name="customers",
        columns={
            "customer_id": ColumnMetadata("customer_id", "bigint"),
            "customer_name": ColumnMetadata("customer_name", "string"),
            "email": ColumnMetadata("email", "string"),
            "missing_col": ColumnMetadata("missing_col", "string")
        }
    )

    dst_schema = TableSchema(
        database_name="migration_validator",
        schema_name="public",
        table_name="customers",
        columns={
            "customer_id": ColumnMetadata("customer_id", "bigint"),
            "customer_name": ColumnMetadata("customer_name", "text"),
            "email": ColumnMetadata("email", "text")
        }
    )

    mapping = {
        "customer_id": "customer_id",
        "customer_name": "customer_name",
        "email": "email"
    }

    comp = discovery.compare_schemas(
        source_schema=src_schema,
        dest_schema=dst_schema,
        column_mapping=mapping,
        key_columns=["customer_id"]
    )

    assert comp.is_compatible is False
    assert "missing_col" in comp.missing_in_dest
