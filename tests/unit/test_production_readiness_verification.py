"""
Production Readiness Verification Test Suite.
Verifies multi-batch correctness, key-synchronized streaming, metadata cache concurrency,
connection pool security, and process-restart/resumption semantics.
"""

import threading
from typing import Any, List, Optional

import pandas as pd
import pytest

from migration_validator.cache.metadata_cache import (
    SQLiteMetadataCache,
    deserialize_table_schema,
    generate_connection_fingerprint,
    serialize_table_schema,
)
from migration_validator.connectors.base import (
    BaseConnector,
    ColumnMetadata,
    ConnectionTestResult,
    KeySummary,
    TableSchema,
)
from migration_validator.connectors.engine_pool import get_engine_pool
from migration_validator.validation.models import (
    DiscrepancyType,
    ValidationStatus,
)
from migration_validator.validation.record_comparison import (
    RecordComparisonEngine,
    run_record_comparison_check,
)


class MultiBatchMockConnector(BaseConnector):
    """In-memory connector with keyset pagination simulation."""

    def __init__(self, data: pd.DataFrame, key_col: str = "customer_id"):
        self.df = data.copy()
        self.key_col = key_col

    def test_connection(self):
        return ConnectionTestResult(True, "Mock", "Connected", 0.1)

    def get_databases_or_schemas(self):
        return ["public"]

    def get_tables(self, s=None):
        return ["customers"]

    def get_table_schema(self, table_name, schema_or_db=None):
        cols = {
            c: ColumnMetadata(c, "varchar" if "name" in c or "email" in c else "bigint", True, c == self.key_col, idx)
            for idx, c in enumerate(self.df.columns, start=1)
        }
        return TableSchema("mock", "public", table_name, cols)

    def get_record_count(self, table_name, schema_or_db=None):
        return len(self.df)

    def get_null_counts(self, table_name, columns, schema_or_db=None):
        return {c: int(self.df[c].isnull().sum()) for c in columns if c in self.df}

    def get_aggregates(self, table_name, numeric_columns, schema_or_db=None):
        return {}

    def get_distinct_counts(self, table_name, columns, schema_or_db=None):
        return {c: int(self.df[c].nunique()) for c in columns if c in self.df}

    def get_key_summary(self, table_name, key_columns, schema_or_db=None):
        pk = key_columns[0]
        s = self.df[pk]
        tot = len(self.df)
        dist = int(s.nunique())
        nulls = int(s.isnull().sum())
        return KeySummary(tot, dist, nulls, max(0, tot - nulls - dist), s.min(), s.max())

    def fetch_records_batch(
        self,
        table_name: str,
        columns: List[str],
        key_columns: List[str],
        batch_size: int,
        last_key: Optional[Any] = None,
        schema_or_db: Optional[str] = None
    ):
        pk = key_columns[0]
        sub = self.df
        if last_key is not None:
            sub = sub[sub[pk] > last_key]
        sub = sub[columns].sort_values(by=pk, ascending=True).head(batch_size)
        if sub.empty:
            return sub, None
        return sub, sub[pk].iloc[-1]

    def close(self):
        pass


# -----------------------------------------------------------------------------
# Phase 2: Multi-Batch Correctness Tests Across All Engines
# -----------------------------------------------------------------------------

