# datavalidator (`dvtool`) — Cross-Cloud Migration Validation Guide

`dvtool` is a configuration-driven tool for proving that data migrated between engines — in
particular **AWS ⇄ Alibaba Cloud** — is complete and identical. One YAML project file per
migration describes connections and table pairs; the same commands, checks and reports are used
on every project.

---

## 1. Install

```bash
pip install ".[mysql,postgres,aws,alibaba]"   # pick the drivers you need
pip install ".[all]"                          # or everything (adds duckdb/pyarrow for S3/OSS files)
dvtool --help
```

| extra      | installs                         | needed for |
|------------|----------------------------------|-----------|
| `mysql`    | pymysql                          | RDS/Aurora MySQL, MariaDB, Alibaba RDS MySQL, PolarDB-MySQL/X, AnalyticDB MySQL |
| `postgres` | psycopg 3                        | RDS/Aurora PostgreSQL, Redshift, Alibaba RDS PG, PolarDB-PG, AnalyticDB PG, Hologres |
| `aws`      | boto3                            | Athena, `${aws_secret:...}` |
| `alibaba`  | pyodps, Alibaba KMS SDK          | MaxCompute, `${alibaba_kms:...}` |
| `files`    | duckdb, pyarrow                  | Parquet/CSV/JSON on S3, OSS or local disk |

A container image is provided (`Dockerfile`) for CI runners, ECS/EKS or Alibaba ACK/SAE jobs.

## 2. Quick start

```bash
dvtool init migration.yaml                         # commented AWS -> Alibaba template
dvtool test-connections -c migration.yaml          # connectivity + credentials for every connection
dvtool discover -c migration.yaml --connection aws_crm --schema crm
dvtool generate-tables -c migration.yaml --source aws_crm:crm --target ali_crm:crm -o tables.yaml
dvtool explain  -c migration.yaml -t crm.orders    # show the exact SQL for DBA review
dvtool validate -c migration.yaml                  # run, write reports, exit code for CI
```

`generate-tables` matches table names across both schemas and fills in primary keys from the
catalog, so a 300-table schema is configured in minutes. Add it to the project with
`include: [tables.yaml]`.

## 3. Supported engines (`type:` values)

| Cloud | `type` | Dialect |
|---|---|---|
| AWS | `aws-rds-mysql`, `aws-aurora-mysql`, `aws-rds-mariadb` | mysql |
| AWS | `aws-rds-postgres`, `aws-aurora-postgres` | postgres |
| AWS | `aws-redshift` | redshift |
| AWS | `aws-athena` (Glue catalog over S3) | athena |
| AWS | `aws-s3` (Parquet/CSV/JSON files) | duckdb |
| Alibaba | `alibaba-rds-mysql`, `alibaba-polardb-mysql`, `alibaba-polardb-x`, `alibaba-adb-mysql` | mysql |
| Alibaba | `alibaba-rds-postgres`, `alibaba-polardb-pg`, `alibaba-adb-pg` | postgres |
| Alibaba | `alibaba-hologres` | hologres |
| Alibaba | `alibaba-maxcompute` | maxcompute |
| Alibaba | `alibaba-oss` (Parquet/CSV/JSON files) | duckdb |
| Any | `mysql`, `postgres`, `sqlite`, `duckdb`, `local-files` | — |

Any source can be validated against any target (e.g. Athena → MaxCompute, Aurora MySQL →
PolarDB, RDS PostgreSQL → Hologres, S3 Parquet → OSS Parquet).

## 4. What gets checked

| check | how | cost |
|---|---|---|
| `schema` | column presence, renames, logical type compatibility (truncation, scale loss, tz changes) | catalog only |
| `row_count` | `COUNT(*)` with optional abs / % tolerance | ┐ |
| `keys` | NULL keys and duplicate keys on each side (single or composite) | │ one table scan |
| `nulls` | NULL count per column | │ per side, |
| `aggregates` | SUM / AVG / MIN / MAX of numeric columns, MIN / MAX of dates & timestamps | │ all in a |
| `distinct` | `COUNT(DISTINCT)` for listed columns | ┘ single query |
| `data` | row-by-row comparison (see modes below) | depends on mode |
| `custom` | your own SQL on both sides, result sets or scalars compared | your query |

Numeric columns are detected from the catalog types (no name guessing), keys come from config or
the primary key, and every check works for any table.

### Row-level comparison modes (`compare.mode`)

All modes split rows into **hash buckets**: `SUBSTR(MD5(<key text>), 1, n)`. The same expression
runs on both engines, so the same key always lands in the same bucket regardless of collation or
sort order. That gives three strategies:

