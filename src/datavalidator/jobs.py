"""
Background validation runs with live progress and cancellation.

A :class:`ValidationJob` runs :class:`~datavalidator.runner.ProjectRunner` in a worker thread,
records progress messages per table, and writes the reports when the run finishes. Front ends
poll :meth:`ValidationJob.snapshot` instead of blocking.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from datavalidator.config import ProjectConfig
from datavalidator.connectors import Connector
from datavalidator.log import get_logger, mask
from datavalidator.models import RunResult, TableResult
from datavalidator.reports import REPORT_FORMATS, write_all
from datavalidator.runner import ConnectorFactory, ProjectRunner, select_tables

logger = get_logger("jobs")

QUEUED, RUNNING, DONE = "queued", "running", "done"


@dataclass
class TableProgress:
    name: str
    state: str = QUEUED
    message: str = ""
    status: Optional[str] = None      # PASS / WARN / FAIL / ERROR / SKIPPED once done
    duration_s: Optional[float] = None


@dataclass
class JobSnapshot:
    state: str                         # queued | running | done
    elapsed_s: float
    tables: List[TableProgress]
    log: List[str]
    cancel_requested: bool
    error: Optional[str]
    result: Optional[RunResult]
    report_paths: Dict[str, str] = field(default_factory=dict)


class ValidationJob:
    def __init__(self, project: ProjectConfig, tables: Optional[Sequence[str]] = None,
                 tags: Optional[Sequence[str]] = None, mode: Optional[str] = None,
                 parallel: Optional[int] = None, out_dir: Optional[str] = None,
                 formats: Sequence[str] = REPORT_FORMATS,
                 connectors: Optional[Dict[str, Connector]] = None,
                 connector_factory: Optional[ConnectorFactory] = None,
                 max_log_lines: int = 2000):
        self.project = project
        selected = select_tables(project, list(tables) if tables else None, list(tags) if tags else None)
        self.tables = [t.model_copy(deep=True) for t in selected]
        if mode:
            for t in self.tables:
                t.compare.mode = mode
        self.parallel = parallel
        self.out_dir = out_dir or project.output_dir
        self.formats = list(formats)
        self._connectors = connectors
        self._factory = connector_factory
        self._max_log = max_log_lines

        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._progress: Dict[str, TableProgress] = {t.name: TableProgress(t.name) for t in self.tables}
        self._log: List[str] = []
        self._state = QUEUED
        self._t0: Optional[float] = None
        self._t1: Optional[float] = None
        self._error: Optional[str] = None
        self._result: Optional[RunResult] = None
        self._paths: Dict[str, str] = {}
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ control
    def start(self) -> "ValidationJob":
        if not self.tables:
            raise ValueError("no tables selected")
        if self._thread is not None:
            raise RuntimeError("job already started")
        self._t0 = time.perf_counter()
        self._state = RUNNING
        self._thread = threading.Thread(target=self._run, name="dv-job", daemon=True)
        self._thread.start()
        return self

    def cancel(self) -> None:
        """Stop at the next step boundary (a query already sent to an engine finishes first)."""
        self._cancel.set()
        self._append("cancel requested")

    def wait(self, timeout: Optional[float] = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
        return self.done

    @property
    def done(self) -> bool:
        return self._state == DONE

    @property
    def result(self) -> Optional[RunResult]:
        return self._result

    @property
    def report_paths(self) -> Dict[str, str]:
        return dict(self._paths)

    @property
    def error(self) -> Optional[str]:
        return self._error

    def snapshot(self) -> JobSnapshot:
        with self._lock:
            end = self._t1 if self._t1 is not None else time.perf_counter()
            return JobSnapshot(
                state=self._state, elapsed_s=round(end - self._t0, 1) if self._t0 else 0.0,
                tables=[TableProgress(**vars(p)) for p in self._progress.values()],
                log=list(self._log), cancel_requested=self._cancel.is_set(), error=self._error,
                result=self._result, report_paths=dict(self._paths))

    # ------------------------------------------------------------------ worker
    def _append(self, line: str) -> None:
        with self._lock:
            self._log.append(f"{time.strftime('%H:%M:%S')}  {mask(line)}")
            if len(self._log) > self._max_log:
                del self._log[: len(self._log) - self._max_log]

    def _on_progress(self, table: str, msg: str) -> None:
        with self._lock:
            p = self._progress.get(table)
            if p is not None and p.state != DONE:
                p.state, p.message = RUNNING, mask(msg)
        self._append(f"[{table}] {msg}")

    def _on_done(self, r: TableResult) -> None:
        with self._lock:
            p = self._progress.setdefault(r.name, TableProgress(r.name))
            p.state, p.status, p.duration_s = DONE, r.status.value, r.duration_s
            p.message = mask(r.error or f"{r.status.value} in {r.duration_s}s")

    def _run(self) -> None:
        runner = ProjectRunner(self.project, connectors=self._connectors, progress=self._on_progress,
                               connector_factory=self._factory, cancel_event=self._cancel,
                               on_table_done=self._on_done)
        try:
            run = runner.run(self.tables, parallel=self.parallel)
            paths = write_all(run, self.out_dir, self.formats)
            with self._lock:
                self._result, self._paths = run, paths
            self._append(f"run {run.run_id} finished: {run.status.value}"
                         + (" (cancelled)" if run.cancelled else ""))
        except Exception as exc:  # noqa: BLE001
            logger.exception("validation job failed")
            with self._lock:
                self._error = mask(str(exc))
            self._append(f"run failed: {exc}")
        finally:
            if self._connectors is None:
                runner.close()
            with self._lock:
                self._t1 = time.perf_counter()
                self._state = DONE
