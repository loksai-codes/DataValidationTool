# Configuration Reference Guide

This document outlines the full specification for single-job JSON configurations and multi-job CSV manifests.

---

## 1. JSON Job Configuration Specification

Example: `configs/examples/athena_to_postgres.json`

```json
{
  "config_version": 1,
  "job_name": "athena_to_postgres_customers",
  "source": {
    "provider": "aws",
    "engine": "athena",
    "connection_id": "aws_analytics",
    "catalog": "AwsDataCatalog",
    "database": "migration_validator",
    "schema": null,
    "table": "customers"
  },
  "target": {
    "provider": "aws",
    "engine": "postgresql",
    "connection_id": "postgres_dev",
    "catalog": null,
    "database": "migration_validator",
    "schema": "public",
    "table": "customers"
  },
  "comparison": {
    "unique_keys": [
      "customer_id"
    ],
    "filters": [],
    "checks": {
      "schema": true,
      "row_count": true,
      "duplicate_keys": true,
      "nulls": true,
      "data_level": true,
      "aggregates": false
    },
    "partition": {
      "strategy": "key_range",
      "column": "customer_id",
      "range_size": 25000,
      "include_null_partition": true
    },
    "ignored_columns": [],
    "normalization": {
      "trim_strings": false,
      "case_sensitive_strings": true,
      "decimal_tolerance": "0",
      "timestamp_timezone": "UTC"
    }
  },
  "execution": {
    "mode": "local",
    "batch_size": 5000,
    "max_workers": 4,
    "retry_attempts": 3,
    "query_timeout_seconds": 300,
    "lease_duration_seconds": 60
  },
  "reporting": {
    "formats": [
      "json",
      "csv",
      "html"
    ],
    "mask_sensitive_values": true,
    "output_dir": "reports"
  }
}
```

### Fields Breakdown:
- **`source` / `target`**:
  - `provider`: `aws`, `other`, `azure`, `gcp`, `alibaba`.
  - `engine`: `athena`, `postgresql`, `synthetic`.
  - `connection_id`: ID pointing to secure credentials in `SecretStore`.
  - `catalog`: Optional catalog name (e.g. Athena `AwsDataCatalog`).
  - `database`: Database name.
  - `schema`: Schema name (e.g. `public` for Postgres).
  - `table`: Table name.
- **`comparison`**:
  - `unique_keys`: List of one or more columns forming the unique key for record alignment.
  - `checks`: Boolean flags for `schema`, `row_count`, `duplicate_keys`, `nulls`, `data_level`, `aggregates`.
  - `partition`:
    - `strategy`: `key_range`, `date_range`, `keyset_pagination`, `bounded_full_scan`.
    - `column`: Partition column name.
    - `range_size`: Chunk size (e.g. 25,000 rows or numeric range).
    - `include_null_partition`: Adds a dedicated partition for `WHERE column IS NULL`.
  - `normalization`:
    - `trim_strings`: Strip whitespace.
    - `case_sensitive_strings`: False to compare lowercase.
    - `decimal_tolerance`: Numeric tolerance threshold (e.g. `"0.01"`).
    - `timestamp_timezone`: Timezone to convert datetime values before comparing (default: `"UTC"`).
- **`execution`**:
  - `mode`: `"local"` (Spark optional if configured).
  - `batch_size`: Streaming batch fetch size.
  - `max_workers`: Max concurrent worker threads.
  - `lease_duration_seconds`: Lease timeout for partition execution.

---

## 2. Multi-Job CSV Manifest Specification

Example: `configs/examples/jobs.csv`

```csv
job_name,source_engine,source_connection_id,source_database,source_schema,source_table,target_engine,target_connection_id,target_database,target_schema,target_table,unique_key,config_file
customers,athena,aws_analytics,migration_validator,,customers,postgresql,postgres_dev,migration_validator,public,customers,customer_id,athena_to_postgres.json
```

- Each row defines an independent table validation task.
- `config_file` optionally references a JSON profile to inherit complex normalization, partition, or check settings.