* **`full`** — fetch every row (bucket by bucket, bounded memory) and compare. Exact. Transfers
  the whole table through the validator host.
* **`profile`** — each engine computes per-bucket checksums *in-database*: row count, NULL counts,
  key-weighted sums of numerics, key-weighted epoch sums of timestamps (time-zone corrected) and an
  MD5-based checksum of every string value. Only checksums cross the network. Mismatching buckets
  are subdivided (16× per level) until they are small, then only those rows are fetched and
  compared. Weighting by the row key means even values *swapped between rows* are caught.
  In tests, 300k rows with 2 differences transferred 4.8k rows instead of 600k.
* **`sample`** — full comparison of a deterministic subset of buckets (`sample_pct`). Because the
  sample is chosen by key hash, both sides sample exactly the same rows — unlike `TABLESAMPLE`
  or `RAND()`.
* **`auto`** (default) — `full` up to `full_max_rows` (2M), `profile` above.

Results classify every difference: `MISSING_IN_TARGET`, `EXTRA_IN_TARGET`, `VALUE_MISMATCH`,
`NULL_MISMATCH`, `DUPLICATE_KEY_SOURCE/TARGET`, with per-column mismatch counts and samples.

### How values are compared

Each column pair is compared under one logical type, and both sides are normalized first:

* integers / decimals → exact `Decimal` (`1.50 == 1.5`, 64-bit ids never go through float)
* floats → relative tolerance (`float_rel`, default 1e-9)
* timestamps → converted to UTC using each connection's `timezone`, truncated to
  `timestamp_precision` (`s` / `ms` / `us`)
* booleans (`true/1/'t'/tinyint(1)`), JSON (key-order independent), binary (hex) are canonicalized
* NULL and empty string are always distinct unless `normalize.empty_string_as_null: true`
* unparsable values are reported as `<unparsable:...>` instead of being silently dropped

## 5. Project file reference

```yaml
project: my-migration
parallel_tables: 4            # tables validated concurrently
output_dir: dv-reports
include: [tables.yaml]        # optional extra files with a 'tables:' list

connections:
  <name>:
    type: <see section 3>
    cloud: aws | alibaba      # shown in reports
    host / port / database / user / password / schema
    ssl: true | {sslmode: verify-full, sslrootcert: /path/ca.pem}
    timezone: UTC             # zone of NAIVE timestamps stored in this system
    session_init: ["SET ..."] # extra statements on connect
    max_connections: 4
    query_timeout_seconds: 1800
    # Athena: region, workgroup, database, s3_staging_dir, catalog, profile | access_key_id/secret
    # MaxCompute: project, endpoint, tunnel_endpoint, access_key_id/secret, hints
    # Files (aws-s3 / alibaba-oss / local-files): region, endpoint, keys, tables: {name: {path, format}}

defaults:                     # deep-merged into every table
  checks: [schema, row_count, keys, nulls, aggregates, data, custom]

tables:
  - name: unique name
    source: {connection: x, table: schema.table, filter: "dt = '2026-11-01'"}   # or query: SELECT ...
    target: {connection: y, table: schema.table, filter: "..."}
    keys: [id]                # omit -> primary key from catalog
    columns: {src_col: tgt_col}            # renames; others auto-mapped by name
    auto_map_columns: true
    exclude_columns: [etl_loaded_at]
    distinct_columns: [status]
    tags: [critical, wave1]
    fail_on_schema_warnings: false
    fail_on_extra_target_columns: false
    tolerances:
      numeric_abs: 0
      numeric_rel: 0
      float_rel: 1.0e-9
      timestamp_precision: us
      row_count_abs: 0
      row_count_pct: 0
      columns: {amount: {abs: 0.005}}
    normalize:
      trim_strings: false
      rtrim_strings: false    # CHAR(n) padding differences
      case_insensitive_columns: [email]
      empty_string_as_null: false
      ignore_columns: []
    compare:
      mode: auto              # auto | full | profile | sample | none
      full_max_rows: 2000000
      sample_pct: 5
      fetch_batch_rows: 200000
      leaf_bucket_rows: 50000
      initial_bucket_hex_digits: 2
      max_bucket_hex_digits: 5
      max_discrepancy_samples: 1000
      string_checksums: true
      temporal_checksums: true
    custom_checks:
      - name: revenue by month
        source_sql: SELECT month, SUM(amount) FROM ... GROUP BY month
        target_sql: SELECT month, SUM(amount) FROM ... GROUP BY month
        tolerance_abs: 0.01
        severity: fail        # or warn
```

