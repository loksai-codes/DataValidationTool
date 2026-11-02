"""
Unit tests verifying that SchemaDiscoveryService reuses cached metadata and avoids
redundant database connector calls across repeated discovery invocations.
"""

from unittest.mock import MagicMock

import pytest

from migration_validator.cache.metadata_cache import SQLiteMetadataCache
from migration_validator.connectors.base import ColumnMetadata, TableSchema
from migration_validator.services.schema_discovery import SchemaDiscoveryService


@pytest.fixture
def memory_cache():
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "disc_cache.db")
        cache = SQLiteMetadataCache(cache_db_path=db_path)
        yield cache
        cache.close()


def test_schema_discovery_caches_schemas_and_tables(memory_cache):
    mock_src = MagicMock()
    mock_src.get_databases_or_schemas.return_value = ["db_alpha", "db_beta"]
    mock_src.get_tables.return_value = ["customers", "orders"]

    mock_dst = MagicMock()
    mock_dst.get_databases_or_schemas.return_value = ["public", "analytics"]
    mock_dst.get_tables.return_value = ["customers", "transactions"]

    svc = SchemaDiscoveryService(mock_src, mock_dst, cache=memory_cache)

    # First call - hits connector
    schemas_1 = svc.discover_source_schemas()
    assert schemas_1 == ["db_alpha", "db_beta"]
    assert mock_src.get_databases_or_schemas.call_count == 1

    # Second call - served from cache!
    schemas_2 = svc.discover_source_schemas()
    assert schemas_2 == ["db_alpha", "db_beta"]
    assert mock_src.get_databases_or_schemas.call_count == 1  # Not called again!

    # Force refresh - hits connector again
    schemas_3 = svc.discover_source_schemas(force_refresh=True)
    assert schemas_3 == ["db_alpha", "db_beta"]
    assert mock_src.get_databases_or_schemas.call_count == 2


def test_schema_discovery_caches_table_schema(memory_cache):
    mock_src = MagicMock()
    schema_obj = TableSchema(
        database_name="prod",
        schema_name="public",
        table_name="customers",
        columns={"id": ColumnMetadata(name="id", data_type="bigint", is_nullable=False)},
    )
    mock_src.get_table_schema.return_value = schema_obj
    mock_dst = MagicMock()

    svc = SchemaDiscoveryService(mock_src, mock_dst, cache=memory_cache)

    # First fetch
    res1 = svc.get_source_schema("customers", "public")
    assert res1.table_name == "customers"
    assert mock_src.get_table_schema.call_count == 1

    # Second fetch - cache hit
    res2 = svc.get_source_schema("customers", "public")
    assert res2.table_name == "customers"
    assert "id" in res2.columns
    assert mock_src.get_table_schema.call_count == 1
