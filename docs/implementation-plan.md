# Implementation Plan

1. **Foundation:** package config, models, synthetic connectors, fixtures, test/lint setup.
2. **Comparison:** schema/count/key/null/value/aggregate checks and type normalization; test missing/extra/changed rows.
3. **Persistence:** SQLite repository, migrations, stable run/partition IDs, transactional commits, leases, stale-run recovery, retry, cancellation and resume tests.
4. **Reporting:** persisted structured results → CSV/JSON/HTML, with no re-execution on export.
5. **Connectors:** Athena and PostgreSQL end-to-end, metadata discovery and read-only batched fetching.
6. **UI:** clean source/target selection, connection testing, metadata dropdowns, config, progress/resume, results/history.
7. **Batch jobs:** JSON single-job configs, CSV manifest, bounded concurrent jobs.
8. **Expansion:** MySQL/MSSQL/MaxCompute only with tests; optional Spark last.
9. **Release gates:** all tests pass, synthetic demo works, resume test proves committed partitions are skipped and no duplicate results are created, docs/security review complete.
