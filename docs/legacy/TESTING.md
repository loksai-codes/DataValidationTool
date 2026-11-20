# Testing Strategy & Execution Guide

The testing architecture provides fast offline unit tests alongside comprehensive integration tests running against PostgreSQL and AWS S3.

---

## 1. Test Suite Structure

```
tests/
├── unit/
│   ├── test_checkpoint_concurrency_hardening.py  # Unique DB constraint deduplication, 5-worker concurrent replay, partition cursors (4 tests)
│   ├── test_end_to_end_anomaly_suite.py          # 7-anomaly full suite: uninterrupted vs interrupted & resumed exact parity (1 test)
│   ├── test_checkpoint_crash_matrix.py           # 8-scenario crash & recovery transaction boundaries (8 tests)
│   ├── test_composite_keys_advanced.py           # Lexicographical ordering, 2 & 3 col keys, watermark sync, NULL rejection, mixed sort rejection (8 tests)
│   ├── test_datatype_compatibility_matrix.py     # Cross-engine type matrix: BigInt >2^53, Decimal, JSON, Booleans, Nulls (38 tests)
│   ├── test_security_and_concurrency.py          # SQL injection defense, credential masking, multi-run isolation (3 tests)
│   ├── test_durable_checkpoint_resume.py         # Interruption crash simulation, checkpoint recovery, composite keys (4 tests)
│   ├── test_production_readiness_verification.py # Multi-batch streaming, cache concurrency, password masking (9 tests)
│   ├── test_correctness_oracle.py                # Comprehensive 16-scenario oracle suite across all 5 engines (85 tests)
│   ├── test_comparison_strategies.py             # Multi-engine parity, bounded sampling, composite keys (20 tests)
│   ├── test_engine_pool.py                       # Connection pool lifecycle, thread-safety, engine reuse (3 tests)
│   ├── test_metadata_cache.py                    # SQLite metadata cache TTL, invalidation, schema serialization (5 tests)
│   ├── test_schema_discovery_cache.py            # SchemaDiscoveryService integration with persistent cache (2 tests)
│   ├── test_orchestration_resumable.py           # Resumable checkpointing, job cancellation (2 tests)
│   ├── test_serialization.py                    # Canonical serialization, NULL vs empty string, float normalization, SHA-256 (5 tests)
│   ├── test_tolerances.py                       # Absolute and relative tolerance boundary math (5 tests)
│   ├── test_schema_compatibility.py             # Type compatibility matrix, column missing/extra checks (3 tests)
│   ├── test_synthetic_datasets.py               # Synthetic tests: identical, missing, extra, changed, duplicate keys, null diffs (6 tests)
│   └── test_reports.py                          # JSON, CSV, and HTML report validation (3 tests)
└── integration/
    ├── test_postgres_connector.py               # Live queries against PostgreSQL 18.6 public.customers
    ├── test_metadata_repo.py                    # validator_meta migrations, DDL, DML, pagination
    └── test_live_validation.py                  # End-to-end 100,000-record migration validation run
```

**Total Unit Tests:** **214 Passing** (0 Failed, 0 Skipped).  
**Total Integration Tests:** 8 Skipped when local PostgreSQL / AWS credentials are not configured.

---

## 2. Running Tests

### Running Fast Unit Tests (Offline - No Live DB Required)
```powershell
pytest tests/unit/ -v
```

### Running Live Integration Tests (Requires Local PostgreSQL & AWS Profile)
```powershell
pytest tests/integration/ -v -s
```

### Running the Entire Test Suite
```powershell
pytest -v
```

### Running Linting & Style Checks
```powershell
ruff check src/ tests/ benchmarks/
```
