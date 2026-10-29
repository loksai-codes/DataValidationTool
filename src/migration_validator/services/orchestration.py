"""
Validation orchestration service.
Coordinates execution of all validation rules, tracks progress,
aggregates discrepancies, computes overall run status, and persists results.
"""

import time
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional

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


class ValidationOrchestrator:
    """Coordinates execution of validation rules and persistence to metadata repository."""

    def __init__(
        self,
        source_connector: BaseConnector,
        dest_connector: BaseConnector,
        metadata_repo: Optional[MetadataRepository] = None
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
        cancel_check: Optional[Callable[[], bool]] = None
    ) -> ValidationRunSummary:
        """
        Run the complete validation suite according to rule configuration.
        Emits progress events to progress_callback(stage_name, progress_ratio_0_to_1, message).
        """
        run_id = f"RUN-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        start_time = time.perf_counter()
        started_at_str = datetime.now().isoformat()

        logger.info("Starting validation run %s: %s -> %s", run_id, source_table, dest_table)

        # Notify start and record in repository
        if self.repo:
            self.repo.record_run_start(
                run_id=run_id,
                source_type="AWS_ATHENA_OR_S3",
                source_identifier=f"{source_db or 'default'}.{source_table}",
                dest_type="POSTGRESQL",
                dest_identifier=f"{dest_schema or 'public'}.{dest_table}",
                config_dict=rule_config.model_dump()
            )

        checks: List[CheckResult] = []
        total_records_evaluated = 0
        total_discrepancies = 0
        all_discrepancies: List[Discrepancy] = []
        discrepancy_counts: Dict[str, int] = {}

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

                rec_engine = RecordComparisonEngine(
                    source_connector=self.source,
                    dest_connector=self.dest,
                    source_table=source_table,
                    dest_table=dest_table,
                    key_columns=rule_config.key_columns,
                    column_mapping=rule_config.column_mapping,
                    batch_size=rule_config.batch_size,
                    source_db=source_db,
                    dest_schema=dest_schema
                )

                max_recs = rule_config.sample_size if rule_config.use_sampling else None

                def sink_discrepancies(batch: List[Discrepancy]):
                    all_discrepancies.extend(batch)
                    if self.repo:
                        # Persist discrepancy batch to DB
                        d_dicts = [d.model_dump() for d in batch]
                        self.repo.record_discrepancies(run_id, d_dicts)

                rec_chk, total_discrepancies, discrepancy_counts = run_record_comparison_check(
                    engine=rec_engine,
                    max_records=max_recs,
                    discrepancy_sink=sink_discrepancies,
                    cancel_check=cancel_check
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

        except InterruptedError:
            overall_status = ValidationStatus.CANCELLED
            passed = sum(1 for c in checks if c.status == ValidationStatus.PASSED)
            failed = sum(1 for c in checks if c.status == ValidationStatus.FAILED)
            warnings = sum(1 for c in checks if c.status == ValidationStatus.WARNING)
            inconclusives = sum(1 for c in checks if c.status == ValidationStatus.INCONCLUSIVE)

        except Exception as e:
            logger.error("Validation execution error: %s", str(e), exc_info=True)
            overall_status = ValidationStatus.FAILED
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
                status=overall_status.value,
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
