"""Project runner: builds connectors once, validates tables in parallel, aggregates results."""

from __future__ import annotations

import datetime as dt
import fnmatch
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

from datavalidator import __version__
from datavalidator.config import ConnectionConfig, ProjectConfig, TableConfig
from datavalidator.connectors import Connector, create_connector
from datavalidator.engine import TableValidator
from datavalidator.log import get_logger
from datavalidator.models import CheckResult, RunResult, Status, TableResult

logger = get_logger("runner")


def select_tables(project: ProjectConfig, names: Optional[List[str]] = None,
                  tags: Optional[List[str]] = None) -> List[TableConfig]:
    tables = project.tables
    if names:
        tables = [t for t in tables if any(fnmatch.fnmatch(t.name, pat) for pat in names)]
    if tags:
        tables = [t for t in tables if set(tags) & set(t.tags)]
    return tables


ConnectorFactory = Callable[[str, ConnectionConfig], Connector]


class ProjectRunner:
    """Validates the tables of a project.

    ``connector_factory`` builds a connector from a connection config (default
    :func:`create_connector`). ``cancel_event`` stops the run at the next step boundary: tables
    not started are reported as cancelled. ``on_table_done`` is called with each finished
    :class:`TableResult`.
    """

    def __init__(self, project: ProjectConfig, connectors: Optional[Dict[str, Connector]] = None,
                 progress: Optional[Callable[[str, str], None]] = None,
                 connector_factory: Optional[ConnectorFactory] = None,
                 cancel_event: Optional[threading.Event] = None,
                 on_table_done: Optional[Callable[[TableResult], None]] = None):
        self.project = project
        self._connectors: Dict[str, Connector] = dict(connectors or {})
        self.progress = progress
        self.connector_factory = connector_factory or create_connector
        self.cancel_event = cancel_event
        self.on_table_done = on_table_done
        self._lock = threading.Lock()

    def connector(self, name: str) -> Connector:
        with self._lock:
            if name not in self._connectors:
                self._connectors[name] = self.connector_factory(name, self.project.connections[name])
            return self._connectors[name]

    def test_connections(self, names: Optional[List[str]] = None) -> Dict[str, Dict]:
        out = {}
        for name in names or list(self.project.connections):
            cfg = self.project.connections[name]
            try:
                info = self.connector(name).test()
            except Exception as exc:  # noqa: BLE001  (driver missing, bad config ...)
                info = {"ok": False, "latency_ms": None, "message": str(exc)}
            info.update({"type": cfg.type, "cloud": cfg.cloud or ""})
            out[name] = info
        return out

    def run(self, tables: Optional[List[TableConfig]] = None, parallel: Optional[int] = None) -> RunResult:
        tables = tables if tables is not None else self.project.tables
        started = dt.datetime.now(dt.timezone.utc)
        run = RunResult(
            run_id=f"{started.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
            project=self.project.project, started_at=started.isoformat(timespec="seconds"),
            tool_version=__version__,
        )
        used = sorted({t.source.connection for t in tables} | {t.target.connection for t in tables})
        run.connections = {n: {"type": self.project.connections[n].type,
                               "cloud": self.project.connections[n].cloud or ""} for n in used}
        t0 = time.perf_counter()
        workers = max(1, parallel or self.project.parallel_tables)

        def one(tc: TableConfig) -> TableResult:
            if self.cancel_event is not None and self.cancel_event.is_set():
                return TableResult(name=tc.name, source=tc.source.connection, target=tc.target.connection,
                                   status=Status.ERROR, error="cancelled by user", tags=tc.tags,
                                   checks=[CheckResult(check="engine", name="Cancelled", status=Status.ERROR,
                                                       message="run cancelled before this table started")])
            try:
                src = self.connector(tc.source.connection)
                tgt = self.connector(tc.target.connection)
            except Exception as exc:  # noqa: BLE001
                return TableResult(name=tc.name, source=tc.source.connection, target=tc.target.connection,
                                   status=Status.ERROR, error=str(exc), tags=tc.tags,
                                   checks=[CheckResult(check="engine", name="Connect", status=Status.ERROR,
                                                       message=str(exc))])
            return TableValidator(tc, src, tgt, progress=self.progress, cancel_event=self.cancel_event).run()

        results: Dict[str, TableResult] = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dv-table") as ex:
            futs = {ex.submit(one, t): t.name for t in tables}
            for f in as_completed(futs):
                r = f.result()
                results[r.name] = r
                if self.on_table_done is not None:
                    self.on_table_done(r)
        run.tables = [results[t.name] for t in tables]
        run.status = Status.worst([t.status for t in run.tables])
        run.cancelled = bool(self.cancel_event is not None and self.cancel_event.is_set())
        run.finished_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        run.duration_s = round(time.perf_counter() - t0, 2)
        return run

    def close(self) -> None:
        for c in self._connectors.values():
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
