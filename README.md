# DataValidationTool — cross-cloud data migration validator

`dvtool` proves that data migrated between engines is **complete and identical**. It is built for
**AWS ⇄ Alibaba Cloud** migrations and works for any pairing of the supported engines. Every project
uses the same YAML format, checks, reports and CI exit codes.

| | AWS | Alibaba Cloud |
|---|---|---|
| OLTP | RDS / Aurora MySQL, MariaDB, RDS / Aurora PostgreSQL | RDS MySQL / PostgreSQL, PolarDB-MySQL / PolarDB-X / PolarDB-PG |
| Analytics | Redshift, Athena (Glue / S3) | MaxCompute, Hologres, AnalyticDB MySQL / PG |
| Files | S3 Parquet / CSV / JSON | OSS Parquet / CSV / JSON |

## Why this tool

* **Config-driven, no code per table.** Connections and table pairs live in one YAML file.
  `dvtool generate-tables` builds the table list from two schemas and detects primary keys.
* **Low cross-cloud transfer.** In `profile` mode each database computes key-weighted, per-bucket
  checksums of every column. Only the checksums leave the cloud. Rows are fetched only for the
  buckets that differ. A test on 300k rows with 2 differences transferred 4.8k rows instead of 600k.
* **Correct across engines.** Rows are matched by `MD5(key)` buckets instead of `ORDER BY`, so
  collation and sort-order differences can't produce fake missing rows. Values are normalized by
  type before comparison: exact decimals, UTC timestamps with a time zone per connection, and
  canonical booleans and JSON. NULL and empty string are kept distinct.
* **Complete checks.** Schema and type compatibility, row counts, NULL and duplicate keys, NULL
  counts per column, sum/avg/min/max, distinct counts, row-level differences
  (missing / extra / changed / NULL / duplicate) and custom SQL checks.
* **Ready for CI.** Reports in JSON, HTML, CSV, JUnit and Markdown. Exit codes `0` pass, `1` fail,
  `2` error, `3` bad config. Secrets come from environment variables, files, AWS Secrets Manager
  or Alibaba KMS, and are masked in logs.

## Quick start

```bash
pip install ".[mysql,postgres,aws,alibaba]"      # or ".[all]"
dvtool init migration.yaml                       # commented AWS -> Alibaba template
dvtool test-connections -c migration.yaml
dvtool generate-tables -c migration.yaml --source aws_crm:crm --target ali_crm:crm -o tables.yaml
dvtool explain  -c migration.yaml -t crm.orders  # SQL that will run, for DBA review
dvtool validate -c migration.yaml                # -> dv-reports/<run-id>/report.html
```

Minimal project:

```yaml
project: crm-aws-to-alibaba
connections:
  aws_crm: {type: aws-aurora-mysql, host: ..., database: crm, user: dv_ro, password: "${env:AWS_PW}"}
  ali_crm: {type: alibaba-polardb-mysql, host: ..., database: crm, user: dv_ro, password: "${env:ALI_PW}",
            timezone: Asia/Shanghai}
tables:
  - name: customers
    source: {connection: aws_crm, table: crm.customers}
    target: {connection: ali_crm, table: crm.customers}
    keys: [customer_id]          # optional: primary key is detected automatically
```

See [`examples/aws_to_alibaba.yaml`](examples/aws_to_alibaba.yaml) for Athena → MaxCompute,
PostgreSQL, OSS files, composite keys, renames, partition filters, tolerances and custom checks.

## Documentation

* **[`docs/DATAVALIDATOR_GUIDE.md`](docs/DATAVALIDATOR_GUIDE.md)**: install, engines, how each
  check works, full config reference, AWS ⇄ Alibaba pitfalls (time zones, MaxCompute full scans,
  Athena cost, whitelists, IAM/RAM), migration runbook, CI, extending.
* [`examples/ci/github-actions.yml`](examples/ci/github-actions.yml): a nightly validation gate.
* `Dockerfile`: container image (`docker run ... dvtool validate -c project.yaml`).

## Repository layout

```
src/datavalidator/        new engine + CLI (dvtool)
  config.py               project YAML models, connection types
  secrets.py, log.py      secret resolution and log masking
  dialects.py             SQL dialects: mysql, postgres, redshift, hologres, athena, maxcompute, duckdb, sqlite
  executors.py            DB-API pool, Athena (S3 CSV results), MaxCompute (Tunnel)
  connectors.py           catalog discovery and connection factories
  engine.py               checks, hash buckets, profile drill-down
  normalize.py, compare.py  type-aware value normalization and row diff
  runner.py, reports.py, cli.py
tests/datavalidator/      68 tests: SQLite end-to-end + emulated Athena / MaxCompute / MySQL
src/migration_validator/, app.py, pages/   legacy Streamlit UI (original Athena -> PostgreSQL demo)
```

## Tests

```bash
pip install ".[dev]"
pytest tests/datavalidator
```

## Legacy Streamlit UI

The original Streamlit app (`app.py`, `pages/`, `src/migration_validator`) still works through the
`ui` extra (`pip install ".[ui]"`, then `streamlit run app.py`). It is limited to Athena/S3 → PostgreSQL
and to a `customers`-style table. Moving it onto the new engine is planned; until then, use `dvtool`
for real migrations.
