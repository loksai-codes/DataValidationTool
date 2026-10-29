# User Guide

This guide walks through using the Streamlit Data Validation Platform.

---

## 1. Application Overview

Launch the Streamlit app:
```powershell
streamlit run app.py
```

The application consists of 5 dedicated pages accessible via the sidebar:
1. **Dashboard (`app.py`)**: System overview, execution KPI cards, recent runs, and connector status matrix.
2. **Connections (`pages/1_Connections.py`)**: Manage named connections, configure endpoints, and test connectivity.
3. **Compare Tables (`pages/2_Compare_Tables.py`)**: Configure two-column source/target endpoints, select unique keys, choose partition strategy, and launch validation runs.
4. **Run Progress (`pages/3_Run_Progress.py`)**: Real-time progress monitoring, partition status breakdown, and cancellation/resume triggers.
5. **Results (`pages/4_Results.py`)**: Detailed discrepancy inspector with categorized tabs (Missing, Extra, Changed, Keys/Nulls) and offline report downloads.
6. **History (`pages/5_History.py`)**: Filter and review past executions, resume interrupted runs, and export audit summaries.

---

## 2. Step-by-Step Validation Flow

### Step 1: Configure Connections
- Open `1_Connections.py`.
- For AWS Athena, enter AWS Region, Athena Workgroup, and S3 query result location.
- For PostgreSQL, enter Host, Port, Database, Username, and Password.
- Click **Test Connection** to verify credentials and connectivity before proceeding.

### Step 2: Compare Setup
- Open `2_Compare_Tables.py`.
- Select your Source provider, engine, named connection, database, schema, and table.
- Select your Target provider, engine, named connection, database, schema, and table.
- Select your **Unique Key Column(s)** (auto-suggested from primary key metadata).
- Choose your **Partition Strategy**:
  - `Key Range`: Slices numeric IDs into non-overlapping chunks.
  - `Date Range`: Slices date/timestamp columns into daily or monthly intervals.
  - `Keyset Pagination`: For alphanumeric IDs or unknown ranges.
  - `Bounded Full Scan`: Single partition for smaller tables.
- Adjust **Normalization Rules** (e.g. decimal tolerance, timestamp timezone, whitespace trimming).

### Step 3: Preflight Validation & Launch
- Click **Preflight Validation** to ensure both tables are accessible and key columns exist.
- Click **Start Validation Run**. The coordinator initializes checkpoints and begins streaming partition batches.

### Step 4: Progress & Resumption
- Monitor partition progress on `3_Run_Progress.py`.
- If an execution is paused or interrupted due to a network blip or app restart:
  - Open **History** or **Run Progress**.
  - Click **Resume Interrupted Run**.
  - Already committed partitions are skipped; execution resumes only for unfinished chunks.

### Step 5: Discrepancy Inspection & Export
- Open `4_Results.py`.
- Inspect differences organized by category:
  - **Missing in Target**: Keys present in source but absent in target.
  - **Extra in Target**: Keys present in target but absent in source.
  - **Field Mismatches**: Keys present on both sides with differing field values (showing column name, source value, target value).
  - **Keys & Nulls**: Duplicate key occurrences and unexpected null keys.
- Click **Export CSV**, **Export JSON**, or **Export HTML** to download comprehensive audit reports generated directly from SQLite storage without re-querying databases.
