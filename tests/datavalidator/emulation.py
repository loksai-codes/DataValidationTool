"""
Test doubles that let the MySQL, Athena and MaxCompute *dialect SQL* run end-to-end on SQLite.

Each engine's functions (MD5 / to_hex / from_base / CONV / TIMESTAMPDIFF / DATEDIFF / to_unixtime)
are registered as SQLite UDFs with the same semantics, and fake Athena (boto3) and MaxCompute
(PyODPS) clients route queries to SQLite. This exercises the real executors (CSV + NULL
sentinel for Athena, Tunnel reader for MaxCompute) and every generated query shape.
"""

from __future__ import annotations

import calendar
import hashlib
import io
import re
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd


def _parse_ts(v: Any) -> datetime:
    s = str(v)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(s)


def _epoch(v: Any) -> float:
    d = _parse_ts(v)
    return calendar.timegm(d.timetuple()) + d.microsecond / 1e6


def connect(path: str, engine: str) -> sqlite3.Connection:
    c = sqlite3.connect(path, check_same_thread=False)
    if engine == "athena":
        c.create_function("to_utf8", 1, lambda s: None if s is None else str(s).encode("utf-8"))
        c.create_function("md5", 1, lambda b: None if b is None else hashlib.md5(b).digest())
        c.create_function("to_hex", 1, lambda b: None if b is None else bytes(b).hex().upper())
        c.create_function("from_base", 2, lambda s, base: None if s is None else int(s, base))
        c.create_function("to_unixtime", 1, lambda v: None if v is None else _epoch(v))
    else:  # mysql & maxcompute: MD5 returns lower-case hex text
        c.create_function("md5", 1, lambda s: None if s is None else hashlib.md5(str(s).encode()).hexdigest())
        c.create_function("conv", 3, lambda s, f, t: None if s is None else str(int(str(s), int(f))))
        c.create_function("timestampdiff", 3, lambda u, a, b: None if b is None else int(_epoch(b) - _epoch(a)))
        c.create_function("datediff", 3, lambda a, b, u: None if a is None else int(_epoch(a) - _epoch(b)))
    return c


def rewrite(sql: str, engine: str) -> str:
    """Minimal token rewrites for constructs SQLite parses differently."""
    if engine == "mysql":
        sql = sql.replace("TIMESTAMPDIFF(SECOND,", "TIMESTAMPDIFF('SECOND',")
        sql = sql.replace("AS UNSIGNED)", "AS INTEGER)")
    if engine == "maxcompute":
        sql = sql.replace("AS STRING)", "AS TEXT)").replace("AS DATETIME)", "AS TEXT)")
    return sql


def _catalog_columns(conn: sqlite3.Connection, table: str, engine: str) -> List[tuple]:
    out = []
    for cid, name, typ, notnull, _d, _pk in conn.execute(f"PRAGMA table_info('{table}')"):
        t = typ.lower()
        if engine == "athena":
            t = {"datetime": "timestamp(3)", "timestamp": "timestamp(3)", "real": "double"}.get(t, t)
            t = re.sub(r"^numeric", "decimal", t)
        elif engine == "maxcompute":
            t = re.sub(r"^(varchar|text).*", "string", t)
            t = {"timestamp": "datetime", "real": "double"}.get(t, t)
        out.append((name, t, cid + 1, "NO" if notnull else "YES"))
    return out


# ------------------------------------------------------------------------------ Athena
class FakeS3:
    def __init__(self):
        self.objects: Dict[str, bytes] = {}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[f"{Bucket}/{Key}"])}


