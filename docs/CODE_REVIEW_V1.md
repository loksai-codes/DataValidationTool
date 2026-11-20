# Review of the v1 engine (`src/migration_validator`) — why v2 (`datavalidator`) exists

Findings from a full read of the v1 code. These are the reasons the new engine was built
alongside it rather than patched.

| # | Area | Finding | Impact | v2 |
|---|---|---|---|---|
| 1 | Athena connector | Any Athena error silently falls back to reading **one hard-coded S3 Parquet object** (`S3_SOURCE_KEY`), whatever table was requested; `_execute_s3_parquet_fallback` ignores the SQL | Validating table B can silently report results for table A | Errors surface as `ERROR` status; no silent fallback |
| 2 | Athena connector | `get_key_summary` and `fetch_records_batch` always load the whole Parquet file into pandas | Contradicts "no full load into memory"; fails on large tables | All work pushed down as SQL; bounded batches |
| 3 | Athena connector | `get_query_results` is not paginated (max 1,000 rows); polling has no timeout | Truncated results; hung runs | Results read as CSV from S3; timeout + cancel |
| 4 | Athena connector | `test_connection` reports success when Athena is `AccessDenied` | False green connection test | Real `SELECT 1` test |
| 5 | Record comparison | Cross-engine keyset pagination (`ORDER BY key`, `WHERE key > last`) | MySQL/PG/Athena collations order strings differently, so batches go out of step and report rows as missing/extra when they aren't | MD5 hash buckets, identical on both engines |
| 6 | Orchestration | Numeric columns guessed by name (`balance/amount/price/total`, fallback `account_balance`); distinct counts and data-quality checks only on `email`; default mapping is the `customers` table | Not reusable for other tables | Catalog types drive every check |
| 7 | Canonicalization | Floats formatted with `:.4f` | Differences beyond the 4th decimal place are silently ignored | Exact `Decimal`; explicit tolerances |
| 8 | PostgreSQL keys | Composite-key distinct count uses `COALESCE(col::text, '')` | NULL and `''` collide | Explicit NULL-key handling |
| 9 | Engines | Only Athena/S3 and PostgreSQL | No MySQL family (most RDS/Aurora/PolarDB), nothing on Alibaba Cloud | 8 dialects, 25+ connection types |
| 10 | Operations | Streamlit UI is the only entry point; no CLI or exit codes | Can't gate CI/CD or schedule runs | `dvtool` CLI, JUnit, exit codes |
| 11 | Security | Personal AWS account ID and bucket name hard-coded as defaults in `config.py` and `.env.example` | Leaks account details; wrong defaults for other users | No environment-specific defaults |
| 12 | Engine pool | `dispose_engine` rebuilds the key with default pool args | Engines created with non-default args are never disposed | n/a (DB-API pool per connection) |
