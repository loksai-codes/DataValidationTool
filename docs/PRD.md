# Product Requirements Document (PRD)
**Product:** Streamlit Data Validation Platform | **Version:** 1.0

## 1. Product summary
Build a simple Streamlit app to validate data between independently selected source and target databases/tables. Users configure checks, compare actual values using unique keys and partitioned/batched reads, inspect discrepancies, export reports, and resume interrupted runs. First complete connector pair: AWS Athena → PostgreSQL.

## 2. Goals
- Clean two-column Source / Target setup.
- Provider and engine dropdowns independently selectable on both sides.
- Discover catalog/database, schema, tables, columns, and key metadata.
- Validate schema, row count, key uniqueness, NULLs, actual data values, and optional aggregates.
- Partition-based comparison without cross-platform SQL joins or fingerprints as the core method.
- JSON configuration for one job; CSV manifest for multiple jobs.
- Durable resume after Streamlit reruns, browser refresh, session expiry, process interruption, and app restart.
- Separate connectors, comparison engine, persistence/execution, metadata discovery, and reporting.
- Read-only access to business tables; secrets never stored in configs or logs.

## 3. Non-goals
- No React, Next.js, or FastAPI.
- No mandatory Spark.
- No requirement to choose an S3 bucket as the primary workflow.
- No automatic data repair or mutation of source/target tables.
- No claim of connector support until implemented and tested.

## 4. User workflow
1. Select source provider → engine → named connection → catalog/database → schema → table.
2. Independently select target provider → engine → connection → database/schema/table.
3. Test connections and refresh metadata.
4. Configure keys, filters, partitioning, checks, normalization, batch size, workers, timeouts.
5. Preflight validate.
6. Start run and monitor progress by check/partition.
7. Refresh or return later; find the durable run and resume unfinished work.
8. Inspect results and export reports without rerunning validation.

## 5. UI
### Home / Compare Setup
Two clean columns labelled Source and Target. Provider options may include AWS, Alibaba Cloud, Azure, Google Cloud, and Other/Self-hosted. Engine options include Athena, PostgreSQL, MySQL, SQL Server/MSSQL, and MaxCompute, but only operational connectors may be enabled. Provide named connection, Test Connection, database/catalog, schema, table, Refresh Metadata, and metadata timestamp. Do not ask for an S3 path in the main flow. Athena workgroup/query-result settings belong in connection configuration and must be explained there.

### Validation Options
- Single/composite unique key, suggested from metadata.
- Safe structured filters applied equivalently to both sides (e.g. country equals China).
- Partition strategy: key range, date range, keyset pagination, connector-native, or bounded full scan when explicitly allowed.
- Checks: schema, row count, duplicate keys, nulls, data-level comparison, optional aggregates.
- Ignored columns and per-column normalization.
- Local execution default; Spark optional only if configured.
- Batch size, worker cap, retry count, query timeout, consistency/snapshot warning.

### Run Progress
Show run ID, status, duration, tables, active check, completed/total partitions, rows read/compared, discrepancy counts, retries, and last checkpoint. States: Draft, Queued, Running, Paused, Interrupted, Failed, Completed, Completed with discrepancies, Cancel requested, Cancelled. Provide Resume, Retry Failed Partitions, Cancel, View Results.

### Results
Tabs: Summary, Schema, Counts/Aggregates, Missing in Target, Extra in Target, Field Mismatches, Nulls/Keys, Execution Log, Configuration. Show key, column, source value, target value, mismatch type, partition, and diagnostic evidence. Paginate results and avoid loading all discrepancies into Streamlit memory. Export CSV, JSON, HTML. Export must not rerun validation.

### Connections and history
Manage named connections without revealing secrets. Filter run history by date/status/source/target/job. Open, resume, clone config, and export old runs.

