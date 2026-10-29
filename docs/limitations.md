# Limitations & Known Constraints

---

## 1. Local SQLite Metadata Store
- **Single-Machine Persistence**: By default, run metadata, partition checkpoints, and discrepancies are stored in a local SQLite file (`data/validation_metadata.db`).
- **Resumption Constraint**: Durable resumption after app restart requires that this SQLite file remains available on disk.
- **Distributed Scale-Out**: For multi-machine worker fleets, replace `SQLiteRunRepository` with a shared durable database (e.g. PostgreSQL or AWS RDS).

---

## 2. In-Flight Table Mutations
- If source or target tables undergo concurrent write/update traffic during validation without transaction isolation or point-in-time snapshotting, false discrepancies may arise across partition boundaries.
- **Recommendation**: Validate tables during cutover windows, or on point-in-time snapshots / read replicas.

---

## 3. Connector Support Status
- **Implemented**: AWS Athena, PostgreSQL, Synthetic In-Memory.
- **Planned**: MySQL, MSSQL, Alibaba MaxCompute. While their data models and interface definitions exist in the registry, their drivers will be activated once integration suites are complete.

---

## 4. Large Result Set Memory Protection
- Discrepancy tables in the UI are capped and paginated (100 rows per page) to prevent loading millions of discrepancy records into Streamlit browser memory.
- Full discrepancy exports should use the CSV or JSON file export endpoints.
