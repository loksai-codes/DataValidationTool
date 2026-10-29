# Troubleshooting Guide & Operational Runbook

This guide addresses common issues encountered in Windows, Python, PostgreSQL, AWS, and Athena environments.

---

## 1. AWS & Amazon Athena Troubleshooting

### Issue: `An error occurred (AccessDeniedException) when calling the StartQueryExecution operation`
- **Cause**: The configured IAM user or role lacks identity-based permissions for Athena execution on the specified workgroup (`primary`).
- **Remedy**:
  1. Attach the `AmazonAthenaFullAccess` managed policy or the specific custom IAM policy documented in [`SECURITY.md`](SECURITY.md).
  2. Ensure the workgroup name in `.env` matches the actual workgroup created in the AWS region (`ap-south-1`).
  3. The validator features an automatic **S3 Direct Engine fallback** that reads the compressed Parquet dataset directly using S3 read permissions when Athena API execution is restricted.

### Issue: `Unable to locate credentials`
- **Cause**: AWS credentials are not set in environment variables or `~/.aws/credentials`.
- **Remedy**:
  1. Confirm the profile name in `.env`: `AWS_PROFILE=personal-dev`.
  2. Verify with AWS CLI: `aws sts get-caller-identity --profile personal-dev`.
  3. Check `C:\Users\<username>\.aws\credentials` contains the section `[personal-dev]`.

---

## 2. PostgreSQL Troubleshooting

### Issue: `password authentication failed for user "postgres"`
- **Cause**: Incorrect password provided in `.env` or connection string.
- **Remedy**:
  1. Verify the `POSTGRES_PASSWORD` entry in your `.env` file.
  2. Test connection with the local environment check script:
     ```powershell
     python scripts/check_environment.py
     ```
  3. If testing locally on Windows, ensure the `postgresql-x64-18` Windows service is running:
     ```powershell
     Get-Service postgresql-x64-18
     ```

### Issue: `fe_sendauth: no password supplied`
- **Cause**: Password environment variable is missing or empty.
- **Remedy**:
  Ensure `.env` contains `POSTGRES_PASSWORD=your_password` and is loaded in the working directory.

### Issue: `psql : The term 'psql' is not recognized`
- **Cause**: PostgreSQL binary directory is not in the system `PATH`.
- **Remedy**:
  Add PostgreSQL bin to your session:
  ```powershell
  $env:PATH += ";C:\Program Files\PostgreSQL\18\bin"
  ```

---

## 3. Windows & Python Environment Troubleshooting

### Issue: `ModuleNotFoundError: No module named 'migration_validator'`
- **Cause**: The package has not been installed in editable mode in the active environment.
- **Remedy**:
  Run from the project root:
  ```powershell
  pip install -e .
  ```

### Issue: Port 8501 Already in Use
- **Cause**: An existing Streamlit instance is running on port 8501.
- **Remedy**:
  Launch on an alternate port or terminate the existing instance:
  ```powershell
  streamlit run app.py --server.port=8502
  ```
