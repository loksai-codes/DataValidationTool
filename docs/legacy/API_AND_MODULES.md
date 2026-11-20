# Module & API Reference

This document details the classes, methods, and interfaces implemented in the Data Migration Validator.

---

## 1. Connectors Layer (`migration_validator.connectors`)

### `BaseConnector` (Abstract Base Class)
Protocol defining data source and destination requirements.

- `test_connection() -> ConnectionTestResult`: Validates endpoint connectivity and latency.
- `get_databases_or_schemas() -> List[str]`: Lists catalog databases or schemas.
- `get_tables(schema_or_db: Optional[str]) -> List[str]`: Lists tables within a schema.
- `get_table_schema(table_name: str, schema_or_db: Optional[str]) -> TableSchema`: Returns column names, data types, and nullability.
- `get_record_count(table_name: str, schema_or_db: Optional[str]) -> int`: Dynamic row count.
- `get_null_counts(table_name: str, columns: List[str], schema_or_db: Optional[str]) -> Dict[str, int]`: Returns NULL counts per column.
- `get_aggregates(table_name: str, numeric_columns: List[str], schema_or_db: Optional[str]) -> Dict[str, Dict[str, float]]`: Computes SUM, AVG, MIN, MAX.
- `get_distinct_counts(table_name: str, columns: List[str], schema_or_db: Optional[str]) -> Dict[str, int]`: Computes COUNT(DISTINCT).
- `get_key_summary(table_name: str, key_columns: List[str], schema_or_db: Optional[str]) -> KeySummary`: Uniqueness and nullability evaluation.
- `fetch_records_batch(table_name: str, columns: List[str], key_columns: List[str], batch_size: int, last_key: Optional[Any], schema_or_db: Optional[str]) -> Tuple[pd.DataFrame, Optional[Any]]`: Keyset streaming pagination.
- `close() -> None`: Resource and connection cleanup.

### `PostgresConnector`
SQLAlchemy 2.x and Psycopg 3 implementation for PostgreSQL 18.
- Handles identifier quoting with `_quote_ident()`.
- Implements keyset pagination: `WHERE key > :last_key ORDER BY key ASC LIMIT :batch_size`.

### `AthenaConnector`
Boto3 and PyAthena connector for AWS Athena and S3.
- Executes queries via Athena API with polling.
- Implements S3 Direct Parquet stream execution when Athena IAM permissions are restricted.

---

## 2. Validation Engine (`migration_validator.validation`)

### `serialization.py`
- `canonical_value_str(val: Any) -> str`: Normalizes types (NULL sentinel, floats, timestamps).
- `canonical_serialize_record(row_dict: Dict[str, Any], columns: Optional[List[str]]) -> str`: Produces alphabetical key-value canonical strings.
- `compute_record_hash(row_dict: Dict[str, Any], columns: Optional[List[str]]) -> str`: Generates SHA-256 fingerprint.

### `record_comparison.py`
- `RecordComparisonEngine`:
  - `stream_compare(max_records, progress_callback, cancel_check) -> Generator`: Yields `(batch_discrepancies, stats)` in bounded memory chunks.
- `run_record_comparison_check(engine, max_records, discrepancy_sink, progress_callback, cancel_check) -> Tuple[CheckResult, int, Dict[str, int]]`:
  - Executes streaming check, pipes discrepancies to database, and produces status summary.

---

## 3. Orchestration & Metadata (`migration_validator.services` & `repositories`)

### `ValidationOrchestrator`
Coordinates full validation lifecycle across all stages:
- `execute_validation_run(source_table, dest_table, rule_config, source_db, dest_schema, progress_callback, cancel_check) -> ValidationRunSummary`

### `MetadataRepository`
Manages persistence in `validator_meta` schema:
- `initialize_schema(migration_sql_path: Optional[str]) -> bool`: Runs DDL migration idempotently.
- `record_run_start(...)`: Inserts active run record.
- `record_run_finish(...)`: Updates completed run telemetry.
- `record_checks(...)`: Persists check results.
- `record_discrepancies(...)`: Batches discrepancy records into PostgreSQL.
- `get_runs(limit: int) -> List[Dict]`: Retrieves historical execution runs.
- `get_discrepancies_for_run(run_id, discrepancy_type, limit, offset) -> List[Dict]`: Paginated query.
