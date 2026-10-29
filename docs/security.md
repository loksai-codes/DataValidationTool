# Security Architecture & Best Practices

The platform is designed around strict least-privilege, zero-trust credential handling, and read-only database operations.

---

## 1. Zero Secret Ingestion

- **Decoupled Job Configuration**: Job configurations (`ValidationJobConfig`) reference only named `connection_id` strings (e.g. `aws_analytics`), never raw passwords, API keys, or AWS access tokens.
- **Durable Store Sanitization**: The SQLite metadata repository (`runs`, `partitions`, `event_logs`) stores only secret-free configuration snapshots.
- **Redaction Pipeline**:
  - `data_validator.security.redaction.redact_secrets_dict` scans and sanitizes credentials before logging or display.
  - Value masking (`mask_value`) masks sensitive emails, names, and card/account numbers in discrepancy reports by default.

---

## 2. SQL Injection Prevention & Identifier Quoting

- Dynamic queries quote all catalog, schema, table, and column identifiers through `data_validator.security.identifiers.quote_identifier` using engine-specific dialects (`"` for PostgreSQL/Athena, `` ` `` for MySQL, `[]` for MSSQL).
- Disallows dangerous SQL injection characters (semicolons, comments `--` or `/* */`, null bytes).

---

## 3. Read-Only Database Privileges

The validation platform requires only read access against business tables:
- **PostgreSQL**:
  ```sql
  GRANT CONNECT ON DATABASE migration_validator TO validator_user;
  GRANT USAGE ON SCHEMA public TO validator_user;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO validator_user;
  ```
  Every session issues `SET TRANSACTION READ ONLY;`.
- **AWS Athena**:
  - `athena:GetWorkGroup`
  - `athena:StartQueryExecution`, `athena:GetQueryExecution`, `athena:GetQueryResults`
  - `glue:GetDatabase`, `glue:GetDatabases`, `glue:GetTable`, `glue:GetTables`
  - `s3:GetObject`, `s3:PutObject`, `s3:GetBucketLocation` on the staging bucket.

---

## 4. Gitignore & File Hygiene

The `.gitignore` explicitly excludes:
- `.env` and `.streamlit/secrets.toml`
- `*.db`, `*.sqlite`, `*.sqlite3`
- `reports/*`
- `configs/connections.json`
