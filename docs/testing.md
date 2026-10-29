# Testing Guide

Comprehensive automated testing procedures for unit, integration, and end-to-end validation.

---

## 1. Running the Test Suite

Run all tests:
```powershell
pytest tests -v
```

Run unit tests only:
```powershell
pytest tests/unit -v
```

Run integration tests (durable resume and e2e):
```powershell
pytest tests/integration -v
```

---

## 2. Test Structure & Coverage

```text
tests/
├── unit/
│   ├── test_config.py           # SQL identifier validation, quoting, secret redaction, and config loading
│   ├── test_comparison.py       # Normalization (strings, decimals, timestamps), schema checks, row counts, partition planner, record comparison
│   ├── test_persistence.py      # SQLite WAL repository, lease management, atomic commit, and retry idempotency
│   ├── test_connectors.py       # Synthetic connector, connector registry status matrix, PostgreSQL & Athena mocked tests
│   └── test_reporting.py        # CSV, JSON, and HTML report exporters directly from saved state
└── integration/
    └── test_resume_and_e2e.py   # Multi-partition validation, simulated process crash, DB restart, durable resume, and zero duplicate discrepancies verification
```

---

## 3. Key Test Guarantees

1. **Idempotency on Retry**: `tests/unit/test_persistence.py::test_atomic_commit_and_idempotency` proves that committing the exact same partition result multiple times inserts zero duplicate discrepancies due to the SQLite composite unique constraint.
2. **Crash & Durable Resume**: `tests/integration/test_resume_and_e2e.py` proves that when partition 0 completes and partition 1 is interrupted, resuming after a complete coordinator/repository restart skips partition 0 and only processes partition 1.
3. **Discrepancy Categorization**: `tests/unit/test_comparison.py::test_record_compare_all_discrepancy_types` proves all categories (Missing in Target, Extra in Target, Changed Record, Duplicate Key, NULL Key) are distinctly identified with field-level precision.
