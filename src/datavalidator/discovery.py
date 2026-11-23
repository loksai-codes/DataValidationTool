"""Catalog discovery: schemas, tables, columns, primary keys and table matching across two connections."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from datavalidator.config import DatasetRef
from datavalidator.connectors import Connector


@dataclass
class ColumnDescription:
    name: str
    native_type: str
    logical_type: str
    nullable: bool
    ordinal: int
    primary_key: bool = False


@dataclass
class TableDescription:
    connection: str
    schema: Optional[str]
    table: str
    columns: List[ColumnDescription]
    primary_keys: List[str]

    @property
    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]


@dataclass
class MatchResult:
    """Output of :func:`match_tables`; ``entries`` are ready-made ``tables:`` items."""
    entries: List[Dict[str, Any]] = field(default_factory=list)
    unmatched: List[str] = field(default_factory=list)      # source tables with no target table
    no_pk: List[str] = field(default_factory=list)          # matched tables with no primary key found
    target_only: List[str] = field(default_factory=list)    # target tables no source table matched


def list_schemas(conn: Connector) -> List[str]:
    return conn.list_schemas()


def list_tables(conn: Connector, schema: Optional[str] = None) -> List[str]:
    return conn.list_tables(schema or None)


def describe_table(conn: Connector, table: str, schema: Optional[str] = None) -> TableDescription:
    ref = DatasetRef(connection=conn.name, table=table, schema=schema)
    info = conn.get_table_info(ref)
    pk = conn.primary_keys(ref)
    cols = [ColumnDescription(c.name, c.type.native, c.logical.value, c.nullable, c.ordinal, c.name in pk)
            for c in info.columns]
    return TableDescription(conn.name, ref.schema, ref.table, cols, pk)


def _ref(conn_name: str, schema: Optional[str], table: str) -> Dict[str, str]:
    return {"connection": conn_name, "table": f"{schema}.{table}" if schema else table}


def match_tables(source: Connector, source_schema: Optional[str], target: Connector,
                 target_schema: Optional[str], include: str = "*", exclude: str = "") -> MatchResult:
    """Pair tables with the same name (case-insensitive) in two schemas, with detected primary keys."""
    s_tables = source.list_tables(source_schema or None)
    t_list = target.list_tables(target_schema or None)
    t_tables = {t.lower(): t for t in t_list}
    res = MatchResult()
    used = set()
    for st in s_tables:
        if not fnmatch.fnmatch(st, include) or (exclude and fnmatch.fnmatch(st, exclude)):
            continue
        tt = t_tables.get(st.lower())
        if not tt:
            res.unmatched.append(st)
            continue
        used.add(tt)
        s_ref = _ref(source.name, source_schema, st)
        t_ref = _ref(target.name, target_schema, tt)
        pk = source.primary_keys(DatasetRef(**s_ref)) or target.primary_keys(DatasetRef(**t_ref))
        res.entries.append({"name": st, "source": s_ref, "target": t_ref, "keys": pk})
        if not pk:
            res.no_pk.append(st)
    res.target_only = [t for t in t_list if t not in used]
    return res


def tables_yaml(result: MatchResult) -> str:
    """The ``tables:`` YAML written by ``dvtool generate-tables``."""
    text = yaml.safe_dump({"tables": result.entries}, sort_keys=False, allow_unicode=True)
    if result.no_pk:
        text += ("\n# No primary key found (set 'keys' to enable row-level comparison):\n"
                 + "".join(f"#   - {u}\n" for u in result.no_pk))
    if result.unmatched:
        text += "\n# Source tables with no matching target table:\n" + "".join(f"#   - {u}\n" for u in result.unmatched)
    return text
