# Data Migration Validation Platform

Production-grade, Streamlit-first cross-database data validation platform featuring partition-based actual-value comparison, durable resume, and zero cross-platform SQL joins.

---

## 🌟 Key Highlights

- **Streamlit UI Only**: Clean, reactive 5-page dashboard.
- **Independent Source & Target**: Connect any supported provider/engine combination (e.g. AWS Athena → PostgreSQL, AWS → AWS, or Synthetic).
- **Core Comparison by Unique Keys**: Matches actual field values; no hash collisions or cross-database join dependencies.
- **Deterministic Partitioning**: Supports Key Range, Date Range, Keyset Pagination, and Bounded Full Scans with explicit NULL-value partitions.
- **Durable Checkpointing & Resume**: SQLite WAL repository tracks runs, checks, partitions, leases, and discrepancies. Resuming skips committed partitions with zero duplicate discrepancies.
- **Multi-Job Execution**: JSON configurations for individual jobs; CSV manifests for bounded multi-job batch runs.
- **Zero Credentials in State**: Secrets are resolved on demand from local secure stores or environment variables, never committed to configs, databases, or logs.
- **Offline Report Export**: Instant export to CSV, JSON, and interactive self-contained HTML directly from saved results without re-executing queries.

---

## 🏗️ Architecture Overview

```text
Streamlit UI (Pages: Compare Setup, Connections, Run Progress, Results, History)
   │
   ▼
Application Services & Run Coordinator (data_validator.app_services / execution)
   ├─ Config Validation & Preflight Checks
   ├─ Connector Registry (Implemented vs Planned)
   ├─ Durable SQLite Repository (WAL mode, transactional partition commits)
   └─ Report Service (CSV / JSON / HTML)
        │
   ┌────┴───────────────────────────┐
   ▼                                ▼
Source Connector (e.g. Athena)    Target Connector (e.g. PostgreSQL)
   └────────────────┬───────────────┘
                    ▼
          Comparison Engine (data_validator.comparison)
          ├─ Schema Compatibility Check (Blocking / Warning / Compatible)
          ├─ Row Count & Aggregate Checks
          ├─ Key Uniqueness & NULL Key Detection
          ├─ Deterministic Partition Planner
          ├─ Type Normalization (Decimals, Timestamps, Strings, NULLs)
          └─ Actual Value Comparator (Missing, Extra, Changed, Duplicates)
```

---

## 📊 Connector Support Matrix

| Provider | Engine | Status | Capabilities / Notes |
| :--- | :--- | :--- | :--- |
| **AWS** | **Athena** | 🟢 **Implemented & Tested** | S3 staging & workgroup configured inside connection; Glue metadata discovery; read-only streaming. |
| **AWS / Other** | **PostgreSQL** | 🟢 **Implemented & Tested** | `psycopg3` engine; `information_schema` discovery; `SET TRANSACTION READ ONLY`; batched cursors. |
| **Other** | **Synthetic** | 🟢 **Implemented & Tested** | In-memory/SQLite engine for local testing, failure simulation, and interactive demos. |
| **AWS / Other** | **MySQL** | ⚪ **Planned** | Interface defined; enabled upon integration test completion. |
| **Azure / Other**| **MSSQL** | ⚪ **Planned** | Interface defined; enabled upon integration test completion. |
| **Alibaba** | **MaxCompute** | ⚪ **Planned** | Interface defined; enabled upon integration test completion. |

---

## 🚀 Quickstart Guide

### 1. Installation

**Windows PowerShell:**
```powershell
# Clone and enter workspace
cd C:\Users\itslo\OneDrive\Desktop\data_validation_project

# Create virtual environment (Python 3.11+)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install package and dependencies
pip install -e .
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

### 2. Launch the Streamlit Application

```powershell
streamlit run app.py
```
Open your browser to `http://localhost:8501`.

---

### 3. Run the Synthetic Demo

Run a complete automated validation across 100 customer records with intentional discrepancies (missing records, changed balances, duplicate keys, extra target records) and inspect generated HTML/CSV/JSON reports:

```powershell
python scripts/run_synthetic_demo.py
```

Generated reports will be located in `reports/demo/`.

---

### 4. Run Multi-Job Batch Manifest

Execute a batch manifest containing multiple jobs with bounded concurrency:

```powershell
python scripts/run_batch_manifest.py configs/examples/synthetic_batch.csv --max-workers 2
```

---

## ☁️ AWS Athena → PostgreSQL Setup Guide

### 1. Configure Athena in Named Connections (`pages/1_Connections.py`)
- **Connection ID**: `aws_analytics`
- **Provider**: AWS
- **Engine**: Athena
- **AWS Region**: e.g., `ap-south-1` or `us-east-1`
- **Athena Workgroup**: `primary`
- **S3 Staging Directory**: `s3://your-athena-query-results-bucket/staging/`
- **Credentials**: Resolved automatically from your AWS profile (`~/.aws/credentials`) or IAM role.

> [!NOTE]
> Setting the S3 query result output location in the connection configuration ensures that table selection in the primary flow remains clean and database-centric, without requiring bucket navigation.

### 2. Configure PostgreSQL in Named Connections
- **Connection ID**: `postgres_dev`
- **Host**: `localhost` (or RDS endpoint)
- **Port**: `5432`
- **Database**: `migration_validator`
- **Username / Password**: configured securely.

### 3. Run Compare Tables (`pages/2_Compare_Tables.py`)
1. Select Source: AWS → Athena → `aws_analytics` → `migration_validator` → `customers`.
2. Select Target: AWS / Other → PostgreSQL → `postgres_dev` → `migration_validator` → `customers`.
3. Click **Preflight Validation** to verify table existence and key accessibility.
4. Click **Start Validation Run**. Monitor progress on `pages/3_Run_Progress.py` and inspect discrepancies on `pages/4_Results.py`.

---

## 🔄 Durable Resume & Interruption Handling

1. **Transactional Commits**: Partition results and their detected discrepancies are committed atomically to SQLite in WAL mode.
2. **Worker Leases**: Partitions are locked with leases preventing duplicate worker execution.
3. **Stale Recovery**: Stale runs or expired leases are automatically transitioned to `Interrupted` / `Failed`.
4. **Idempotent Retries**: Unique database constraints `(run_id, partition_id, discrepancy_type, key_values, column_name)` ensure duplicate discrepancies are never inserted upon resumption.
5. **Resume Workflow**:
   - In the Streamlit UI, open **Run Progress** or **History** and click **Resume**.
   - Or via Python API: `RunService().resume_validation(run_id)`.
   - Only incomplete or failed partitions will be queried; already committed partitions are skipped entirely.

---

## 🧪 Automated Testing

Run the complete test suite with `pytest`:

```powershell
pytest tests -v
```

### Test Coverage Highlights:
- `tests/unit/test_config.py`: SQL injection prevention, identifier quoting, secret redaction, and schema validation.
- `tests/unit/test_comparison.py`: Normalization (decimals, timestamps, strings), schema compatibility, row counts, duplicate/null keys, and field mismatch classification.
- `tests/unit/test_persistence.py`: SQLite WAL durability, lease management, transactional commits, and idempotency.
- `tests/unit/test_connectors.py`: Connector registry, PostgreSQL mock tests, Athena mock tests, and synthetic engine.
- `tests/unit/test_reporting.py`: CSV, JSON, and HTML report exports generated directly from persisted results.
- `tests/integration/test_resume_and_e2e.py`: Simulates partial execution, process interruption/crash, database restart, resumption, verification of skipped partitions, and zero duplicate discrepancies.