**Secrets** never live in the file: `${env:VAR}`, `${env:VAR:-default}`, `${file:/path}`,
`${aws_secret:secret-id#json_key}`, `${alibaba_kms:secret-name#json_key}`. Resolved values are
masked in all log output.

## 6. AWS ⇄ Alibaba specifics (read before the first run)

**Time zones.** AWS services default to UTC; many Alibaba Cloud services and applications run
in `Asia/Shanghai` (UTC+8) — MaxCompute projects default to it. Set `timezone:` on each connection
to the zone its *naive* timestamps are written in. MySQL sessions are pinned to `+00:00` and
PostgreSQL sessions to `UTC`, so `TIMESTAMP`/`timestamptz` columns are compared as instants.
A uniform 8-hour shift on every row of a timestamp column almost always means a missing
`timezone:` setting.

**Timestamp precision.** Athena and MaxCompute keep milliseconds, MySQL `DATETIME(6)` and
PostgreSQL keep microseconds. Use `timestamp_precision: ms` for those pairs.

**MaxCompute.** Partitioned tables refuse full scans by default — add a partition `filter`
(recommended) or `hints: {odps.sql.allow.fullscan: "true"}`. The ODPS 2.0 type system is enabled
automatically.

**Athena cost.** Each check is one Athena query billed by data scanned. Always add partition
`filter`s for large tables; `profile` mode needs one scan per bucket level (usually 1–3) instead of
transferring every row. Use a dedicated workgroup with a bytes-scanned limit.

**Network.** The validator host needs a route to both clouds: AWS Site-to-Site VPN or Alibaba
Express Connect / VPN Gateway / CEN for private endpoints, or public endpoints with TLS. Alibaba
RDS / PolarDB require the validator's IP in the instance **whitelist**; AWS RDS needs a
security-group rule. Run `dvtool test-connections` first — it reports per-connection latency.

**Least privilege.**
* Databases: a read-only user (`SELECT` on the migrated schemas plus catalog access).
* AWS IAM: `athena:StartQueryExecution/GetQueryExecution/StopQueryExecution`,
  `glue:GetDatabase/GetTable(s)`, `s3:GetObject/PutObject` on the results prefix,
  `s3:GetObject/ListBucket` on the data, `secretsmanager:GetSecretValue` if used.
* Alibaba RAM: MaxCompute `Describe`/`Select` on tables and `CreateInstance` on the project,
  `oss:GetObject/ListObjects` for OSS files, `kms:GetSecretValue` if used.

The validator issues only `SELECT` statements (plus session `SET`s); `dvtool explain` prints them.

**Character sets and padding.** String checksums hash the bytes each engine returns. Use
`utf8mb4` on MySQL-family targets; `latin1` columns on one side will show as mismatches. For
`CHAR(n)` vs `VARCHAR` padding differences set `normalize.rtrim_strings: true`.

**Key text.** Buckets hash `CAST(key AS text)`. Integer and string keys are portable. For keys
whose text form differs between engines (UUID case, `DECIMAL` vs `BIGINT`, timestamps), set
`bucket_key_expr` on each side, e.g. `bucket_key_expr: "LOWER(CAST(id AS CHAR))"`.

## 7. Migration runbook

1. **Before cutover / during full load** — `schema` + `row_count` checks on every table to catch
   DDL drift early (`checks: [schema, row_count]`).
2. **During CDC replication** — validate closed partitions or rows older than the replication
   lag with `filter: "updated_at < '...'"`, `row_count_pct` tolerance, and `mode: profile`.
3. **Cutover window** — freeze writes, run the full suite on all tables with `--fail-on warn`.
   Keep the HTML/JSON reports as sign-off evidence.
4. **After cutover** — schedule nightly runs on critical tables (`--tag critical`).

`history.jsonl` in the output directory records every run for trend tracking across waves.

## 8. CI/CD

```bash
dvtool validate -c migration.yaml --format json,html,junit,md --fail-on fail
```

| exit code | meaning |
|---|---|
| 0 | all tables PASS (WARN allowed unless `--fail-on warn`) |
| 1 | at least one table FAILED |
| 2 | at least one table could not be validated (connection / SQL error) |
| 3 | configuration error |

Reports per run: `results.json` (machine readable), `report.html` (self-contained, shareable),
`checks.csv`, `discrepancies.csv`, `junit.xml` (Jenkins / GitLab / GitHub / Azure DevOps test
views), `summary.md` (also appended to the GitHub Actions job summary automatically).
See `examples/ci/github-actions.yml`.

## 9. Extending to a new engine

1. Add a `Dialect` subclass in `src/datavalidator/dialects.py` (quoting, text cast, MD5, hex → int,
   epoch seconds, catalog queries — about 40 lines).
