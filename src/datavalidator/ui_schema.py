"""
Connection form specification.

One place describes which :class:`~datavalidator.config.ConnectionConfig` fields each driver
uses, with labels and help text. Front ends (the web UI) render forms from it, so a new engine
needs only an entry in ``CONNECTION_TYPES`` (config.py) and, for a new driver, a list here.

Defaults are never written here: they come from the Pydantic model (:func:`field_default`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

from datavalidator.config import CONNECTION_TYPES, ConnectionConfig

Kind = Literal["text", "int", "bool", "secret", "list", "mapping", "ssl", "file_tables"]
Group = Literal["connection", "credentials", "behaviour"]

GROUP_LABELS: Dict[str, str] = {
    "connection": "Connection",
    "credentials": "Credentials",
    "behaviour": "Behaviour",
}


@dataclass(frozen=True)
class FieldSpec:
    #: Python attribute name on ConnectionConfig (``schema_`` for the YAML key ``schema``)
    name: str
    label: str
    kind: Kind = "text"
    help: str = ""
    required: bool = False
    group: Group = "connection"
    placeholder: str = ""

    @property
    def key(self) -> str:
        """The key used in the YAML file (the field's alias when it has one)."""
        f = ConnectionConfig.model_fields[self.name]
        return f.alias or self.name

    @property
    def secret(self) -> bool:
        return self.kind == "secret"


@dataclass(frozen=True)
class DriverSpec:
    driver: str
    label: str
    description: str
    fields: Tuple[FieldSpec, ...]


# ----------------------------------------------------------------------------- field building blocks
def _f(name: str, label: str, kind: Kind = "text", help: str = "", required: bool = False,
       group: Group = "connection", placeholder: str = "") -> FieldSpec:
    return FieldSpec(name, label, kind, help, required, group, placeholder)


_HOST = _f("host", "Host", help="Endpoint host name or IP.", required=True)
_DATABASE = _f("database", "Database", help="Database to connect to.")
_USER = _f("user", "User", help="Read-only user is enough: only SELECT statements are run.", group="credentials")
_PASSWORD = _f("password", "Password", "secret", group="credentials",
               help="Plain value or a reference: ${env:VAR}, ${file:/path}, ${aws_secret:id#key}, "
                    "${alibaba_kms:name#key}.")
_SSL = _f("ssl", "TLS / SSL", "ssl",
          help="On, off, or driver options such as {sslmode: verify-full, sslrootcert: /path/ca.pem}.")
_SCHEMA = _f("schema_", "Default schema", help="Schema used when a table name has no schema prefix.")
_CONNECT_TIMEOUT = _f("connect_timeout", "Connect timeout (s)", "int", group="behaviour")
_REGION = _f("region", "Region", help="Cloud region of this connection.")
_ACCESS_KEY_ID = _f("access_key_id", "Access key ID", group="credentials",
                    help="Leave empty to use the default credential chain or the profile.")
_ACCESS_KEY_SECRET = _f("access_key_secret", "Access key secret", "secret", group="credentials",
                        help="Plain value or a ${...} reference.")
_SESSION_TOKEN = _f("session_token", "Session token", "secret", group="credentials",
                    help="Only for temporary credentials.")
_ENDPOINT = _f("endpoint", "Endpoint", help="Service endpoint URL.")

#: fields every connection has, shown after the driver-specific ones
COMMON_FIELDS: Tuple[FieldSpec, ...] = (
    _f("cloud", "Cloud", help="Free text shown in reports, e.g. aws, alibaba, onprem.", group="behaviour"),
    _f("description", "Description", group="behaviour"),
    _f("timezone", "Time zone of naive timestamps", group="behaviour",
       help="IANA zone that naive timestamps in this system are written in (e.g. UTC, Asia/Shanghai)."),
    _f("max_connections", "Max connections", "int", group="behaviour"),
    _f("query_timeout_seconds", "Query timeout (s)", "int", group="behaviour"),
    _f("session_init", "Session init statements", "list", group="behaviour",
       help="Extra statements run on each new session, one per line."),
)

DRIVERS: Dict[str, DriverSpec] = {
    "mysql": DriverSpec("mysql", "MySQL protocol", "MySQL, MariaDB and compatible services", (
        _HOST, _f("port", "Port", "int", placeholder="3306"), _DATABASE, _USER, _PASSWORD, _SSL,
        _CONNECT_TIMEOUT,
    )),
    "postgres": DriverSpec("postgres", "PostgreSQL protocol", "PostgreSQL and compatible services", (
        _HOST, _f("port", "Port", "int", placeholder="5432"), _DATABASE, _SCHEMA, _USER, _PASSWORD, _SSL,
        _CONNECT_TIMEOUT,
    )),
    "athena": DriverSpec("athena", "Athena", "Serverless SQL over S3 with the Glue catalog", (
        _REGION,
        _f("profile", "Profile", group="credentials", help="Named credentials profile on this machine."),
        _ACCESS_KEY_ID, _ACCESS_KEY_SECRET, _SESSION_TOKEN,
        _f("workgroup", "Workgroup", help="Use a workgroup with a bytes-scanned limit to cap cost."),
        _f("s3_staging_dir", "S3 staging dir", placeholder="s3://bucket/prefix/",
           help="Where query results are written. Optional if the workgroup sets one."),
        _f("catalog", "Data catalog", help="Leave empty for the default catalog."),
        _DATABASE, _SCHEMA,
    )),
    "maxcompute": DriverSpec("maxcompute", "MaxCompute", "ODPS SQL with results read through Tunnel", (
        _f("project", "Project", required=True, help="MaxCompute project name."),
        _f("endpoint", "Endpoint", required=True, help="Service endpoint URL of the project's region."),
        _f("tunnel_endpoint", "Tunnel endpoint", help="Optional; set when the default Tunnel is not reachable."),
        _ACCESS_KEY_ID, _ACCESS_KEY_SECRET, _SCHEMA,
        _f("hints", "SQL hints", "mapping",
           help="Session flags as key: value lines, e.g. odps.sql.allow.fullscan: \"true\"."),
    )),
    "files": DriverSpec("files", "Files", "Parquet, CSV or JSON files queried with DuckDB", (
        _REGION, _ENDPOINT, _ACCESS_KEY_ID, _ACCESS_KEY_SECRET, _SESSION_TOKEN,
        _f("tables", "File tables", "file_tables", required=True,
           help="Each logical table name maps to one or more paths (local, s3:// or oss://) and a format."),
    )),
    "sqlite": DriverSpec("sqlite", "SQLite", "Local database file", (
        _f("database", "Database file", required=True, help="Path to the .db file (:memory: for empty)."),
    )),
    "duckdb": DriverSpec("duckdb", "DuckDB", "Local DuckDB database file", (
        _f("database", "Database file", help="Path to the .duckdb file; empty for in-memory."),
    )),
}


# ----------------------------------------------------------------------------- queries
def driver_of(conn_type: str) -> str:
    return CONNECTION_TYPES[conn_type.strip().lower()][1]


def driver_spec(conn_type: str) -> DriverSpec:
    return DRIVERS[driver_of(conn_type)]


def fields_for(conn_type: str) -> List[FieldSpec]:
    """Every field shown for a connection type: driver-specific first, then common ones."""
    seen, out = set(), []
    for f in (*driver_spec(conn_type).fields, *COMMON_FIELDS):
        if f.name not in seen:
            seen.add(f.name)
            out.append(f)
    return out


def types_by_driver() -> Dict[str, List[str]]:
    """Connection types grouped by driver, in ``DRIVERS`` order (unknown drivers last)."""
    groups: Dict[str, List[str]] = {}
    for t, (_dialect, driver) in CONNECTION_TYPES.items():
        groups.setdefault(driver, []).append(t)
    order = list(DRIVERS) + sorted(set(groups) - set(DRIVERS))
    return {d: sorted(groups[d]) for d in order if d in groups}


def field_default(name: str) -> Any:
    """Default value of a ConnectionConfig field, taken from the Pydantic model."""
    return ConnectionConfig.model_fields[name].get_default(call_default_factory=True)


def secret_keys() -> set:
    """YAML keys that hold secrets for any driver."""
    keys = {f.key for spec in DRIVERS.values() for f in spec.fields if f.secret}
    keys |= {f.key for f in COMMON_FIELDS if f.secret}
    return keys


_REF = re.compile(r"\$\{[^}]+\}")


def is_reference(value: Any) -> bool:
    """True when a value is made only of ``${...}`` references (nothing typed in plain text)."""
    return isinstance(value, str) and bool(_REF.search(value)) and not _REF.sub("", value).strip()


def missing_required(conn_type: str, values: Dict[str, Any]) -> List[str]:
    """Labels of required fields that are empty in a raw connection dict."""
    out = []
    for f in fields_for(conn_type):
        if f.required and values.get(f.key) in (None, "", [], {}):
            out.append(f.label)
    return out


def type_label(conn_type: str, spec: Optional[DriverSpec] = None) -> str:
    spec = spec or driver_spec(conn_type)
    dialect = CONNECTION_TYPES[conn_type][0]
    return f"{conn_type}  ({spec.label}, {dialect} SQL)"
