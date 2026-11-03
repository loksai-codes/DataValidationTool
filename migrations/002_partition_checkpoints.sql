-- ==============================================================================
-- VALIDATOR_META PARTITION CHECKPOINTS MIGRATION
-- Durable checkpoint tracking for interrupted validation runs and safe resumption.
-- ==============================================================================

CREATE TABLE IF NOT EXISTS validator_meta.partition_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES validator_meta.validation_runs(run_id) ON DELETE CASCADE,
    batch_index INT NOT NULL,
    last_source_key TEXT,
    last_dest_key TEXT,
    records_evaluated BIGINT NOT NULL DEFAULT 0,
    discrepancies_found BIGINT NOT NULL DEFAULT 0,
    config_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'COMMITTED',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_partition_checkpoints_run_batch 
ON validator_meta.partition_checkpoints(run_id, batch_index DESC);

ALTER TABLE validator_meta.discrepancies 
ADD COLUMN IF NOT EXISTS discrepancy_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_discrepancies_run_hash 
ON validator_meta.discrepancies(run_id, discrepancy_hash);
