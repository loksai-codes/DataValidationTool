# Connector Support Matrix & Capability Guide

The platform uses a modular connector interface where providers and engines are independent dimensions. Only connectors that have complete implementations and automated test suites are enabled in the execution flow.

---

## 1. Support Status Matrix

| Provider | Engine | Status | Connection Testing | Metadata Discovery | Batched Streaming | Read-Only Guard |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| **AWS** | **Athena** | 🟢 **Implemented & Tested** | Yes | Yes (Glue / PyAthena) | Yes | Yes |
| **AWS** | **PostgreSQL** | 🟢 **Implemented & Tested** | Yes | Yes (Information Schema) | Yes | Yes (`READ ONLY`) |
| **Other** | **PostgreSQL** | 🟢 **Implemented & Tested** | Yes | Yes (Information Schema) | Yes | Yes (`READ ONLY`) |
| **Other** | **Synthetic** | 🟢 **Implemented & Tested** | Yes | Yes (SQLite) | Yes | Yes |
| **AWS / Other** | **MySQL** | ⚪ **Planned** | Interface defined | Pending driver tests | Pending | Pending |
| **Azure / Other**| **MSSQL** | ⚪ **Planned** | Interface defined | Pending driver tests | Pending | Pending |
| **Alibaba** | **MaxCompute** | ⚪ **Planned** | Interface defined | Pending driver tests | Pending | Pending |

---

## 2. Implemented Connectors Details

### AWS Athena (`data_validator.connectors.aws_athena`)
- **Query Engine**: Amazon Athena Engine v3.
- **Connection Configuration**: Configures S3 query output staging directory (`s3_staging_dir`) and workgroup directly inside connection settings. This avoids cluttering table comparison selection with bucket picking.
- **Discovery**: Automatically scans Glue Catalog databases, tables, partition keys, and column data types.
- **Streaming**: PyAthena cursor with paginated batch fetching.

### PostgreSQL (`data_validator.connectors.postgresql`)
- **Driver**: `psycopg` (v3) binary.
- **Safety**: Automatically issues `SET TRANSACTION READ ONLY` on each connection/session.
- **Discovery**: Queries PostgreSQL `information_schema.columns`, `information_schema.tables`, `information_schema.table_constraints`, and `pg_class` for exact schemas and estimated row counts.
- **Streaming**: Server-side cursors or batched client fetch.

### Synthetic In-Memory (`data_validator.connectors.synthetic`)
- **Engine**: SQLite in-memory or local database.
- **Use Cases**: Unit tests, benchmark validations, fault-injection simulations, and interactive demo runs.
- **Fault Injection**: Supports simulating fetch errors, latency, and partial partition failures for crash-recovery validation.
