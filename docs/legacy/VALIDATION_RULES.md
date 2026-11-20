# Validation Rules & Decision Matrix

This document defines every validation rule executed by the Data Migration Validator, including its trigger, mathematical logic, and exact status criteria (`PASSED`, `FAILED`, `WARNING`, `INCONCLUSIVE`).

---

## Status Definitions

| Status | Meaning | Action Required |
| :--- | :--- | :--- |
| **`PASSED`** | System metrics and record values match within configured tolerances. | None. Migration verified successfully. |
| **`FAILED`** | At least one validation rule breached permissible thresholds or data discrepancies exist. | Immediate investigation into data pipeline or schema transformation. |
| **`WARNING`** | Non-critical advisory condition observed (e.g. data quality warnings or compatible type coercions). | Review data quality metrics; migration infrastructure remains sound. |
| **`INCONCLUSIVE`** | Connectivity failure, timeout, or missing table preventing metrics evaluation. | Verify network, service credentials, or table names. |
| **`CANCELLED`** | Execution was manually stopped by user request. | Re-run validation suite when ready. |

---

## Rule Specification Matrix

### 1. Schema Validation (`SCHEMA`)
- **Trigger**: Validates column mapping, column presence, and data type compatibility.
- **Criteria**:
  - `PASSED`: All mapped source columns exist in the destination and data types belong to equivalent compatibility categories (e.g., Athena `string` -> PostgreSQL `text`; Athena `bigint` -> PostgreSQL `bigint`; Athena `double` -> PostgreSQL `double precision`).
  - `WARNING`: All columns are present, but type differences require coercion.
  - `FAILED`: Any mapped column is missing in destination, or types are incompatible (e.g. `bigint` -> `boolean`).
  - `INCONCLUSIVE`: Schema metadata could not be fetched due to connection failure.

### 2. Record Count Parity (`RECORD_COUNT`)
- **Trigger**: Queries `SELECT COUNT(*)` dynamically from both systems.
- **Criteria**:
  - `PASSED`: `Destination Count == Source Count`.
  - `FAILED`: `Destination Count != Source Count`.
  - `INCONCLUSIVE`: Either database query failed or timed out.

### 3. Key Integrity & Uniqueness (`KEY_INTEGRITY`)
- **Trigger**: Analyzes the configured business primary key or composite keys (`customer_id`).
- **Sub-Checks**:
  1. **Duplicate Key Detection**:
     - `PASSED`: 0 duplicate keys in both systems.
     - `FAILED`: Duplicate keys detected in source or destination.
  2. **Key Nullability**:
     - `PASSED`: 0 NULL keys.
     - `FAILED`: Key column contains NULL values.
  3. **Key Boundary Parity**:
     - `PASSED`: `MIN(key)` and `MAX(key)` match between source and destination.
     - `FAILED`: Key bounds diverge.

### 4. NULL Distribution Parity (`NULL_COUNT`)
- **Trigger**: Calculates `COUNT(CASE WHEN col IS NULL THEN 1 END)` for every mapped column.
- **Criteria**:
  - `PASSED`: `Destination NULL Count == Source NULL Count` for all mapped columns.
  - `FAILED`: Any column exhibits a difference in NULL values between source and destination.
  - *Note*: Differentiates literal NULL values from empty strings `""`.

### 5. Numeric Aggregates (`AGGREGATE`)
- **Trigger**: Pushes down `SUM`, `AVG`, `MIN`, and `MAX` calculations for financial/numeric columns (`account_balance`).
- **Mathematical Condition**:
  $$\text{Within Tolerance} \iff |D - S| \le \text{tol}_{\text{abs}} \quad\text{OR}\quad \left( |S| > 10^{-9} \land \frac{|D - S|}{|S|} \le \text{tol}_{\text{rel}} \right)$$
- **Criteria**:
  - `PASSED`: Difference is within absolute tolerance (`0.01`) OR relative tolerance (`0.0001`).
  - `FAILED`: Difference exceeds both absolute and relative tolerances.
  - `INCONCLUSIVE`: Aggregates could not be computed.

### 6. Distinct Value Counts (`DISTINCT_COUNT`)
- **Trigger**: Compares `COUNT(DISTINCT col)` for high-cardinality attributes (e.g., `email`).
- **Criteria**:
  - `PASSED`: `Destination Distinct Count == Source Distinct Count`.
  - `FAILED`: Distinct counts differ between systems.

### 7. Record-Level Fingerprint Comparison (`RECORD_COMPARISON`)
- **Trigger**: Streams key-based batches using keyset pagination (`WHERE key > :last_key ORDER BY key ASC LIMIT :batch_size`).
- **Canonical Fingerprint**:
  - Serializes each row deterministically using `canonical_value_str()`.
  - Computes SHA-256 digest: `SHA256(col_a:val_a|col_b:val_b|...)`.
- **Criteria**:
  - `PASSED`: All evaluated records have matching SHA-256 fingerprints (0 discrepancies).
  - `FAILED`: Any missing records (`MISSING_IN_DEST`), extra records (`EXTRA_IN_DEST`), or changed column values (`VALUE_MISMATCH`) are detected.
  - `INCONCLUSIVE`: 0 records evaluated.

### 8. Data Quality Rules (`DATA_QUALITY`)
- **Trigger**: Evaluates dataset characteristics independently of migration consistency.
- **Criteria**:
  - **Email Repeated Occurrences**: In the baseline dataset, 9,014 duplicate emails exist across 100,000 customers (90,986 unique).
  - `PASSED`: 0 duplicate emails.
  - `WARNING`: Repeated emails observed. Flagged as an advisory data quality warning; does NOT fail migration parity.
