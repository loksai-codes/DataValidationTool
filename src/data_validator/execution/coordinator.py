"""Run coordinator orchestrating checks, partitioning, and durable validation."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from data_validator.comparison.count_checks import run_count_check
from data_validator.comparison.partition_planner import plan_partitions
from data_validator.comparison.record_compare import compare_records_batch
from data_validator.comparison.schema_checks import SchemaDiffSeverity, run_schema_check
from data_validator.config.models import ValidationJobConfig
from data_validator.config.validators import preflight_validate_job
from data_validator.connectors.base import BaseConnector
from data_validator.connectors.registry import create_connector
from data_validator.execution.cancellation import CancellationToken, ValidationCancelledError
from data_validator.persistence.models import (
    CheckRecord,
    CheckStatus,
    PartitionStatus,
    ReportReference,
    RunRecord,
    RunStatus,
)
from data_validator.persistence.repository import BaseRunRepository
from data_validator.reporting.builder import build_report_data
from data_validator.reporting.csv_exporter import export_csv_report
from data_validator.reporting.html_exporter import export_html_report
from data_validator.reporting.json_exporter import export_json_report
from data_validator.security.secrets import SecretStore, default_secret_store

logger = logging.getLogger(__name__)


class ValidationCoordinator:
    """Orchestrates validation execution, state persistence, and durable resume."""

    def __init__(
        self,
        config: ValidationJobConfig,
        repository: BaseRunRepository,
        secret_store: SecretStore = default_secret_store,
        source_connector: BaseConnector | None = None,
        target_connector: BaseConnector | None = None,
        cancellation_token: CancellationToken | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.config = config
        self.repo = repository
        self.secret_store = secret_store
        self.source_connector = source_connector
        self.target_connector = target_connector
        self.cancel_token = cancellation_token or CancellationToken()
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"

    def _init_connectors(self) -> None:
        if self.source_connector is None:
            s_cfg = self.secret_store.get_connection(self.config.source.connection_id)
            self.source_connector = create_connector(
                self.config.source.provider.value,
                self.config.source.engine.value,
                self.config.source.connection_id,
                s_cfg,
            )
        if self.target_connector is None:
            t_cfg = self.secret_store.get_connection(self.config.target.connection_id)
            self.target_connector = create_connector(
                self.config.target.provider.value,
                self.config.target.engine.value,
                self.config.target.connection_id,
                t_cfg,
            )

    def run(self, resume_run_id: str | None = None) -> RunRecord:
        """Execute validation job or resume an interrupted run."""
        now = datetime.now(timezone.utc)

        # 1. Initialize or load run record
        if resume_run_id:
            run_record = self.repo.get_run(resume_run_id)
            if not run_record:
                raise ValueError(f"Run ID '{resume_run_id}' not found to resume.")
            self.repo.update_run_status(resume_run_id, RunStatus.RUNNING)
            self.repo.log_event(
                resume_run_id, "INFO", "Coordinator", f"Resuming run {resume_run_id}"
            )
            run_id = resume_run_id
        else:
            run_id = f"run_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
            config_json_str = json.dumps(self.config.model_dump(mode="json"))
            run_record = RunRecord(
                run_id=run_id,
                job_name=self.config.job_name,
                status=RunStatus.RUNNING,
                config_json=config_json_str,
                started_at=now,
                last_heartbeat_at=now,
            )
            self.repo.create_run(run_record)
            self.repo.log_event(
                run_id, "INFO", "Coordinator", f"Started validation run {run_id}"
            )

        try:
            self._init_connectors()
            assert self.source_connector is not None
            assert self.target_connector is not None

            # Test connections
            s_test = self.source_connector.test_connection()
            if not s_test.success:
                raise ConnectionError(f"Source connection failed: {s_test.message}")

            t_test = self.target_connector.test_connection()
            if not t_test.success:
                raise ConnectionError(f"Target connection failed: {t_test.message}")

            # 2. Metadata discovery & Schema Check
            s_meta = self.source_connector.get_table_metadata(
                table=self.config.source.table,
                schema=self.config.source.schema_,
                database=self.config.source.database,
                catalog=self.config.source.catalog,
            )
            t_meta = self.target_connector.get_table_metadata(
                table=self.config.target.table,
                schema=self.config.target.schema_,
                database=self.config.target.database,
                catalog=self.config.target.catalog,
            )

            # Preflight validation
            preflight_validate_job(
                self.config,
                secret_store=self.secret_store,
                source_metadata=s_meta,
                target_metadata=t_meta,
            )

            checks_to_save: list[CheckRecord] = []

            # Execute schema check if enabled
            if self.config.comparison.checks.schema_check:
                s_cols_dict = [
                    {"name": c.name, "type": c.data_type, "nullable": c.nullable}
                    for c in s_meta.columns
                ]
                t_cols_dict = [
                    {"name": c.name, "type": c.data_type, "nullable": c.nullable}
                    for c in t_meta.columns
                ]
                schema_res = run_schema_check(
                    s_cols_dict, t_cols_dict, self.config.comparison
                )
                chk_status = (
                    CheckStatus.PASSED if schema_res.passed else CheckStatus.FAILED
                )
                checks_to_save.append(
                    CheckRecord(
                        check_id=f"{run_id}_schema",
                        run_id=run_id,
                        check_name="schema",
                        status=chk_status,
                        summary=schema_res.summary,
                        details_json=json.dumps(
                            [
                                {
                                    "col": d.column_name,
                                    "src": d.source_type,
                                    "tgt": d.target_type,
                                    "severity": d.severity.value,
                                    "msg": d.message,
                                }
                                for d in schema_res.diffs
                            ]
                        ),
                    )
                )
                if schema_res.severity == SchemaDiffSeverity.BLOCKING:
                    self.repo.save_checks(checks_to_save)
                    self.repo.update_run_status(
                        run_id,
                        RunStatus.FAILED,
                        error_message=schema_res.summary,
                        completed_at=datetime.now(timezone.utc),
                    )
                    return self.repo.get_run(run_id)  # type: ignore

            # 3. Row count check if enabled
            if self.config.comparison.checks.row_count:
                s_cnt = self.source_connector.get_filtered_row_count(
                    table=self.config.source.table,
                    schema=self.config.source.schema_,
                    database=self.config.source.database,
                    catalog=self.config.source.catalog,
                )
                t_cnt = self.target_connector.get_filtered_row_count(
                    table=self.config.target.table,
                    schema=self.config.target.schema_,
                    database=self.config.target.database,
                    catalog=self.config.target.catalog,
                )
                cnt_res = run_count_check(s_cnt, t_cnt)
                checks_to_save.append(
                    CheckRecord(
                        check_id=f"{run_id}_row_count",
                        run_id=run_id,
                        check_name="row_count",
                        status=CheckStatus.PASSED if cnt_res.passed else CheckStatus.FAILED,
                        summary=cnt_res.summary,
                        details_json=json.dumps(
                            {
                                "source_count": s_cnt,
                                "target_count": t_cnt,
                                "delta": cnt_res.delta,
                            }
                        ),
                    )
                )

            self.repo.save_checks(checks_to_save)

            # 4. Partition Planning (if not already planned)
            existing_parts = self.repo.get_partitions(run_id)
            if not existing_parts:
                col_name = (
                    self.config.comparison.partition.column
                    or self.config.comparison.unique_keys[0]
                )
                col_stats = self.source_connector.get_column_stats(
                    table=self.config.source.table,
                    column=col_name,
                    schema=self.config.source.schema_,
                    database=self.config.source.database,
                    catalog=self.config.source.catalog,
                )
                planned_parts = plan_partitions(
                    run_id=run_id,
                    partition_config=self.config.comparison.partition,
                    column_min=col_stats.min_value,
                    column_max=col_stats.max_value,
                    estimated_row_count=s_meta.estimated_row_count or 0,
                )
                self.repo.save_partitions(planned_parts)

            # 5. Partition execution loop (Processes only unfinished partitions)
            unfinished = self.repo.get_unfinished_partitions(run_id)
            self.repo.log_event(
                run_id,
                "INFO",
                "Coordinator",
                f"Starting partition comparison. Unfinished partitions: {len(unfinished)}",
            )

            for part in unfinished:
                self.cancel_token.check_cancelled()

                # Lease partition
                acquired = self.repo.acquire_partition_lease(
                    partition_id=part.partition_id,
                    worker_id=self.worker_id,
                    lease_duration_seconds=self.config.execution.lease_duration_seconds,
                )
                if not acquired:
                    continue

                self.repo.update_run_heartbeat(run_id)

                # Fetch records from source
                s_records: list[dict[str, Any]] = []
                for s_batch in self.source_connector.fetch_records_batch(
                    table=self.config.source.table,
                    schema=self.config.source.schema_,
                    database=self.config.source.database,
                    catalog=self.config.source.catalog,
                    filter_expr=part.cursor_state
                    if part.cursor_state and not part.cursor_state.startswith("{")
                    else None,
                    batch_size=self.config.execution.batch_size,
                ):
                    s_records.extend(s_batch)

                # Fetch records from target
                t_records: list[dict[str, Any]] = []
                for t_batch in self.target_connector.fetch_records_batch(
                    table=self.config.target.table,
                    schema=self.config.target.schema_,
                    database=self.config.target.database,
                    catalog=self.config.target.catalog,
                    filter_expr=part.cursor_state
                    if part.cursor_state and not part.cursor_state.startswith("{")
                    else None,
                    batch_size=self.config.execution.batch_size,
                ):
                    t_records.extend(t_batch)

                # Compare records
                comp_res = compare_records_batch(
                    source_records=s_records,
                    target_records=t_records,
                    unique_keys=self.config.comparison.unique_keys,
                    ignored_columns=self.config.comparison.ignored_columns,
                    normalization_config=self.config.comparison.normalization,
                    run_id=run_id,
                    partition_id=part.partition_id,
                )

                # Atomic commit of partition results and detected discrepancies
                self.repo.commit_partition_results(
                    partition_id=part.partition_id,
                    status=PartitionStatus.COMPLETED,
                    discrepancies=comp_res.discrepancies,
                    rows_source=comp_res.rows_source,
                    rows_target=comp_res.rows_target,
                    rows_compared=comp_res.rows_compared,
                    missing_count=comp_res.missing_count,
                    extra_count=comp_res.extra_count,
                    changed_count=comp_res.changed_count,
                )

            # 6. Finalize Run
            final_run = self.repo.get_run(run_id)
            assert final_run is not None

            # Check if any discrepancies
            total_discrepancies = (
                final_run.missing_count
                + final_run.extra_count
                + final_run.changed_count
                + final_run.duplicate_key_count
                + final_run.null_key_count
            )

            final_status = (
                RunStatus.COMPLETED_WITH_DISCREPANCIES
                if total_discrepancies > 0
                else RunStatus.COMPLETED
            )
            self.repo.update_run_status(
                run_id,
                final_status,
                completed_at=datetime.now(timezone.utc),
            )
            self.repo.log_event(
                run_id,
                "INFO",
                "Coordinator",
                f"Validation finished with status: {final_status.value}. Total discrepancies: {total_discrepancies}",
            )

            # 7. Generate Reports
            report_data = build_report_data(
                self.repo,
                run_id,
                mask_sensitive=self.config.reporting.mask_sensitive_values,
            )
            out_dir = self.config.reporting.output_dir

            for fmt in self.config.reporting.formats:
                if fmt == "json":
                    p = export_json_report(report_data, out_dir)
                    self.repo.save_report_reference(
                        ReportReference(run_id=run_id, format="json", file_path=str(p))
                    )
                elif fmt == "csv":
                    p = export_csv_report(report_data, out_dir)
                    self.repo.save_report_reference(
                        ReportReference(run_id=run_id, format="csv", file_path=str(p))
                    )
                elif fmt == "html":
                    p = export_html_report(report_data, out_dir)
                    self.repo.save_report_reference(
                        ReportReference(run_id=run_id, format="html", file_path=str(p))
                    )

            return self.repo.get_run(run_id)  # type: ignore

        except ValidationCancelledError as e:
            self.repo.update_run_status(
                run_id,
                RunStatus.CANCELLED,
                error_message=str(e),
                completed_at=datetime.now(timezone.utc),
            )
            self.repo.log_event(run_id, "WARNING", "Coordinator", f"Run cancelled: {e}")
            return self.repo.get_run(run_id)  # type: ignore

        except Exception as e:
            logger.exception("Validation run failed unexpectedly.")
            self.repo.update_run_status(
                run_id,
                RunStatus.FAILED,
                error_message=str(e),
                completed_at=datetime.now(timezone.utc),
            )
            self.repo.log_event(
                run_id, "ERROR", "Coordinator", f"Run failed with error: {e}"
            )
            return self.repo.get_run(run_id)  # type: ignore

        finally:
            if self.source_connector:
                self.source_connector.close()
            if self.target_connector:
                self.target_connector.close()