@pytest.fixture
def multi_batch_datasets():
    """
    Creates a 25-record dataset divided across 5 batches (batch_size = 5).
    Injected anomalies:
      - Batch 1: Key 3 missing in destination
      - Batch 2: Key 8 changed balance in destination
      - Batch 3: Key 14 extra in destination
      - Batch 4: Key 18 NULL email in destination (non-NULL in source)
      - Batch 5: Key 23 duplicate in destination
    """
    src_rows = []
    dst_rows = []

    for i in range(1, 26):
        src_row = {
            "customer_id": i,
            "customer_name": f"Customer_{i}",
            "email": f"user_{i}@example.com",
            "account_balance": float(i * 100),
        }
        dst_row = dict(src_row)

        if i == 3:
            # Missing in destination
            src_rows.append(src_row)
            continue
        elif i == 8:
            # Value mismatch
            dst_row["account_balance"] = 9999.0
        elif i == 14:
            # Extra in destination: skip adding to source
            dst_rows.append(dst_row)
            continue
        elif i == 18:
            # NULL mismatch
            dst_row["email"] = None
        elif i == 23:
            # Duplicate key in destination (2 copies total: 1 original + 1 duplicate)
            src_rows.append(src_row)
            dst_rows.append(dst_row)
            dup_row = dict(dst_row)
            dup_row["customer_name"] = "Customer_23_Duplicate"
            dst_rows.append(dup_row)
            continue

        src_rows.append(src_row)
        dst_rows.append(dst_row)

    src_df = pd.DataFrame(src_rows)
    dst_df = pd.DataFrame(dst_rows)
    return src_df, dst_df


@pytest.mark.parametrize("strategy", ["duckdb", "vectorized_pandas", "polars", "hash_assisted"])
def test_multi_batch_correctness_all_strategies(multi_batch_datasets, strategy):
    src_df, dst_df = multi_batch_datasets
    src_conn = MultiBatchMockConnector(src_df)
    dst_conn = MultiBatchMockConnector(dst_df)

    cols = ["customer_id", "customer_name", "email", "account_balance"]
    col_map = {c: c for c in cols}

    # Batch size = 5 guarantees 5 distinct batches
    engine = RecordComparisonEngine(
        source_connector=src_conn,
        dest_connector=dst_conn,
        source_table="customers",
        dest_table="customers",
        key_columns=["customer_id"],
        column_mapping=col_map,
        batch_size=5,
        strategy=strategy,
        max_discrepancy_samples=1000
    )

    result, total_diffs, counts = run_record_comparison_check(engine)

    assert result.status == ValidationStatus.FAILED
    assert counts[DiscrepancyType.MISSING_IN_DEST.value] == 1  # Key 3
    assert counts[DiscrepancyType.VALUE_MISMATCH.value] == 1   # Key 8
    assert counts[DiscrepancyType.EXTRA_IN_DEST.value] == 1    # Key 14
    assert counts[DiscrepancyType.NULL_MISMATCH.value] == 1     # Key 18
    assert counts[DiscrepancyType.DUPLICATE_KEY.value] == 2     # Key 23 (2 rows share duplicate key)
    assert total_diffs == 6, f"Expected exactly 6 discrepancies, got {total_diffs} with counts {counts}"


# -----------------------------------------------------------------------------
# Phase 4: Metadata Cache Concurrency and Security Verification
# -----------------------------------------------------------------------------

def test_metadata_cache_concurrent_access(tmp_path):
    cache_path = str(tmp_path / "concurrent_cache.db")
    cache = SQLiteMetadataCache(cache_db_path=cache_path, default_ttl_seconds=3600)

    fp = generate_connection_fingerprint(
        "POSTGRESQL", host="localhost", port=5432, database="test_db", user="test_user"
    )

    cols = {
        "id": ColumnMetadata("id", "bigint", False, True, 1),
        "name": ColumnMetadata("name", "varchar", True, False, 2),
    }
    schema = TableSchema("postgres", "public", "test_table", cols)
    serialized = serialize_table_schema(schema)

    errors = []

    def writer_worker(idx):
        try:
            cache.set(fp, "table_schemas", f"public.table_{idx}", serialized)
        except Exception as e:
            errors.append(e)

    def reader_worker(idx):
        try:
            val = cache.get(fp, "table_schemas", f"public.table_{idx}")
            if val is not None:
                deserialize_table_schema(val)
        except Exception as e:
            errors.append(e)

    threads = []
    for i in range(10):
        t1 = threading.Thread(target=writer_worker, args=(i,))
        t2 = threading.Thread(target=reader_worker, args=(i,))
        threads.extend([t1, t2])

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent metadata cache access raised errors: {errors}"


