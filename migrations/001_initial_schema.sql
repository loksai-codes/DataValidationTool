-- ==============================================================================
-- VALIDATOR_META INITIAL SCHEMA MIGRATION
-- Dedicated schema for Data Migration Validator application metadata.
-- Isolated completely from customer data (public.customers is never touched).
-- ==============================================================================

CREATE SCHEMA IF NOT EXISTS validator_meta;

-- 1. Validation Runs Table
CREATE TABLE IF NOT EXISTS validator_meta.validation_runs (
    run_id TEXT PRIMARY KEY,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    status TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_identifier TEXT NOT NULL,
    dest_type TEXT NOT NULL,
    dest_identifier TEXT NOT NULL,
    total_checks INT DEFAULT 0,
    passed_checks INT DEFAULT 0,
    failed_checks INT DEFAULT 0,
    warning_checks INT DEFAULT 0,
    inconclusive_checks INT DEFAULT 0,
    total_records_evaluated BIGINT DEFAULT 0,
    total_discrepancies BIGINT DEFAULT 0,
    config_json JSONB,
    duration_seconds DOUBLE PRECISION
);

-- Index on started_at for fast history queries
CREATE INDEX IF NOT EXISTS idx_validation_runs_started_at 
ON validator_meta.validation_runs(started_at DESC);

-- 2. Validation Checks Table
CREATE TABLE IF NOT EXISTS validator_meta.validation_checks (
    check_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES validator_meta.validation_runs(run_id) ON DELETE CASCADE,
    check_type TEXT NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    source_metric TEXT,
    dest_metric TEXT,
    difference TEXT,
    tolerance TEXT,
    duration_ms DOUBLE PRECISION,
    error_message TEXT,
    details_json JSONB
);

CREATE INDEX IF NOT EXISTS idx_validation_checks_run_id 
ON validator_meta.validation_checks(run_id);

-- 3. Discrepancies Table
CREATE TABLE IF NOT EXISTS validator_meta.discrepancies (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES validator_meta.validation_runs(run_id) ON DELETE CASCADE,
    discrepancy_type TEXT NOT NULL,
    record_key TEXT NOT NULL,
    column_name TEXT,
    source_val TEXT,
    dest_val TEXT,
    details_json JSONB
);

CREATE INDEX IF NOT EXISTS idx_discrepancies_run_type 
ON validator_meta.discrepancies(run_id, discrepancy_type);

CREATE INDEX IF NOT EXISTS idx_discrepancies_key 
ON validator_meta.discrepancies(run_id, record_key);

-- 4. Dataset Mappings Table
CREATE TABLE IF NOT EXISTS validator_meta.dataset_mappings (
    id BIGSERIAL PRIMARY KEY,
    mapping_name TEXT UNIQUE NOT NULL,
    source_table TEXT NOT NULL,
    dest_table TEXT NOT NULL,
    key_columns TEXT[] NOT NULL,
    column_map_json JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
