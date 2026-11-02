"""
Unit tests for Persistent SQLite Metadata Cache.
Validates cache hits, misses, TTL expiration, invalidation, and schema serialization.
"""

import os
import tempfile
import time

import pytest

from migration_validator.cache.metadata_cache import (
    SQLiteMetadataCache,
    deserialize_table_schema,
    generate_connection_fingerprint,
    serialize_table_schema,
)
from migration_validator.connectors.base import ColumnMetadata, TableSchema


@pytest.fixture
def temp_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_metadata_cache.db")
        cache = SQLiteMetadataCache(cache_db_path=db_path, default_ttl_seconds=2)
        yield cache
        cache.close()


def test_fingerprint_generation():
    fp1 = generate_connection_fingerprint("POSTGRESQL", "localhost", 5432, "test_db", "user1")
    fp2 = generate_connection_fingerprint("POSTGRESQL", "localhost", 5432, "test_db", "user1")
    fp3 = generate_connection_fingerprint("POSTGRESQL", "localhost", 5432, "other_db", "user1")

    assert fp1 == fp2
    assert fp1 != fp3
    assert len(fp1) == 24


def test_cache_hit_and_miss(temp_cache):
    fp = "test_fp_001"
    assert temp_cache.get(fp, "schemas", "public") is None

    temp_cache.set(fp, "schemas", "public", ["customers", "orders"])
    val = temp_cache.get(fp, "schemas", "public")
    assert val == ["customers", "orders"]

    stats = temp_cache.get_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["cached_entries"] == 1


def test_cache_ttl_expiration(temp_cache):
    fp = "test_fp_002"
    # Set with 1 second TTL
    temp_cache.set(fp, "tables", "public", ["table1"], ttl_seconds=1)
    assert temp_cache.get(fp, "tables", "public") == ["table1"]

    time.sleep(1.2)
    assert temp_cache.get(fp, "tables", "public") is None


def test_cache_invalidation(temp_cache):
    fp = "test_fp_003"
    temp_cache.set(fp, "table_schema", "customers", {"col": "val"})
    temp_cache.set(fp, "table_schema", "orders", {"col": "val2"})
    temp_cache.set("other_fp", "table_schema", "customers", {"col": "val3"})

    # Invalidate customers for fp
    deleted = temp_cache.invalidate(fingerprint=fp, category="table_schema", key="customers")
    assert deleted == 1
    assert temp_cache.get(fp, "table_schema", "customers") is None
    assert temp_cache.get(fp, "table_schema", "orders") is not None
    assert temp_cache.get("other_fp", "table_schema", "customers") is not None


def test_table_schema_serialization():
    cols = {
        "id": ColumnMetadata(name="id", data_type="bigint", is_nullable=False, is_primary_key=True),
        "name": ColumnMetadata(name="name", data_type="varchar", is_nullable=True),
    }
    schema = TableSchema(database_name="db", schema_name="public", table_name="users", columns=cols)

    data = serialize_table_schema(schema)
    reconstructed = deserialize_table_schema(data)

    assert reconstructed.table_name == "users"
    assert reconstructed.schema_name == "public"
    assert "id" in reconstructed.columns
    assert reconstructed.columns["id"].data_type == "bigint"
    assert reconstructed.columns["id"].is_nullable is False
