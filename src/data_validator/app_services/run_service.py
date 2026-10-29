"""Application service for orchestrating validation runs and durable state."""
from __future__ import annotations

import threading
from typing import Any

from data_validator.config.models import ValidationJobConfig
from data_validator.execution.cancellation import CancellationToken
from data_validator.execution.coordinator import ValidationCoordinator
from data_validator.persistence.models import RunRecord, RunStatus
from data_validator.persistence.repository import BaseRunRepository
from data_validator.persistence.sqlite_repository import SQLiteRunRepository
from data_validator.security.secrets import SecretStore, default_secret_store


class RunService:
    """Service for launching, resuming, monitoring, and cancelling validation runs."""

    def __init__(
        self,
        repository: BaseRunRepository | None = None,
        secret_store: SecretStore | None = None,
    ) -> None:
        self.repo = repository or SQLiteRunRepository()
        self.secret_store = secret_store or default_secret_store
        self._active_tokens: dict[str, CancellationToken] = {}
        self._lock = threading.Lock()

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.repo.get_run(run_id)

    def list_runs(
        self,
        limit: int = 50,
        status: RunStatus | None = None,
        job_name: str | None = None,
    ) -> list[RunRecord]:
        return self.repo.list_runs(limit=limit, status=status, job_name=job_name)

    def recover_stale_runs(self, timeout_seconds: int = 120) -> int:
        return self.repo.recover_stale_leases(timeout_seconds=timeout_seconds)

    def cancel_run(self, run_id: str, reason: str = "Cancelled by user") -> bool:
        with self._lock:
            if run_id in self._active_tokens:
                self._active_tokens[run_id].cancel(reason)
                self.repo.update_run_status(run_id, RunStatus.CANCEL_REQUESTED)
                return True
        # If not active in this process, update DB directly
        run = self.repo.get_run(run_id)
        if run and run.status in (RunStatus.RUNNING, RunStatus.QUEUED):
            self.repo.update_run_status(run_id, RunStatus.CANCELLED, error_message=reason)
            return True
        return False

    def start_validation(self, config: ValidationJobConfig) -> RunRecord:
        """Start a validation run synchronously."""
        token = CancellationToken()
        coord = ValidationCoordinator(
            config=config,
            repository=self.repo,
            secret_store=self.secret_store,
            cancellation_token=token,
        )
        # Run
        return coord.run()

    def resume_validation(self, run_id: str) -> RunRecord:
        """Resume an interrupted or failed validation run."""
        run = self.repo.get_run(run_id)
        if not run:
            raise ValueError(f"Run '{run_id}' not found.")

        import json
        config_dict = json.loads(run.config_json)
        config = ValidationJobConfig.model_validate(config_dict)

        token = CancellationToken()
        with self._lock:
            self._active_tokens[run_id] = token

        coord = ValidationCoordinator(
            config=config,
            repository=self.repo,
            secret_store=self.secret_store,
            cancellation_token=token,
        )
        try:
            return coord.run(resume_run_id=run_id)
        finally:
            with self._lock:
                if run_id in self._active_tokens:
                    del self._active_tokens[run_id]
