# Security & Permission Architecture

The Data Migration Validator adheres to the principle of least privilege, strict read-only execution on customer tables, and zero plaintext secret exposure.

---

## 1. Credential Management & Secret Masking

- **Environment-Based Configuration**: Secrets (PostgreSQL passwords, AWS access keys) are never hardcoded. They are loaded dynamically from environment variables or a local `.env` file.
- **Pydantic `SecretStr`**: PostgreSQL password is encapsulated in `pydantic.SecretStr`, ensuring that `repr()`, `str()`, or `model_dump()` automatically redact sensitive values to `**********`.
- **Log Sanitization Filter**: A custom `SecretMaskingFilter` intercepts all log records before emission to standard output or log files. Regex filters sanitize connection strings (`:password@`) and literal secrets.
- **Report Redaction**: Exported JSON, CSV, and HTML reports contain only hostnames, databases, and table identifiers; passwords and tokens are never included.

---

## 2. Read-Only Query Constraints & SQL Injection Protection

- **Non-Destructive Operations**: The application never executes `INSERT`, `UPDATE`, `DELETE`, `DROP`, or `TRUNCATE` against customer tables (`public.customers`). All validation operations utilize `SELECT` statements.
- **Safe Identifier Quoting**: All schema names, table names, and column identifiers are passed through `_quote_ident()` which strips and escapes double-quote characters (`"` -> `""`) to prevent identifier injection.
- **Parameterized SQL**: All dynamic predicates (such as keyset pagination limits and cursor bounds) use SQLAlchemy bind parameters (`:last_key`, `:limit`).

---

## 3. Minimum Permissions Matrix

### 3.1. AWS IAM Permissions Policy (Source)

To run validation queries via Amazon Athena and discover Glue tables, the following identity-based policy should be attached to the IAM user/role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AthenaQueryExecution",
      "Effect": "Allow",
      "Action": [
        "athena:StartQueryExecution",
        "athena:GetQueryExecution",
        "athena:GetQueryResults",
        "athena:StopQueryExecution",
        "athena:GetWorkGroup"
      ],
      "Resource": [
        "arn:aws:athena:ap-south-1:960607644797:workgroup/primary",
        "arn:aws:athena:ap-south-1:960607644797:datacatalog/*"
      ]
    },
    {
      "Sid": "GlueCatalogRead",
      "Effect": "Allow",
      "Action": [
        "glue:GetDatabase",
        "glue:GetDatabases",
        "glue:GetTable",
        "glue:GetTables",
        "glue:GetPartitions"
      ],
      "Resource": [
        "arn:aws:glue:ap-south-1:960607644797:catalog",
        "arn:aws:glue:ap-south-1:960607644797:database/migration_validator",
        "arn:aws:glue:ap-south-1:960607644797:table/migration_validator/*"
      ]
    },
    {
      "Sid": "S3SourceAndResultAccess",
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::loksai-migration-validator-960607644797",
        "arn:aws:s3:::loksai-migration-validator-960607644797/*"
      ]
    }
  ]
}
```

*Note on S3 Direct Engine*: If Athena IAM execution is withheld, the validator seamlessly operates using the `s3:GetObject` permission on the Parquet object without requiring administrator permissions.

### 3.2. PostgreSQL Role Permissions (Destination)

The application requires `SELECT` permissions on customer data and DDL/DML permissions within the dedicated `validator_meta` metadata schema:

```sql
-- Read-only on source customer data
GRANT USAGE ON SCHEMA public TO validator_user;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO validator_user;

-- Metadata isolation schema
CREATE SCHEMA IF NOT EXISTS validator_meta;
GRANT USAGE, CREATE ON SCHEMA validator_meta TO validator_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA validator_meta TO validator_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA validator_meta TO validator_user;
```
