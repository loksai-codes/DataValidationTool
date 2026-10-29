# MASTER BUILD PROMPT — Streamlit Data Validation Platform

Act as a senior Python engineer. Read `docs/PRD.md`, `docs/architecture.md`, `docs/implementation-plan.md`, and all example configs first. They are authoritative. Build the working application end to end; do not redesign or replace this architecture.

## Non-negotiable
- Streamlit UI only. No React, Next.js, or FastAPI.
- Primary selection is provider → engine → connection → database/catalog → schema → table. Do not make S3 bucket selection the primary workflow; configure Athena workgroup/query-result settings inside its connection.
- Source and target are independent. Support AWS → AWS and cross-provider combinations only when the connectors are genuinely implemented.
- First real end-to-end connector pair: AWS Athena → PostgreSQL.
- Core comparison uses unique keys and actual field values, not fingerprints/hashes. No cross-platform SQL join.
- Keep connectors, comparison, persistence/checkpointing, execution, metadata discovery, config, UI and reporting separate.
- Durable resume after browser refresh, Streamlit rerun/session expiry, process interruption and app restart is mandatory. `st.session_state` is never the sole run store.
- JSON config for one job; CSV manifest for multiple jobs; bounded concurrency.
- Read-only against business tables. No hardcoded secrets, no fake success, no empty placeholder functions.

## First steps
1. Inspect existing repository and preserve useful work that matches the requirements.
2. Map PRD acceptance criteria to modules and tests.
3. Implement the app, not just plans/scaffolding.
4. If making a major rewrite, explain the files and rationale first and keep to this architecture.

## Deliverables
Complete README, PRD, architecture, implementation plan, user guide, configuration reference, connector support matrix, security guide, troubleshooting guide, testing guide, limitations, Streamlit app/pages, package under `src/data_validator`, versioned config models/schema, SQLite repository and migrations, sample JSON/CSV, `.env.example`, `.gitignore`, dependency config, unit/integration tests and synthetic fixtures.

## UI
Clean, minimal, two-column Source and Target setup. Provider and engine dropdowns are independent; engine list depends on provider and actual support. Add named connections, Test Connection, metadata discovery, database/catalog/schema/table dropdowns, refresh metadata, and fetched-at display. Keep connection settings and secrets out of the main table-selection flow. Never reveal secrets. Show unsupported connector options as Planned/Unavailable, not functional.

Pages: Compare Setup, Connections, Run Progress, Results, History. Configure checks and partition options without overwhelming users.

## Validation
Implement schema, row count, duplicate-key, NULL, data-level actual-value comparison and optional aggregates. Support composite unique keys, structured filters, ignored columns, normalization, and explicit decimal/timestamp tolerances. Apply equivalent filters to both sides. Parameterize values and validate identifiers.

Classify records correctly:
- key only in source → missing in target
- key only in target → extra in target
- key on both sides with differing compared values → changed record with field-level details
- key and compared values agree → match
Detect duplicate/null keys before normal key-based reconciliation; never silently collapse duplicates.

Use deterministic, non-overlapping, complete partitions: key range, date range, keyset pagination, supported native partitions, or explicitly permitted bounded full scan. Handle null partition values, empty tables, gaps, skew, boundaries and time zones. Reject unsafe strategies.

## Durable resume
SQLite by default behind a repository abstraction. Persist run/config snapshot without secrets, schema snapshots, checks, partitions/cursors, counters, discrepancy rows, retry counts, sanitized errors, event log, timestamps and report references.
- Commit discrepancy rows and partition completion transactionally.
- Stable IDs and uniqueness constraints make retries idempotent.
- Leases prevent two workers from processing the same partition.
- Recover stale Running states as Interrupted.
- Resume only incomplete/failed partitions; skip committed partitions.
- Re-resolve credentials on resume without losing progress if unavailable.
- Cancellation at batch/partition boundaries.
- Add an automated test simulating partial completion, interruption, repository/app restart, resume, skipped completed partitions, and no duplicate discrepancies.
- Document local SQLite's dependence on retaining the file; shared multi-machine deployment needs shared durable storage.

## Connectors
Define a common interface for connection test, metadata discovery, table metadata, safe read-only query, batched fetch/cursor resume, capabilities and cleanup. Use connector-specific dialects and typed query specs. Implement Athena and PostgreSQL first, including metadata discovery. For Athena, configure region/workgroup/query result output in named connection settings; do not make users pick a bucket as the first step. Add MySQL/MSSQL/MaxCompute only when functional and tested; otherwise mark planned.

## Config, multi-job execution and libraries
Version config with Pydantic or JSON Schema. JSON contains a complete job. CSV is a simple multi-job manifest and can reference a JSON profile for complex settings. Configs refer to connection IDs, never passwords. Validate all jobs before launch. Bound per-job and cross-job concurrency. Use PyArrow/Polars where useful; don't load large datasets into memory. Spark is optional and isolated behind a capability check.

## Reporting and metadata
Discover catalogs/databases, schemas, tables, columns/types/nullability/keys and supported stats; record retrieval time and capabilities. Never invent unavailable metadata. Persist structured results. Export CSV, JSON and HTML from saved results without rerunning validation. Paginate large result sets and mask sensitive values by default.

## Engineering quality
Pin/constrain dependencies, type-hint business code, document supported Python, use pytest, structured redacted logging, timeouts, bounded retries and least-privilege instructions. Add tests for every discrepancy category, normalization, partition coverage, config validation, interruption/resume/idempotency, report exports and synthetic end-to-end workflow. Run tests and fix failures before claiming success. No secrets in source, logs, configs, fixtures, reports or screenshots.

## Required final report
State implemented features, final directory tree, exact Windows PowerShell and Unix setup/run commands, secure connection setup, synthetic demo steps, Athena→PostgreSQL steps, resume procedure, test commands and actual test outcomes, connector support matrix (Implemented/Tested/Planned), known limitations, and incomplete items. Never claim unimplemented functionality works.