2. Add a connection factory in `connectors.py` (any PEP 249 driver can reuse `DbApiExecutor`).
3. Register friendly `type` names in `CONNECTION_TYPES` (`config.py`).
   For a new driver, add its connection fields to `DRIVERS` in `ui_schema.py` so the web UI can show it.
4. Add an emulation to `tests/datavalidator/emulation.py` and run the cross-engine tests.

## 10. Testing

```bash
pytest tests/datavalidator          # 96 tests, no external services needed
```

The suite runs every validation mode end-to-end on SQLite and, through emulated engines, runs the
real **Athena** executor (CSV results and NULL handling) and **MaxCompute** executor with the
actual generated Athena / MaxCompute / MySQL SQL. Before relying on a new engine pairing in
production, do one pilot run against a small table with known, deliberately injected differences.

## 11. Known limitations

* `profile` mode compares timestamps at whole-second granularity inside the database; sub-second
  differences are found by `full`/`sample` mode or when the bucket is drilled down for another
  reason. Float sums use relative tolerance.
* Tables without any key (no PK, no unique column) get schema/count/aggregate checks only.
* Complex types (ARRAY/MAP/STRUCT) are compared in `full` mode as canonical JSON; `profile` mode
  only checks their NULL counts.
* The legacy Streamlit UI (`app.py`, `src/migration_validator`) still uses the original engine.

## 12. Web UI

`dvtool ui` opens a browser front end for the same project files. It contains no validation
logic of its own: it edits a project, then calls the engine exactly as the CLI does.

```bash
pip install ".[all,ui]"            # or only the drivers you need, plus ui
dvtool ui                          # http://localhost:8501
dvtool ui -c migration.yaml        # open a project (include: files are followed)
dvtool ui --host 0.0.0.0 --port 8080
```

Without the `ui` extra, `dvtool ui` prints the install command and every other command works as before.

### Walkthrough: Athena → Athena across two accounts

1. **Project.** Name the project and choose the report directory.
2. **Connections → + New connection.** Engine family *Athena*, type `aws-athena`. Name it
   `lake_a`; set region, workgroup, S3 staging dir, database and either a profile or access keys.
   Save, then **Test connection**.
3. **Duplicate** `lake_a` as `lake_b` and change what differs: account credentials, region,
   workgroup, catalog, database or staging dir. Test it too. The two connections share nothing.
4. **Tables.** Source `lake_a` + its database, target `lake_b` + its database, **Discover tables**.
   Tables with the same name are paired; uncheck any you don't want, then **Add checked pairs**.
   Pair renamed tables under *Pair tables manually*. Athena has no primary keys, so pick the key
   columns for each table under *Edit table*, and add a partition filter for large tables.
5. **Checks & options.** Set project defaults (for example `timestamp_precision: ms` and
   `compare.mode: profile`), then override per table where needed. Custom SQL checks are per table.
6. **Review SQL.** Shows the profile, bucket-checksum and row-fetch SQL for each side for DBA
   approval, and warns when a side billed per data scanned has no filter.
7. **Run.** Pick tables, tags, a mode override and report formats, then **Start validation**.
   Progress updates per table every second. **Cancel run** stops at the next step; a query already
   sent to an engine finishes first.
8. **Results.** Status per table and check with source and target values, a discrepancy table you
   can filter by table, kind, column or key, and download buttons for every report file.
   **History** re-opens any earlier run from `history.jsonl`.
9. **Project → Export YAML.** Download or save the file and run it with `dvtool validate -c`.

The same steps work for any pair in section 3, such as Aurora MySQL → PolarDB or Athena → MaxCompute.

### YAML import and export

* Export writes the project as the CLI reads it. Table entries hold only what differs from the
  project `defaults`, and loading the file gives the same configuration the UI had.
* Import accepts any project file. *Open from path* also follows `include:` files; an uploaded file
  can't, because it has no location.
* Secret fields accept a typed value or a `${env:...}`, `${file:...}`, `${aws_secret:...}` or
  `${alibaba_kms:...}` reference. Typed values are masked and kept only in the running session.
  Export is blocked until they are converted to `${env:VAR}` references (you can rename the
  variables); set those variables before running the file with the CLI.

### For developers

The UI lives in `src/datavalidator/ui/` and only calls public engine functions:
`ui_schema.py` (connection form spec), `workspace.py` (editable project + actions),
`discovery.py`, `project_io.py` (YAML export, secrets), `jobs.py` (background runs),
`engine.explain_table()`. A new driver needs a `DriverSpec` in `ui_schema.DRIVERS`;
`tests/datavalidator/test_ui_backend.py` fails if any connection type has no spec.
