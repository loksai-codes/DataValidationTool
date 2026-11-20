# User Guide & Operating Manual

Welcome to the **Data Migration Validator** Mission Control. This guide provides a step-by-step walkthrough of the validation lifecycle across all 7 user interface pages.

---

## Page 1: Overview
**Goal**: Monitor real-time telemetry, connection statuses, and previous validation runs.

1. **Connector Health Banner**: Displays status for Amazon Athena/S3 and PostgreSQL.
2. **System KPIs**:
   - Total Validation Runs
   - Passed Runs
   - Failed Runs
   - Records Validated
   - Discrepancies Count
3. **Execution History**: Click any previous run to review its duration, checks, and outcome.

---

## Page 2: Connections
**Goal**: Verify and configure database credentials and AWS profiles.

1. **Source Tab (Amazon Athena & S3)**:
   - Verify AWS Region (`ap-south-1`) and Profile (`personal-dev`).
   - Confirm Athena database (`migration_validator`) and workgroup (`primary`).
   - Click **⚡ Test Athena & S3 Connection**.
2. **Destination Tab (PostgreSQL)**:
   - Verify Host (`localhost`), Port (`5432`), Database (`migration_validator`), and Username (`postgres`).
   - Enter your password in the masked input field.
   - Click **⚡ Test PostgreSQL Connection**.

---

## Page 3: Dataset Explorer
**Goal**: Discover schemas, align table selections, and map columns.

1. **Table Discovery**:
   - Source: Select `migration_validator` database and `customers` table.
   - Destination: Select `public` schema and `customers` table.
   - Click **🔄 Refresh Schema Information** if tables were recently migrated.
2. **Column Schemas**:
   - Review column names, data types, and nullability side-by-side.
3. **Field Mappings**:
   - The system automatically pairs columns with matching names.
   - Adjust any custom mappings or select `<IGNORE / DO NOT MAP>` if necessary.
4. **Business Key Selection**:
   - Select primary or composite key columns (default: `customer_id`).
5. **Compatibility Analysis**:
   - Review automated type checking (e.g., verifying `string` -> `text` and `double` -> `double precision`).

---

## Page 4: Configure Validation
**Goal**: Tailor rule selection, numeric tolerances, and dataset scope.

1. **Active Validation Rules**:
   - Toggle rules: Schema Compatibility, Record Count Parity, Key Uniqueness, NULL Distribution, Numeric Aggregates, Distinct Value Counts, Record-Level Fingerprints, and Data Quality Rules.
2. **Tolerances & Batches**:
   - Set absolute financial tolerance (default: `0.01`).
   - Set relative tolerance (default: `0.0001`).
   - Choose comparison batch size (default: `5000`).
3. **Validation Scope**:
   - Select **Full Dataset (100,000+ Records)** for complete migration sign-off, or **Sampled Validation** for fast sanity checks.
4. **Launch**:
   - Click **🚀 Run Validation Suite**. Real-time progress updates through each stage.

---

## Page 5: Results
**Goal**: Review validation decisions and check-by-check breakdown.

1. **Executive Status Banner**:
   - Displays overall status: `PASSED`, `FAILED`, `WARNING`, `INCONCLUSIVE`, or `CANCELLED`.
2. **KPI Metric Tiles**:
   - Records Evaluated, Passed Checks, Failed Checks, Discrepancies, Latency.
3. **Visual Analytics**:
   - Donut chart of check outcome distributions.
   - Horizontal bar chart of execution latency by check.
4. **Check Breakdown Table**:
   - Inspect individual check metrics, differences, tolerances, and error details.

---

## Page 6: Discrepancy Explorer
**Goal**: Audit and drill down into specific record divergences.

1. **Discrepancy Breakdown**:
   - View counts by category: `MISSING_IN_DEST`, `EXTRA_IN_DEST`, `VALUE_MISMATCH`.
2. **Filter & Paginate**:
   - Select discrepancy type filter.
   - Select page size (25, 50, 100, 250) to page through millions of records without loading the entire dataset into memory.
3. **Export CSV**:
   - Click **📥 Download Current Discrepancies (CSV)** for ticketing or audit trails.

---

## Page 7: Reports and History
**Goal**: Generate, preview, and download compliance audit artifacts.

1. **Export Formats**:
   - **📦 Download JSON Report**: Complete machine-readable audit report without credentials.
   - **📊 Download CSV Discrepancies**: Tabular list of record differences.
   - **🌐 Download HTML Report**: Self-contained executive report with dark styling, charts, and tables.
2. **Embedded HTML Preview**:
   - Live interactive preview of the audit report.
3. **Historical Runs Table**:
   - Complete audit trail of all previous runs with timestamps and durations.
