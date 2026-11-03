"""
Validation orchestration service.
Coordinates execution of all validation rules, tracks progress,
aggregates discrepancies, computes overall run status, and persists results.
"""

import hashlib
import json
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from migration_validator.connectors.base import BaseConnector
from migration_validator.logging_config import get_logger
from migration_validator.repositories.metadata_repository import MetadataRepository
from migration_validator.services.schema_discovery import SchemaDiscoveryService
from migration_validator.validation.aggregates import validate_aggregates, validate_distinct_counts
from migration_validator.validation.counts import validate_record_counts
from migration_validator.validation.keys import validate_keys
from migration_validator.validation.models import (
    CheckResult,
    CheckType,
    Discrepancy,
    DiscrepancyType,
    ValidationRuleConfig,
    ValidationRunSummary,
    ValidationStatus,
)
from migration_validator.validation.nulls import validate_null_counts
from migration_validator.validation.quality import validate_email_data_quality
from migration_validator.validation.record_comparison import (
    RecordComparisonEngine,
    run_record_comparison_check,
)
from migration_validator.validation.schema import validate_schema

logger = get_logger("services.orchestration")


def compute_config_fingerprint(rule_config: ValidationRuleConfig) -> str:
    """Computes a deterministic hash fingerprint of validation rule settings for resume compatibility."""
    elements = [
        ",".join(sorted(rule_config.key_columns)),
        ",".join(f"{k}:{v}" for k, v in sorted(rule_config.column_mapping.items())),
        str(rule_config.batch_size),
        str(rule_config.numeric_tolerance_abs),
        str(rule_config.numeric_tolerance_rel),
        str(rule_config.comparison_strategy),
    ]
    raw = "|".join(elements)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class ValidationOrchestrator:
    """Coordinates execution of validation rules and persistence to metadata repository."""

    def __init__(
        self,
        source_connector: BaseConnector,
        dest_connector: BaseConnector,
        metadata_repo: Optional[MetadataRepository] = None,
    ):
        self.source = source_connector
        self.dest = dest_connector
        self.repo = metadata_repo
        self.discovery = SchemaDiscoveryService(source_connector, dest_connector)

    def execute_validation_run(
        self,
        source_table: str,
        dest_table: str,
        rule_config: ValidationRuleConfig,
        source_db: Optional[str] = None,
        dest_schema: Optional[str] = "public",
        progress_callback: Optional[Callable[[str, float, Optional[str]], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        resume_run_id: Optional[str] = None,
    ) -> ValidationRunSummary:
        """
        Run the complete validation suite according to rule configuration.
        Supports resuming an interrupted run via resume_run_id from its latest committed checkpoint.
        """
        start_time = time.perf_counter()
        started_at_str = datetime.now().isoformat()
        current_fp = compute_config_fingerprint(rule_config)

        checks: List[CheckResult] = []
        total_records_evaluated = 0
        total_discrepancies = 0
        discrepancy_counts: Dict[str, int] = {}
        start_batch_index = 0
        initial_last_src_key: Optional[Any] = None
        initial_last_dst_key: Optional[Any] = None

        if resume_run_id:
            run_id = resume_run_id
            if not self.repo:
                raise ValueError("Cannot resume validation run without a configured MetadataRepository.")
            existing_run = self.repo.get_run_by_id(run_id)
            if not existing_run:
                raise ValueError(f"Run ID '{run_id}' not found in metadata repository. Cannot resume non-existent run.")
            if existing_run["status"] in ["PASSED", "FAILED", "WARNING", "INCONCLUSIVE", "COMPLETED"]:
                raise ValueError(f"Run ID '{run_id}' has already completed with status '{existing_run['status']}'. Cannot resume a completed run.")

            orig_cfg = existing_run.get("config_json")
            if orig_cfg:
                if isinstance(orig_cfg, str):
                    orig_cfg = json.loads(orig_cfg)
                orig_fp = compute_config_fingerprint(ValidationRuleConfig(**orig_cfg))
                if orig_fp != current_fp:
                    raise ValueError(
                        f"Cannot resume run '{run_id}': Configuration fingerprint mismatch. "
                        f"Initial configuration fingerprint was '{orig_fp}', "
                        f"but current configuration fingerprint is '{current_fp}'. "
                        "Resuming with altered key columns, batch size, or comparison strategy is unsafe."
                    )

            latest_chk = self.repo.get_latest_checkpoint(run_id)
            if latest_chk:
                if latest_chk["config_fingerprint"] != current_fp:
                    raise ValueError(
                        f"Cannot resume run '{run_id}': Configuration fingerprint mismatch. "
                        f"Persisted fingerprint was '{latest_chk['config_fingerprint']}', "
                        f"but current configuration fingerprint is '{current_fp}'. "
                        "Resuming with altered key columns, batch size, or comparison strategy is unsafe."
                    )
                start_batch_index = latest_chk["batch_index"] + 1
                initial_last_src_key = latest_chk["last_source_key"]
                initial_last_dst_key = latest_chk["last_dest_key"]
                total_records_evaluated = latest_chk["records_evaluated"]
                total_discrepancies = latest_chk["discrepancies_found"]
                discrepancy_counts = self.repo.get_discrepancy_counts_by_type(run_id)
                logger.info(
                    "Resuming run %s from checkpoint batch %d (last_src_key=%s, last_dst_key=%s, records_evaluated=%d, discrepancies=%d)",
                    run_id, latest_chk["batch_index"], initial_last_src_key, initial_last_dst_key, total_records_evaluated, total_discrepancies,
                )
            self.repo.update_run_status(run_id, "RUNNING")
            logger.info("Resumed validation run %s: %s -> %s", run_id, source_table, dest_table)
        else:
            run_id = f"RUN-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            logger.info("Starting validation run %s: %s -> %s", run_id, source_table, dest_table)
            if self.repo:
                self.repo.record_run_start(
                    run_id=run_id,
                    source_type="AWS_ATHENA_OR_S3",
                    source_identifier=f"{source_db or 'default'}.{source_table}",
                    dest_type="POSTGRESQL",
                    dest_identifier=f"{dest_schema or 'public'}.{dest_table}",
                    config_dict=rule_config.model_dump(),
                )

        def update_progress(stage: str, ratio: float, msg: Optional[str] = None):
            logger.info("[%s] Stage: %s (%.0f%%) - %s", run_id, stage, ratio * 100, msg or "")
            if progress_callback:
                progress_callback(stage, ratio, msg)

        try:
            # Stage 1: Schema Discovery & Validation
            if rule_config.run_schema_validation:
                update_progress("Schema Validation", 0.10, "Comparing table schemas and data types...")
                if cancel_check and cancel_check():
                    raise InterruptedError("Validation cancelled by user.")

                src_schema = self.discovery.get_source_schema(source_table, schema_or_db=source_db)
                dst_schema = self.discovery.get_dest_schema(dest_table, schema=dest_schema)

                schema_chk = validate_schema(
                    discovery_service=self.discovery,
                    source_schema=src_schema,
                    dest_schema=dst_schema,
                    column_mapping=rule_config.column_mapping,
                    key_columns=rule_config.key_columns
                )
                checks.append(schema_chk)

            # Stage 2: Record Count Comparison
            if rule_config.run_record_count:
                update_progress("Record Counts", 0.25, "Querying dynamic record counts...")
                if cancel_check and cancel_check():
                    raise InterruptedError("Validation cancelled by user.")

                count_chk = validate_record_counts(
                    source_connector=self.source,
                    dest_connector=self.dest,
                    source_table=source_table,
                    dest_table=dest_table,
                    source_db=source_db,
                    dest_schema=dest_schema
                )
                checks.append(count_chk)

            # Stage 3: Key Uniqueness & Nullability
            if rule_config.run_key_validation:
                update_progress("Key Integrity", 0.40, "Evaluating key uniqueness, nullability, and boundaries...")
                if cancel_check and cancel_check():
                    raise InterruptedError("Validation cancelled by user.")

                key_chks = validate_keys(
                    source_connector=self.source,
                    dest_connector=self.dest,
                    source_table=source_table,
                    dest_table=dest_table,
                    key_columns=rule_config.key_columns,
                    source_db=source_db,
                    dest_schema=dest_schema
                )
                checks.extend(key_chks)

            # Stage 4: Column NULL Parity
            if rule_config.run_null_validation:
                update_progress("NULL Parity", 0.50, "Checking NULL value counts by column...")
                if cancel_check and cancel_check():
                    raise InterruptedError("Validation cancelled by user.")

                null_chks = validate_null_counts(
                    source_connector=self.source,
                    dest_connector=self.dest,
                    source_table=source_table,
                    dest_table=dest_table,
                    column_mapping=rule_config.column_mapping,
                    source_db=source_db,
                    dest_schema=dest_schema
                )
                checks.extend(null_chks)

            # Stage 5: Numeric Aggregates & Distinct Counts
            if rule_config.run_numeric_aggregates:
                update_progress("Numeric Aggregates", 0.65, "Pushing down SUM, AVG, MIN, MAX calculations...")
                if cancel_check and cancel_check():
                    raise InterruptedError("Validation cancelled by user.")

                # Identify numeric columns from column mapping
                numeric_cols = [
                    col for col in rule_config.column_mapping.keys()
                    if "balance" in col.lower() or "amount" in col.lower() or "price" in col.lower() or "total" in col.lower()
                ]
                if not numeric_cols:
                    numeric_cols = ["account_balance"]

                agg_chks = validate_aggregates(
                    source_connector=self.source,
                    dest_connector=self.dest,
                    source_table=source_table,
                    dest_table=dest_table,
                    numeric_columns=numeric_cols,
                    tolerance_abs=rule_config.numeric_tolerance_abs,
                    tolerance_rel=rule_config.numeric_tolerance_rel,
                    source_db=source_db,
                    dest_schema=dest_schema
                )
                checks.extend(agg_chks)

            if rule_config.run_distinct_counts:
                distinct_cols = ["email"] if "email" in rule_config.column_mapping else []
                if distinct_cols:
                    dist_chks = validate_distinct_counts(
                        source_connector=self.source,
                        dest_connector=self.dest,
                        source_table=source_table,
                        dest_table=dest_table,
                        columns_to_check=distinct_cols,
                        source_db=source_db,
                        dest_schema=dest_schema
                    )
                    checks.extend(dist_chks)

            # Stage 6: Record-Level Fingerprint Comparison
            if rule_config.run_record_comparison:
                update_progress("Record-Level Comparison", 0.80, "Streaming key-based batches and SHA-256 fingerprints...")
                if cancel_check and cancel_check():
                    raise InterruptedError("Validation cancelled by user.")

                current_batch_idx = start_batch_index

                def handle_batch_checkpoint(
                    batch_idx: int,
                    batch_discs: List[Discrepancy],
                    batch_stats: Dict[str, int],
                    cursor_info: Dict[str, Any],
                ):
                    nonlocal current_batch_idx, total_records_evaluated, total_discrepancies
                    eff_batch_idx = current_batch_idx + batch_idx
                    total_records_evaluated += batch_stats.get("evaluated", 0)
                    total_discrepancies += len(batch_discs)
                    if self.repo:
                        d_dicts = [d.model_dump() for d in batch_discs]
                        self.repo.commit_checkpoint_with_discrepancies(
                            run_id=run_id,
                            batch_index=eff_batch_idx,
                            last_source_key=cursor_info.get("last_src_key"),
                            last_dest_key=cursor_info.get("last_dst_key"),
                            records_evaluated=total_records_evaluated,
                            discrepancies_found=total_discrepancies,
                            discrepancies=d_dicts,
                            config_fingerprint=current_fp,
                        )

                rec_engine = RecordComparisonEngine(
                    source_connector=self.source,
                    dest_connector=self.dest,
                    source_table=source_table,
                    dest_table=dest_table,
                    key_columns=rule_config.key_columns,
                    column_mapping=rule_config.column_mapping,
                    batch_size=rule_config.batch_size,
                    source_db=source_db,
                    dest_schema=dest_schema,
                    strategy=rule_config.comparison_strategy,
                    max_discrepancy_samples=rule_config.max_discrepancy_samples,
                    initial_last_src_key=initial_last_src_key,
                    initial_last_dst_key=initial_last_dst_key,
                )

                max_recs = rule_config.sample_size if rule_config.use_sampling else None

                init_val = discrepancy_counts.get(DiscrepancyType.VALUE_MISMATCH.value, 0)
                init_nulls = discrepancy_counts.get(DiscrepancyType.NULL_MISMATCH.value, 0)
                init_changed = init_val + init_nulls
                rec_chk, total_discrepancies, discrepancy_counts = run_record_comparison_check(
                    engine=rec_engine,
                    max_records=max_recs,
                    progress_callback=update_progress,
                    cancel_check=cancel_check,
                    checkpoint_callback=handle_batch_checkpoint,
                    initial_evaluated=total_records_evaluated,
                    initial_missing=discrepancy_counts.get(DiscrepancyType.MISSING_IN_DEST.value, 0),
                    initial_extra=discrepancy_counts.get(DiscrepancyType.EXTRA_IN_DEST.value, 0),
                    initial_changed=init_changed,
                    initial_col_diffs=init_val + init_nulls,
                    initial_null_diffs=init_nulls,
                    initial_dup_keys=discrepancy_counts.get(DiscrepancyType.DUPLICATE_KEY.value, 0),
                    initial_samples_collected=total_discrepancies,
                )
                checks.append(rec_chk)
                total_records_evaluated = rec_chk.metadata.get("total_evaluated", 0)

            # Stage 7: Data Quality Checks (Optional)
            if rule_config.run_data_quality:
                update_progress("Data Quality", 0.90, "Evaluating data quality rules...")
                dq_chks = validate_email_data_quality(
                    connector=self.dest,
                    table_name=dest_table,
                    schema_or_db=dest_schema
                )
                checks.extend(dq_chks)

            # Compute Overall Status
            passed = sum(1 for c in checks if c.status == ValidationStatus.PASSED)
            failed = sum(1 for c in checks if c.status == ValidationStatus.FAILED)
            warnings = sum(1 for c in checks if c.status == ValidationStatus.WARNING)
            inconclusives = sum(1 for c in checks if c.status == ValidationStatus.INCONCLUSIVE)

            if failed > 0:
                overall_status = ValidationStatus.FAILED
            elif warnings > 0:
                overall_status = ValidationStatus.WARNING
            elif inconclusives > 0:
                overall_status = ValidationStatus.INCONCLUSIVE
            else:
                overall_status = ValidationStatus.PASSED
            run_finish_status = overall_status.value

        except InterruptedError:
            overall_status = ValidationStatus.CANCELLED
            run_finish_status = "CANCELLED"
            if self.repo:
                self.repo.update_run_status(run_id, "CANCELLED")
            passed = sum(1 for c in checks if c.status == ValidationStatus.PASSED)
            failed = sum(1 for c in checks if c.status == ValidationStatus.FAILED)
            warnings = sum(1 for c in checks if c.status == ValidationStatus.WARNING)
            inconclusives = sum(1 for c in checks if c.status == ValidationStatus.INCONCLUSIVE)

        except Exception as e:
            logger.error("Validation execution error: %s", str(e), exc_info=True)
            overall_status = ValidationStatus.FAILED
            run_finish_status = "ABORTED"
            checks.append(
                CheckResult(
                    check_id=f"CHK-ORCH-ERR-{uuid.uuid4().hex[:6]}",
                    check_type=CheckType.RECORD_COUNT,
                    check_name="Validation Orchestration Execution",
                    status=ValidationStatus.FAILED,
                    error_details=f"Unexpected error: {str(e)}"
                )
            )
            passed = sum(1 for c in checks if c.status == ValidationStatus.PASSED)
            failed = sum(1 for c in checks if c.status == ValidationStatus.FAILED)
            warnings = sum(1 for c in checks if c.status == ValidationStatus.WARNING)
            inconclusives = sum(1 for c in checks if c.status == ValidationStatus.INCONCLUSIVE)

        duration_sec = time.perf_counter() - start_time
        completed_at_str = datetime.now().isoformat()

        # Finalize metadata repository records
        if self.repo:
            check_dicts = [c.model_dump() for c in checks]
            self.repo.record_checks(run_id, check_dicts)
            self.repo.record_run_finish(
                run_id=run_id,
                status=run_finish_status or overall_status.value,
                total_checks=len(checks),
                passed_checks=passed,
                failed_checks=failed,
                warning_checks=warnings,
                inconclusive_checks=inconclusives,
                total_records_evaluated=total_records_evaluated,
                total_discrepancies=total_discrepancies,
                duration_seconds=duration_sec
            )

        update_progress("Complete", 1.0, f"Run {run_id} finished: {overall_status.value}")

        return ValidationRunSummary(
            run_id=run_id,
            overall_status=overall_status,
            started_at=started_at_str,
            completed_at=completed_at_str,
            duration_seconds=round(duration_sec, 2),
            total_checks=len(checks),
            passed_checks=passed,
            failed_checks=failed,
            warning_checks=warnings,
            inconclusive_checks=inconclusives,
            total_records_evaluated=total_records_evaluated,
            total_discrepancies=total_discrepancies,
            discrepancy_counts=discrepancy_counts,
            checks=checks
        )
