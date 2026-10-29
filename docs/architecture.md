# Architecture — Streamlit Data Validation Platform

## 1. Principles
Simple Streamlit UI; independent source and target; database-specific behavior inside connectors; connector-agnostic comparison; separate report generation; durable resume outside session state; local execution first, optional Spark later.

## 2. Flow
```text
Streamlit UI
  ├─ Connections + metadata discovery
  ├─ Compare setup + validation options
  ├─ Progress / resume
  ├─ Results / history / exports
  ↓
Application services + run coordinator
  ├─ Config validation
  ├─ Connector registry
  ├─ Durable repository (SQLite default)
  └─ Report service
       ↓                         ↓
 Source connector          Target connector
       └──────────┬──────────────┘
                  ↓
          Comparison engine
          ├─ Schema checks
          ├─ Count/aggregate checks
          ├─ Key/NULL checks
          ├─ Partition planner
          ├─ Value comparator
          └─ Type normalization
                  ↓
       Incremental transactional result writer
                  ↓
       CSV / JSON / HTML report exporters
```

## 3. Project structure
```text
data-validation-platform/
├── app.py
├── pages/
│   ├── 1_Connections.py
│   ├── 2_Compare_Tables.py
│   ├── 3_Run_Progress.py
│   ├── 4_Results.py
│   └── 5_History.py
├── src/data_validator/
│   ├── app_services/{connections.py,metadata.py,run_service.py,report_service.py}
│   ├── connectors/{base.py,registry.py,aws_athena.py,postgresql.py,mysql.py,mssql.py,maxcompute.py}
│   ├── comparison/{schema_checks.py,count_checks.py,key_checks.py,null_checks.py,aggregate_checks.py,partition_planner.py,record_compare.py,normalization.py}
│   ├── execution/{coordinator.py,local_executor.py,spark_executor.py,worker.py,cancellation.py}
│   ├── persistence/{models.py,repository.py,sqlite_repository.py,migrations/}
│   ├── config/{models.py,loader.py,validators.py,schema.json}
│   ├── reporting/{models.py,builder.py,csv_exporter.py,json_exporter.py,html_exporter.py}
│   ├── security/{secrets.py,identifiers.py,redaction.py}
│   └── utils/
├── configs/{examples/,schemas/}
├── tests/{unit/,integration/,fixtures/}
├── docs/
├── scripts/
├── reports/   # gitignored output
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.txt
└── README.md
```
Create modules incrementally and ensure each is functional; do not add empty scaffolding just to match the tree.

## 4. Connector interface
Each connector provides test_connection, list catalogs/databases, list schemas, list tables, get table metadata, optional row-count estimates, safe read-only query execution, batched fetching with cursor/checkpoint support, capabilities, and cleanup. Use typed query specs. Validate identifiers separately; parameterize values. Connector capabilities must declare support for snapshots, cancellation, filters, partition metadata, and estimates.

## 5. Comparison engine
No Streamlit imports and no report formatting. It accepts validated config, connector instances, metadata, and repository writer. Order: preflight → schema → counts/aggregates → key/null checks → partition plan → actual row comparison → persist checks/discrepancies → complete run only after durable commit.

Match actual values by configured unique keys. Normalize key types and values explicitly. Never collapse duplicates in a dictionary before checking uniqueness. For very large partitions, use sorted keyset streams or bounded batch merge; reject unsupported paging strategies instead of silently loading all rows into RAM.

## 6. Partitioning
Support key ranges, date windows, keyset pagination, connector-native partitioning, and explicitly allowed bounded full scans. Boundaries must be deterministic, non-overlapping and complete. Handle NULL partition values separately or by an explicit rule. Store stable partition IDs, boundaries/cursor, state, attempts, counts, errors and commit time.

## 7. Persistence/resume
SQLite local default behind repository interface. Persist runs, secret-free config snapshot, schemas, checks, partitions, cursors, discrepancies, event logs, retries, errors and report references. Transactionally commit discrepancy records and partition completion. Stable unique keys prevent duplicates. Leases prevent concurrent duplicate work. Stale running leases become Interrupted. Resume skips completed partitions. Resolve secrets again when resuming. Session state is only for UI. Local resume requires retaining the SQLite file.

## 8. Config and metadata
Use versioned Pydantic/JSON Schema. JSON = one full job; CSV = multiple independent jobs, optionally referencing JSON profiles. Metadata cache includes provider/engine, connection status, catalogs/databases, schemas, tables, columns/types/nullability/key info, statistics where available, capabilities, and fetched-at time. Use unknown/unsupported rather than fabricated metadata.

## 9. Reporting
Comparison writes structured persisted results. Exporters consume saved results to create CSV discrepancy files, JSON machine-readable report, and HTML summary. Export must never rerun comparison. Paginate/stream large output and apply masking.

## 10. Performance, reliability, security
Use PyArrow/Polars where they demonstrably help; network/query cost may dominate. Bound memory, concurrency, timeouts and retries. Retry transient failures only. Read-only business access, secrets in environment/Streamlit secrets/secret manager, safe identifiers and parameterized filters, redacted logs, gitignored local data/reports.

## 11. Testing
Test config validation, filters, normalization, schema compatibility, partition coverage, duplicates, null keys, missing/extra/changed records, retries, cancellation, leases, resume/idempotency, report output and synthetic end-to-end runs. Live connector tests are opt-in and read-only.
