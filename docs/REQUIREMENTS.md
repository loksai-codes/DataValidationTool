# Requirements Specification

## 1. Project Objective

Build a reusable, high-performance application that validates data migrated between cloud data lakes and relational target systems.

- **Initial Source System**: Amazon S3 + Amazon Athena
  - S3 bucket: `loksai-migration-validator-960607644797`
  - Parquet object: `s3://loksai-migration-validator-960607644797/raw-data/customers/part-000.parquet`
  - AWS region: `ap-south-1`
  - Athena database: `migration_validator`
  - Athena table: `migration_validator.customers`
- **Initial Destination System**: PostgreSQL 18.6
  - Host: `localhost:5432`
  - Database: `migration_validator`
  - Table: `public.customers`
- **Dataset Size**: 100,000 customer records.

---

## 2. Functional Requirements

1. **Pluggable Architecture**: Modularity permitting future connector additions (Snowflake, BigQuery, Redshift, MySQL) without modifying validation logic.
2. **Schema Validation**: Compare columns, data types, compatibility, and mappings.
3. **Dynamic Record Counts**: Query record counts dynamically without hardcoded assumptions.
4. **Key Validation**: Detect duplicate keys, NULL keys, distinct key counts, and boundary parity for single or composite keys.
5. **NULL Parity**: Column-by-column NULL comparison, distinguishing NULL from empty strings `""`.
6. **Numeric Aggregates**: Compute `SUM`, `AVG`, `MIN`, `MAX`, and `COUNT(DISTINCT)` with configurable absolute and relative tolerances.
7. **Record-Level Comparison**: Key-based streaming comparison utilizing deterministic canonical serialization and SHA-256 fingerprints to identify missing rows, extra rows, and changed column values.
8. **Data Quality Checks**: Configurable rules (e.g. email format and duplicate occurrences) kept strictly separate from migration infrastructure consistency.
9. **Metadata Isolation**: Dedicated schema `validator_meta` in PostgreSQL to store validation history, check outcomes, and discrepancy logs without touching customer records.
10. **Reports & Exports**: Multi-format audit exports (JSON, CSV, HTML) with no secrets exposed.

---

## 3. Non-Functional Requirements

1. **Performance**: Bounded memory footprint. Execution across 100,000 records in under 30 seconds.
2. **Security**: Read-only validation queries on customer data; environment-based secrets; automated log masking; parameterized SQL.
3. **Enterprise UI**: Dark mission-control theme built exclusively with Streamlit (no React or separate backend).
4. **Testability**: Comprehensive pytest suite (unit and live integration tests) and Ruff linting compliance.
