# Testing Strategy & Execution Guide

The testing architecture provides fast offline unit tests alongside comprehensive integration tests running against PostgreSQL and AWS S3.

---

## 1. Test Suite Structure

```
tests/
├── unit/
│   ├── test_correctness_oracle.py        # Comprehensive 16-scenario oracle suite across all 5 engines (85 tests)
│   ├── test_comparison_strategies.py     # Multi-engine parity, bounded sampling, composite keys (20 tests)
│   ├── test_engine_pool.py               # Connection pool lifecycle, thread-safety, engine reuse (3 tests)
│   ├── test_metadata_cache.py            # SQLite metadata cache TTL, invalidation, schema serialization (5 tests)
│   ├── test_schema_discovery_cache.py    # SchemaDiscoveryService integration with persistent cache (2 tests)
│   ├── test_orchestration_resumable.py   # Resumable checkpointing, job cancellation (2 tests)
│   ├── test_serialization.py            # Canonical serialization, NULL vs empty string, float normalization, SHA-256 (5 tests)
│   ├── test_tolerances.py               # Absolute and relative tolerance boundary math (5 tests)
│   ├── test_schema_compatibility.py     # Type compatibility matrix, column missing/extra checks (3 tests)
│   ├── test_synthetic_datasets.py       # Synthetic tests: identical, missing, extra, changed, duplicate keys, null diffs (6 tests)
│   └── test_reports.py                  # JSON, CSV, and HTML report validation (3 tests)
└── integration/
    ├── test_postgres_connector.py       # Live queries against PostgreSQL 18.6 public.customers
    ├── test_metadata_repo.py            # validator_meta migrations, DDL, DML, pagination
    └── test_live_validation.py          # End-to-end 100,000-record migration validation run
```

---

## 2. Running Tests

### Running Fast Unit Tests (Offline - No Live DB Required)
```powershell
pytest -m unit -v
```

### Running Live Integration Tests (Requires Local PostgreSQL & AWS Profile)
```powershell
pytest -m integration -v -s
```

### Running the Entire Test Suite
```powershell
pytest -v
```

### Running Linting & Style Checks
```powershell
ruff check .
```

---

## 3. Synthetic Failure Scenarios

`tests/unit/test_synthetic_datasets.py` constructs synthetic in-memory DataFrames to verify exact failure detection:

1. **Identical Datasets**: 0 discrepancies, `PASSED`.
2. **Missing Rows in Destination**: Identifies specific missing keys, flags `MISSING_IN_DEST`, `FAILED`.
3. **Extra Rows in Destination**: Flags unexpected keys, `EXTRA_IN_DEST`, `FAILED`.
4. **Changed Values / Column Corruptions**: Compares SHA-256 fingerprints, identifies mismatched columns (`account_balance`, `city`), flags `VALUE_MISMATCH`, `FAILED`.
5. **Duplicate Keys**: Detects repeated keys in destination, flags `DUPLICATE_KEY`, `FAILED`.
6. **NULL Value Differences**: Detects unexpected NULL occurrences, flags `FAILED`.
