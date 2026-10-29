# Troubleshooting Guide

Common issues, diagnostic procedures, and resolution steps.

---

## 1. Connection & Authentication Issues

### Problem: `PreflightValidationError: Source/Target connection '...' not found in secrets`
- **Cause**: The connection ID referenced in the job configuration is not configured in `configs/connections.json`, Streamlit secrets, or environment variables.
- **Resolution**:
  - Open `pages/1_Connections.py` and save the connection with that exact ID.
  - Or define environment variables matching `CONN_<CONNECTION_ID>_...`.

### Problem: Athena connection check fails with `AccessDeniedException` or `WorkGroup not found`
- **Cause**: The IAM role/credentials lack Athena permissions (`athena:GetWorkGroup`, `athena:StartQueryExecution`), or the configured S3 staging bucket is inaccessible.
- **Resolution**:
  - Check `s3_staging_dir` in connection configuration. Ensure the bucket exists and IAM permissions include `s3:GetBucketLocation`, `s3:GetObject`, `s3:PutObject`.
  - Ensure the workgroup (default `primary`) is enabled in the target AWS region.

---

## 2. Validation & Reconciliation Issues

### Problem: `Duplicate keys detected`
- **Cause**: The chosen unique key is not genuinely unique in the table dataset.
- **Resolution**:
  - Add secondary columns in `Unique Key Column(s)` to form a composite key (e.g. `['tenant_id', 'user_id']`).
  - Do not proceed with single-key reconciliation if duplicates exist, as non-unique keys cannot accurately pair rows.

### Problem: False positive field mismatches on numbers or timestamps
- **Cause**: Representation variance across engines (e.g. Postgres `NUMERIC` with trailing zeros vs Athena floating representations, or timezone differences).
- **Resolution**:
  - Adjust `Decimal Tolerance Threshold` in normalization settings (e.g. `0.01` or `0.0001`).
  - Set `Normalize Timestamps to Timezone` to `UTC` to align timezone offsets.
  - Enable `Trim String Whitespace` if text columns contain padding.

---

## 3. Resumption & Execution Issues

### Problem: Run status displays `Interrupted`
- **Cause**: The Streamlit server was restarted, or the execution process crashed during an active partition.
- **Resolution**:
  - Go to `pages/5_History.py` or `pages/3_Run_Progress.py` and click **Resume**.
  - The repository's transactional checkpoints guarantee that committed partitions remain saved; only incomplete partitions will be re-processed.

### Problem: Query timeout during large partition scan
- **Cause**: Partition chunk size is too large for the network or database query limit.
- **Resolution**:
  - Decrease `range_size` in Partition Strategy (e.g. from 25,000 to 5,000).
  - Increase `query_timeout_seconds` in execution settings.
