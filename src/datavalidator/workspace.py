"""
An editable project plus the engine actions a front end needs.

:class:`Workspace` holds a project as a plain dict (``data``), exactly as it appears in a YAML
file: secret references stay unresolved and ``defaults`` stay separate from table entries.
Every action builds a :class:`ProjectConfig` from it and calls the engine, so a project made
in the web UI behaves the same as the exported YAML run by ``dvtool validate -c``.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from datavalidator.config import (
    ConnectionConfig,
    ProjectConfig,
    parse_project_text,
    project_from_dict,
    read_project_dict,
)
from datavalidator.connectors import Connector, create_connector
from datavalidator.discovery import MatchResult, TableDescription, describe_table, match_tables
from datavalidator.engine import TableExplain, explain_table
from datavalidator.jobs import ValidationJob
from datavalidator.models import RunResult
from datavalidator.project_io import (
    PlaintextSecret,
    dump_project,
    externalize_secrets,
    find_plaintext_secrets,
)
from datavalidator.reports import REPORT_FORMATS, load_run, read_history, run_files
from datavalidator.runner import ConnectorFactory, ProjectRunner
from datavalidator.secrets import resolve


def new_project_dict(name: str = "new-project") -> Dict[str, Any]:
    f = ProjectConfig.model_fields
    return {"project": name, "parallel_tables": f["parallel_tables"].default,
            "output_dir": f["output_dir"].default, "connections": {}, "defaults": {}, "tables": []}


def format_validation_error(exc: Exception) -> List[str]:
    if isinstance(exc, ValidationError):
        out = []
        for e in exc.errors():
            loc = ".".join(str(x) for x in e.get("loc", ()))
            out.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
        return out
    return [str(exc)]


class Workspace:
    def __init__(self, data: Optional[Dict[str, Any]] = None,
                 connector_factory: Optional[ConnectorFactory] = None):
        self.data: Dict[str, Any] = new_project_dict()
        if data:
            self.data.update(copy.deepcopy(data))
        self.data.setdefault("connections", {})
        self.data.setdefault("defaults", {})
        self.data.setdefault("tables", [])
        self._factory: ConnectorFactory = connector_factory or create_connector
        self._conns: Dict[str, Tuple[str, Connector]] = {}
        self._cache: Dict[Tuple, Any] = {}

    # ------------------------------------------------------------------ load / save
    @classmethod
    def from_yaml(cls, text: str, base_dir: Optional[str] = None, **kw) -> "Workspace":
        ws = cls(parse_project_text(text, base_dir), **kw)
        ws.project(resolve_secrets=False)  # raise early on an invalid file
        return ws

    @classmethod
    def from_path(cls, path: str, **kw) -> "Workspace":
        ws = cls(read_project_dict(path), **kw)
        ws.project(resolve_secrets=False)
        return ws

    def project(self, resolve_secrets: bool = True) -> ProjectConfig:
        return project_from_dict(self.data, resolve_secrets=resolve_secrets)

    def errors(self) -> List[str]:
        """Problems that stop the project from loading (secret references are not resolved)."""
        try:
            self.project(resolve_secrets=False)
            return []
        except Exception as exc:  # noqa: BLE001
            return format_validation_error(exc)

    def plaintext_secrets(self) -> List[PlaintextSecret]:
        return find_plaintext_secrets(self.data.get("connections", {}))

    def externalize_secrets(self, names: Optional[Dict[Tuple[str, str], str]] = None,
                            set_env: bool = True) -> Dict[str, str]:
        """Replace typed secrets with ``${env:VAR}``; the values go to this process's environment.

        Returns ``{VAR: value}`` so the caller can tell the user which variables to set.
        """
        conns, env = externalize_secrets(self.data.get("connections", {}), names)
        self.data["connections"] = conns
        if set_env:
            os.environ.update(env)
        return env

    def export_yaml(self) -> str:
        """YAML for ``dvtool validate -c``; refuses plaintext secrets (see externalize_secrets)."""
        return dump_project(self.project(resolve_secrets=False))

    # ------------------------------------------------------------------ connections
    def connection_names(self) -> List[str]:
        return list(self.data.get("connections", {}))

    def set_connection(self, name: str, values: Dict[str, Any], old_name: Optional[str] = None) -> None:
        conns = self.data.setdefault("connections", {})
        if old_name and old_name != name:
            if name in conns:
                raise ValueError(f"connection '{name}' already exists")
            conns = {(name if k == old_name else k): v for k, v in conns.items()}
            for t in self.data.get("tables", []):
                for side in ("source", "target"):
                    if t.get(side, {}).get("connection") == old_name:
                        t[side]["connection"] = name
            self._drop(old_name)
        conns[name] = {k: v for k, v in values.items() if v not in (None, "", [], {})}
        self.data["connections"] = conns
        self._drop(name)

    def delete_connection(self, name: str) -> List[str]:
        """Remove a connection; returns the tables that still reference it (they must be fixed)."""
        self.data.get("connections", {}).pop(name, None)
        self._drop(name)
        return [t.get("name", "?") for t in self.data.get("tables", [])
                if name in (t.get("source", {}).get("connection"), t.get("target", {}).get("connection"))]

    def connection_usage(self, name: str) -> Dict[str, List[str]]:
        """Tables that use a connection, by side: {"source": [...], "target": [...]}."""
        out: Dict[str, List[str]] = {"source": [], "target": []}
        for t in self.data.get("tables", []):
            for side in out:
                if t.get(side, {}).get("connection") == name:
                    out[side].append(t.get("name", "?"))
        return out

    def connection_role(self, name: str) -> Optional[str]:
        """The connection's role: as set on it, else from the tables using it, else None."""
        role = self.data["connections"].get(name, {}).get("role")
        if role:
            return role
        used = self.connection_usage(name)
        if used["source"] and used["target"]:
            return "both"
        return "source" if used["source"] else ("target" if used["target"] else None)

    def connections_for(self, role: str) -> List[str]:
        """Connections meant for a side ("source" or "target"); "both" counts for either."""
        return [n for n in self.connection_names() if self.connection_role(n) in (role, "both")]

    def connection_config(self, name: str, resolve_secrets: bool = True) -> ConnectionConfig:
        raw = self.data["connections"][name]
        if resolve_secrets:
            raw = resolve({name: raw})[name]
        return ConnectionConfig.model_validate(raw)

    def _fingerprint(self, name: str) -> str:
        return json.dumps(self.data["connections"].get(name), sort_keys=True, default=str)

    def _drop(self, name: str) -> None:
        old = self._conns.pop(name, None)
        if old:
            try:
                old[1].close()
            except Exception:  # noqa: BLE001
                pass
        self._cache = {k: v for k, v in self._cache.items() if k[1] != name}

    def connector(self, name: str) -> Connector:
        fp = self._fingerprint(name)
        cached = self._conns.get(name)
        if cached and cached[0] == fp:
            return cached[1]
        self._drop(name)
        conn = self._factory(name, self.connection_config(name))
        self._conns[name] = (fp, conn)
        return conn

    def test_connection(self, name: str) -> Dict[str, Any]:
        """Connectivity check through :meth:`ProjectRunner.test_connections`."""
        try:
            cfg = self.connection_config(name)
        except Exception as exc:  # noqa: BLE001
            raw = self.data["connections"].get(name, {})
            return {"ok": False, "latency_ms": None, "message": "; ".join(format_validation_error(exc)),
                    "type": raw.get("type", ""), "cloud": raw.get("cloud", "")}
        single = ProjectConfig(project=self.data.get("project") or "test", connections={name: cfg}, tables=[])
        cached = self._conns.get(name)
        pre = {name: cached[1]} if cached and cached[0] == self._fingerprint(name) else None
        runner = ProjectRunner(single, connectors=pre, connector_factory=self._factory)
        res = runner.test_connections([name])[name]
        if pre is None and name in runner._connectors:
            self._conns[name] = (self._fingerprint(name), runner._connectors[name])
        return res

    # ------------------------------------------------------------------ discovery (cached)
    def _cached(self, key: Tuple, fn):
        full = (key[0], key[1], self._fingerprint(key[1])) + tuple(key[2:])
        if full not in self._cache:
            self._cache[full] = fn()
        return self._cache[full]

    def clear_discovery_cache(self, name: Optional[str] = None) -> None:
        self._cache = {k: v for k, v in self._cache.items() if name is not None and k[1] != name}

    def schemas(self, name: str) -> List[str]:
        return self._cached(("schemas", name), lambda: self.connector(name).list_schemas())

    def tables(self, name: str, schema: Optional[str] = None) -> List[str]:
        return self._cached(("tables", name, schema or ""), lambda: self.connector(name).list_tables(schema or None))

    def describe(self, name: str, table: str, schema: Optional[str] = None) -> TableDescription:
        return self._cached(("describe", name, schema or "", table),
                            lambda: describe_table(self.connector(name), table, schema or None))

    def match(self, source: str, source_schema: Optional[str], target: str, target_schema: Optional[str],
              include: str = "*", exclude: str = "") -> MatchResult:
        return match_tables(self.connector(source), source_schema or None, self.connector(target),
                            target_schema or None, include or "*", exclude or "")

    # ------------------------------------------------------------------ tables
    def table_names(self) -> List[str]:
        return [t.get("name", "") for t in self.data.get("tables", [])]

    def table_index(self, name: str) -> int:
        for i, t in enumerate(self.data.get("tables", [])):
            if t.get("name") == name:
                return i
        raise KeyError(name)

    def add_tables(self, entries: Sequence[Dict[str, Any]]) -> List[str]:
        """Append table entries; returns names skipped because they already exist."""
        existing, skipped = set(self.table_names()), []
        for e in entries:
            if e.get("name") in existing:
                skipped.append(e["name"])
                continue
            entry = {k: v for k, v in copy.deepcopy(e).items() if v not in (None, [], {})}
            self.data.setdefault("tables", []).append(entry)
            existing.add(e["name"])
        return skipped

    def effective_table(self, name: str):
        """The table with project defaults applied (secrets not resolved)."""
        for t in self.project(resolve_secrets=False).tables:
            if t.name == name:
                return t
        raise KeyError(name)

    # ------------------------------------------------------------------ explain / run
    def explain(self, table: str) -> TableExplain:
        project = self.project()
        tc = next(t for t in project.tables if t.name == table)
        return explain_table(tc, self.connector(tc.source.connection), self.connector(tc.target.connection))

    def start_run(self, tables: Optional[Sequence[str]] = None, tags: Optional[Sequence[str]] = None,
                  mode: Optional[str] = None, parallel: Optional[int] = None,
                  formats: Sequence[str] = REPORT_FORMATS) -> ValidationJob:
        """Start a validation in a background thread (fresh connectors, separate from the UI's)."""
        job = ValidationJob(self.project(), tables=tables, tags=tags, mode=mode, parallel=parallel,
                            formats=formats, connector_factory=self._factory)
        return job.start()

    # ------------------------------------------------------------------ history
    @property
    def output_dir(self) -> str:
        return self.data.get("output_dir") or ProjectConfig.model_fields["output_dir"].default

    def history(self) -> List[Dict[str, Any]]:
        return read_history(self.output_dir)

    def load_run(self, run_id: str) -> RunResult:
        return load_run(self.output_dir, run_id)

    def run_files(self, run_id: str) -> Dict[str, str]:
        return run_files(self.output_dir, run_id)

    def close(self) -> None:
        for name in list(self._conns):
            self._drop(name)
