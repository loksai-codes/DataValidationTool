"""
Writing projects back to YAML, and keeping secrets out of the files.

``dump_project`` is the inverse of :func:`datavalidator.config.project_from_dict`: loading its
output gives an equal :class:`ProjectConfig`. Table entries only keep what differs from the
project ``defaults`` (and the model defaults), so exported files stay short and readable.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import yaml

from datavalidator.config import ProjectConfig, TableConfig, TableDefaults, _deep_merge
from datavalidator.secrets import _SECRET_FIELD_HINTS
from datavalidator.ui_schema import is_reference, secret_keys

#: TableConfig fields always written, even when equal to a default
_ALWAYS = ("name", "source", "target")


class PlaintextSecretError(ValueError):
    def __init__(self, found: List["PlaintextSecret"]):
        self.found = found
        names = ", ".join(f"{s.connection}.{s.key}" for s in found)
        super().__init__(f"plaintext secrets would be written to YAML: {names}. "
                         "Replace them with ${env:VAR} (or another ${...}) references first.")


@dataclass(frozen=True)
class PlaintextSecret:
    connection: str
    key: str
    suggested_env: str


# ----------------------------------------------------------------------------- secrets
def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return k in secret_keys() or any(h in k for h in _SECRET_FIELD_HINTS)


def env_var_name(connection: str, key: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", f"DV_{connection}_{key}".upper()).strip("_")


def find_plaintext_secrets(connections: Dict[str, Dict[str, Any]]) -> List[PlaintextSecret]:
    """Secret fields holding a typed value instead of a ``${...}`` reference."""
    out = []
    for name, conn in (connections or {}).items():
        for key, value in (conn or {}).items():
            if not _is_secret_key(str(key)) or value in (None, ""):
                continue
            if not is_reference(value):
                out.append(PlaintextSecret(name, str(key), env_var_name(name, str(key))))
    return out


def externalize_secrets(connections: Dict[str, Dict[str, Any]],
                        names: Dict[Tuple[str, str], str] | None = None
                        ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """Replace plaintext secrets with ``${env:VAR}`` references.

    Returns the new connections dict and ``{VAR: plaintext}`` so the caller can export the
    variables (the values are never written to a file by this function).
    """
    conns = copy.deepcopy(connections or {})
    env: Dict[str, str] = {}
    for s in find_plaintext_secrets(conns):
        var = (names or {}).get((s.connection, s.key)) or s.suggested_env
        env[var] = str(conns[s.connection][s.key])
        conns[s.connection][s.key] = "${env:" + var + "}"
    return conns, env


# ----------------------------------------------------------------------------- dumping
def _dump_model(m) -> Dict[str, Any]:
    return m.model_dump(by_alias=True, mode="json")


def _table_baseline(defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Every TableConfig default (model defaults deep-merged with the project ``defaults``)."""
    base = _dump_model(TableDefaults())
    for name, f in TableConfig.model_fields.items():
        if name in base or f.is_required():
            continue
        v = f.get_default(call_default_factory=True)
        base[f.alias or name] = v.model_dump(by_alias=True, mode="json") if hasattr(v, "model_dump") else v
    return _deep_merge(base, defaults or {})


def _diff(value: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in value.items():
        b = base.get(k, _MISSING)
        if isinstance(v, dict) and isinstance(b, dict) and v and b:
            sub = _diff(v, b)
            if sub:
                out[k] = sub
        elif v != b:
            out[k] = v
    return out


_MISSING = object()


def project_to_dict(project: ProjectConfig, allow_plaintext_secrets: bool = False) -> Dict[str, Any]:
    """A minimal project dict that loads back to an equal :class:`ProjectConfig`."""
    conns = {name: c.model_dump(by_alias=True, mode="json", exclude_defaults=True)
             for name, c in project.connections.items()}
    for name, c in project.connections.items():
        conns[name]["type"] = c.type  # always written (it is required)
    if not allow_plaintext_secrets:
        found = find_plaintext_secrets(conns)
        if found:
            raise PlaintextSecretError(found)

    base = _table_baseline(project.defaults)
    tables = []
    for t in project.tables:
        full = _dump_model(t)
        entry = {k: ({kk: vv for kk, vv in full[k].items() if vv is not None} if isinstance(full[k], dict)
                     else full[k]) for k in _ALWAYS}
        entry.update({k: v for k, v in _diff(full, base).items() if k not in _ALWAYS})
        tables.append(entry)

    out: Dict[str, Any] = {"project": project.project}
    if project.description:
        out["description"] = project.description
    out["parallel_tables"] = project.parallel_tables
    out["output_dir"] = project.output_dir
    out["connections"] = conns
    if project.defaults:
        out["defaults"] = copy.deepcopy(project.defaults)
    out["tables"] = tables
    return out


def dump_project(project: ProjectConfig, allow_plaintext_secrets: bool = False) -> str:
    """YAML text that ``dvtool validate -c`` runs unchanged."""
    data = project_to_dict(project, allow_plaintext_secrets=allow_plaintext_secrets)
    header = ("# datavalidator project file (written by dvtool)\n"
              "# Run with: dvtool validate -c <this file>\n")
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
