"""Report builder that pulls stored run state and discrepancies from repository without re-running validation."""
from __future__ import annotations

import json
from typing import Any

from data_validator.persistence.models import DiscrepancyRecord
from data_validator.persistence.repository import BaseRunRepository
from data_validator.reporting.models import ReportSummary, ValidationReportData
from data_validator.security.redaction import mask_value


def build_report_data(
    repo: BaseRunRepository,
    run_id: str,
    mask_sensitive: bool = True,
    discrepancy_limit: int = 500,
) -> ValidationReportData:
    """Build report data structure directly from saved repository results."""
    run = repo.get_run(run_id)
    if not run:
        raise ValueError(f"Run '{run_id}' not found in metadata repository.")

    cfg: dict[str, Any] = {}
    if run.config_json:
        try:
            cfg = json.loads(run.config_json)
        except Exception:
            cfg = {}

    src_tbl = f"{cfg.get('source', {}).get('database', '')}.{cfg.get('source', {}).get('table', '')}"
    tgt_tbl = f"{cfg.get('target', {}).get('database', '')}.{cfg.get('target', {}).get('table', '')}"

    duration = None
    if run.started_at and run.completed_at:
        duration = (run.completed_at - run.started_at).total_seconds()

    total_disc = (
        run.missing_count
        + run.extra_count
        + run.changed_count
        + run.duplicate_key_count
        + run.null_key_count
    )

    summary = ReportSummary(
        run_id=run.run_id,
        job_name=run.job_name,
        status=run.status.value,
        source_table=src_tbl,
        target_table=tgt_tbl,
        rows_source=run.rows_source,
        rows_target=run.rows_target,
        rows_compared=run.rows_compared,
        missing_count=run.missing_count,
        extra_count=run.extra_count,
        changed_count=run.changed_count,
        duplicate_key_count=run.duplicate_key_count,
        null_key_count=run.null_key_count,
        total_discrepancies=total_disc,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_seconds=duration,
    )

    checks = [
        {
            "check_name": c.check_name,
            "status": c.status.value,
            "summary": c.summary,
            "executed_at": c.executed_at.isoformat(),
        }
        for c in repo.get_checks(run_id)
    ]

    raw_discs = repo.get_discrepancies(run_id, limit=discrepancy_limit)
    discrepancies: list[dict[str, Any]] = []

    for d in raw_discs:
        s_val = d.source_value
        t_val = d.target_value
        if mask_sensitive:
            if s_val and not s_val.startswith("{"):
                s_val = mask_value(s_val)
            if t_val and not t_val.startswith("{"):
                t_val = mask_value(t_val)

        discrepancies.append(
            {
                "id": d.id,
                "partition_id": d.partition_id,
                "type": d.discrepancy_type.value,
                "key": d.key_values,
                "column": d.column_name,
                "source_value": s_val,
                "target_value": t_val,
                "detected_at": d.detected_at.isoformat(),
            }
        )

    logs = [
        {
            "level": log.level,
            "component": log.component,
            "message": log.message,
            "created_at": log.created_at.isoformat(),
        }
        for log in repo.get_event_logs(run_id, limit=100)
    ]

    return ValidationReportData(
        summary=summary,
        config_snapshot=cfg,
        checks=checks,
        discrepancies=discrepancies,
        event_logs=logs,
    )