class FakeAthena:
    """Executes on SQLite and writes Athena-style CSV (all values quoted, NULL = empty field)."""

    def __init__(self, db_path: str, s3: FakeS3, database: str = "lake"):
        self.conn = connect(db_path, "athena")
        self.s3 = s3
        self.database = database          # the single Glue database this fake exposes
        self.executions: Dict[str, dict] = {}
        self.queries: List[str] = []
        self.workgroups: List[str] = []

    def start_query_execution(self, QueryString, WorkGroup=None, QueryExecutionContext=None, ResultConfiguration=None):
        qid = uuid.uuid4().hex
        self.queries.append(QueryString)
        self.workgroups.append(WorkGroup)
        loc = f"s3://results/{qid}.csv"
        try:
            # schema-qualified names refer to the one database SQLite holds
            sql = QueryString.replace(f'"{self.database}".', "")
            m = re.search(r"FROM information_schema\.columns WHERE table_name = '([^']+)'", sql)
            if m:
                rows = _catalog_columns(self.conn, m.group(1), "athena")
                cols = ["column_name", "data_type", "ordinal_position", "is_nullable"]
            elif "FROM information_schema.tables" in sql:
                rows = self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
                cols = ["table_name"]
            elif "FROM information_schema.schemata" in sql:
                rows, cols = [(self.database,)], ["schema_name"]
            else:
                cur = self.conn.execute(rewrite(sql, "athena"))
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            lines = [",".join(f'"{c}"' for c in cols)]
            for r in rows:
                lines.append(",".join("" if v is None else '"' + _athena_text(v).replace('"', '""') + '"' for v in r))
            self.s3.objects[f"results/{qid}.csv"] = ("\n".join(lines) + "\n").encode()
            state = {"State": "SUCCEEDED"}
        except Exception as exc:  # noqa: BLE001
            state = {"State": "FAILED", "StateChangeReason": str(exc)}
        self.executions[qid] = {"Status": state, "ResultConfiguration": {"OutputLocation": loc}}
        return {"QueryExecutionId": qid}

    def get_query_execution(self, QueryExecutionId):
        return {"QueryExecution": self.executions[QueryExecutionId]}

    def stop_query_execution(self, QueryExecutionId):
        pass


def _athena_text(v: Any) -> str:
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return repr(v)
    return str(v)


# ------------------------------------------------------------------------------ MaxCompute
class _Col:
    def __init__(self, name, type_):
        self.name, self.type, self.nullable = name, type_, True


class _Schema:
    def __init__(self, cols):
        self.columns = cols
        self.simple_columns = cols


class _Record:
    def __init__(self, values):
        self.values = list(values)


class _Reader:
    def __init__(self, cols, rows):
        self.schema = _Schema([_Col(c, "string") for c in cols])
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return (_Record(r) for r in self._rows)


class _Instance:
    def __init__(self, cols, rows):
        self._cols, self._rows = cols, rows

    def open_reader(self, tunnel=True):
        return _Reader(self._cols, self._rows)


class _Table:
    def __init__(self, name, cols):
        self.name = name
        self.table_schema = _Schema(cols)


class FakeODPS:
    def __init__(self, db_path: str):
        self.conn = connect(db_path, "maxcompute")
        self.queries: List[str] = []
        self.hints_seen: List[dict] = []

    def execute_sql(self, sql, hints=None):
        self.queries.append(sql)
        self.hints_seen.append(hints or {})
        cur = self.conn.execute(rewrite(sql, "maxcompute"))
        cols = [d[0] for d in cur.description]
        rows = []
        for r in cur.fetchall():
            # PyODPS returns datetime objects for DATETIME columns
            rows.append(tuple(_maybe_dt(v) for v in r))
        return _Instance(cols, rows)

    def get_table(self, name, project=None):
        cols = [_Col(n, t) for n, t, _o, _nn in _catalog_columns(self.conn, name, "maxcompute")]
        if not cols:
            raise LookupError(name)
        return _Table(name, cols)

    def list_tables(self, project=None):
        return [_Table(r[0], []) for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def _maybe_dt(v: Any) -> Any:
    if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", v):
        return pd.Timestamp(v).to_pydatetime()
    return v


# ------------------------------------------------------------------------------ MySQL (DB-API)
def mysql_like_executor(db_path: str):
    from datavalidator.executors import DbApiExecutor

    class _Cur:
        def __init__(self, conn):
            self._c = conn.cursor()
            self.description = None

        def execute(self, sql):
            m = re.search(r"FROM information_schema\.columns WHERE table_schema = DATABASE\(\) AND table_name = '([^']+)'", sql)
            if m:
                self._rows = _catalog_columns(self._c.connection, m.group(1), "mysql")
                self.description = [("column_name",), ("data_type",), ("ordinal_position",), ("is_nullable",)]
                return
            if "key_column_usage" in sql:
                t = re.search(r"table_name = '([^']+)'", sql).group(1)
                self._rows = [(r[1],) for r in self._c.connection.execute(f"PRAGMA table_info('{t}')") if r[5]]
                self.description = [("column_name",)]
                return
            if sql.startswith("SET "):
                self._rows, self.description = [], None
                return
            self._c.execute(rewrite(sql, "mysql"))
            self.description = self._c.description
            self._rows = None

        def fetchall(self):
            return self._rows if self._rows is not None else self._c.fetchall()

        def close(self):
            pass

    class _Conn:
        def __init__(self):
            self._conn = connect(db_path, "mysql")

        def cursor(self):
            return _Cur(self._conn)

        def commit(self):
            pass

        def close(self):
            self._conn.close()

    return DbApiExecutor(lambda: _Conn(), ["SET time_zone = '+00:00'"], 2, "mysql-emu")