def test_metadata_cache_configuration_invalidation(tmp_path):
    cache_path = str(tmp_path / "config_cache.db")
    cache = SQLiteMetadataCache(cache_db_path=cache_path, default_ttl_seconds=3600)

    # Initial config (port 5432)
    fp_v1 = generate_connection_fingerprint(
        "POSTGRESQL", host="localhost", port=5432, database="test_db", user="test_user"
    )
    cols = {"id": ColumnMetadata("id", "bigint", False, True, 1)}
    schema = TableSchema("postgres", "public", "customers", cols)
    cache.set(fp_v1, "table_schemas", "public.customers", serialize_table_schema(schema))

    # Cached value present
    assert cache.get(fp_v1, "table_schemas", "public.customers") is not None

    # Modified config (port changed to 5433)
    fp_v2 = generate_connection_fingerprint(
        "POSTGRESQL", host="localhost", port=5433, database="test_db", user="test_user"
    )
    # Different fingerprint must result in cache miss, preventing stale metadata reuse
    assert fp_v1 != fp_v2
    assert cache.get(fp_v2, "table_schemas", "public.customers") is None


def test_metadata_cache_excludes_credentials():
    # Ensure fingerprint doesn't accept or leak password
    import inspect
    sig = inspect.signature(generate_connection_fingerprint)
    param_names = list(sig.parameters.keys())
    assert "password" not in param_names
    assert "secret" not in param_names
    assert "token" not in param_names


# -----------------------------------------------------------------------------
# Phase 4 & 6: EnginePool Security and Masking
# -----------------------------------------------------------------------------

def test_engine_pool_masks_passwords():
    pool = get_engine_pool()
    raw_url = "postgresql+psycopg://my_admin:SuperSecretPassword123!@db.example.com:5432/prod_db"
    safe_key = pool.generate_engine_key(raw_url)

    # Engine key is a SHA-256 hash digest, completely masking passwords and internal URLs
    assert "SuperSecretPassword123!" not in safe_key
    assert len(safe_key) == 16


# -----------------------------------------------------------------------------
# Phase 3: Process Interruption and Safe Checkpoint Advancement
# -----------------------------------------------------------------------------

def test_record_comparison_cancellation_preserves_partial_state(multi_batch_datasets):
    src_df, dst_df = multi_batch_datasets
    src_conn = MultiBatchMockConnector(src_df)
    dst_conn = MultiBatchMockConnector(dst_df)

    cols = ["customer_id", "customer_name", "email", "account_balance"]
    col_map = {c: c for c in cols}

    engine = RecordComparisonEngine(
        source_connector=src_conn,
        dest_connector=dst_conn,
        source_table="customers",
        dest_table="customers",
        key_columns=["customer_id"],
        column_mapping=col_map,
        batch_size=5,
        strategy="duckdb"
    )

    batch_count = 0
    collected_discrepancies = []

    def cancel_after_2_batches():
        nonlocal batch_count
        return batch_count >= 2

    def sink(batch):
        collected_discrepancies.extend(batch)

    # Execute stream with cancellation check
    try:
        for batch_discs, stats in engine.stream_compare(cancel_check=cancel_after_2_batches):
            batch_count += 1
            sink(batch_discs)
    except InterruptedError:
        pass

    # Cancelled after 2 batches: total records evaluated = 10
    assert batch_count == 2
    # Only batch 1 anomaly (Key 3 missing) and batch 2 anomaly (Key 8 changed) were evaluated
    assert len(collected_discrepancies) == 2
    assert any(d.key == "3" and d.type == DiscrepancyType.MISSING_IN_DEST for d in collected_discrepancies)
    assert any(d.key == "8" and d.type == DiscrepancyType.VALUE_MISMATCH for d in collected_discrepancies)