## 6. Validation semantics
- **Schema:** compare columns, types, nullability, optionally order; classify compatible/warning/blocking differences explicitly.
- **Row count:** compare filtered datasets; disclose consistency limitations if data changes during execution.
- **Keys:** require configured unique key(s) for normal record comparison. Detect null and duplicate keys. Do not silently collapse duplicates.
- **NULLs:** compare per-column null counts and support required-not-null rules.
- **Data level:** independently stream source and target batches; match by normalized single/composite key; compare actual configured field values. Classify source-only as missing in target, target-only as extra, and same-key differing fields as changed. Explicitly normalize decimals, timestamps/time zones, dates, booleans, strings, binary, and NULLs.
- **Aggregates:** optional count/sum/min/max/average/distinct-count checks with explicit numeric tolerance.
- **Filters:** use structured filter models and parameterized values; validate identifiers and apply semantically equivalent filters to both sources.
- **Partitioning:** deterministic, non-overlapping, complete coverage. Key-range requires suitable range column; date-range requires date/time column; otherwise use keyset pagination or explicitly allowed bounded scan. Handle NULL partition values, empty tables, gaps, skew, inclusive/exclusive boundaries and time zones. Reject unsafe configurations.

## 7. Durable resume — core requirement
Use SQLite as default local metadata store behind a repository interface; keep it separate from business tables. Persist run ID, secret-free config snapshot, table/schema metadata snapshots, check state, partition plan and boundaries/cursors, completed partitions, counts, discrepancies, retries, sanitized errors, event log, timestamps, and report references.
- Commit discrepancy results and partition completion in one transaction.
- Stable IDs and unique constraints make retries idempotent.
- Use leases to prevent duplicate workers; recover stale running leases as Interrupted.
- Resume only unfinished/failed partitions and checks.
- Re-resolve credentials on resume; missing credentials must not erase progress.
- Cancellation is cooperative at batch/partition boundaries.
- Streamlit session state is UI-only, never the sole progress store.
- Document that local SQLite resume depends on retaining the SQLite file; multi-machine deployments need shared durable storage.

## 8. Configuration
Versioned Pydantic or JSON Schema. JSON represents a complete job. CSV represents independent jobs and may reference a JSON profile for advanced nested settings. Config references connection IDs, never credentials. Validate all configs before running. Use bounded parallelism and independent job/run IDs.

## 9. Performance and libraries
Python 3.11+ (pin supported version), Streamlit, SQLAlchemy where appropriate, boto3/Athena connector, psycopg, PyODBC for MSSQL, official MaxCompute SDK, PyArrow and/or Polars where useful, Pydantic/JSON Schema, pytest, SQLite. Use streaming, bounded batches, controlled concurrency, timeouts, and retries. Spark is optional and isolated. Do not load entire large tables into pandas/Streamlit memory.

## 10. Security
Read-only business-table permissions; parameterized filters; validated identifiers; credentials only in environment, Streamlit secrets, or secret manager. Never log/export secrets or full sensitive values by default. `.env`, local DBs, logs, sensitive fixtures and reports must be gitignored. Document least privilege and masking.

## 11. Acceptance criteria
1. Athena → PostgreSQL can be selected and run end-to-end; no manual S3 bucket selection in primary flow.
2. Independent provider/engine selectors support AWS → AWS and any implemented provider combination.
3. Metadata discovery and connection testing work.
4. Schema/count/duplicate-key/NULL/value comparison produce correct tested results.
5. Missing, extra, and changed rows are distinct categories.
6. Source and target queried independently; no cross-platform join or fingerprint dependency.
7. JSON and CSV config validation and bounded multi-job execution work.
8. Run survives browser refresh, session expiry, and app restart; resume skips committed partitions and does not duplicate discrepancies.
9. Reports export from persisted results.
10. No hardcoded credentials or false claims about unimplemented connectors.
11. Tests cover normalization, partition boundaries, duplicate keys, all discrepancy classes, interruption/resume/idempotency, and reports.
12. README and docs provide setup, permissions, usage, configuration, testing, and troubleshooting.

## 12. Definition of done
A rendered UI alone is not done. Deliver a tested end-to-end validation path, durable resume, actionable results, exportable reports, secure configuration, and complete documentation. Clearly label any connector or capability not implemented.
