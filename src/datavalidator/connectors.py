"""
Connectors = dialect + executor + catalog discovery, built from a :class:`ConnectionConfig`.

Drivers are imported lazily so a project only needs the packages for the engines it uses:

    pip install 'datavalidator[mysql]'      # pymysql
    pip install 'datavalidator[postgres]'   # psycopg 3
    pip install 'datavalidator[aws]'        # boto3 (Athena, Secrets Manager)
    pip install 'datavalidator[alibaba]'    # pyodps (MaxCompute), KMS secrets
    pip install 'datavalidator[files]'      # duckdb (S3 / OSS / local Parquet & CSV)
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from datavalidator.config import ConnectionConfig, DatasetRef
from datavalidator.datatypes import Logical, TypeInfo, parse_type
from datavalidator.dialects import Dialect, get_dialect
from datavalidator.executors import (
    AthenaExecutor,
    BaseExecutor,
    DbApiExecutor,
    MaxComputeExecutor,
)
from datavalidator.log import get_logger

logger = get_logger("connectors")


@dataclass
class ColumnInfo:
    name: str
    type: TypeInfo
    nullable: bool = True
    ordinal: int = 0

    @property
    def logical(self) -> Logical:
        return self.type.logical


@dataclass
class TableInfo:
    columns: List[ColumnInfo]
    by_lower: Dict[str, ColumnInfo] = field(init=False)

    def __post_init__(self) -> None:
        self.by_lower = {c.name.lower(): c for c in self.columns}

    def get(self, name: str) -> Optional[ColumnInfo]:
        for c in self.columns:
            if c.name == name:
                return c
        return self.by_lower.get(name.lower())


class Connector:
    def __init__(self, name: str, config: ConnectionConfig, dialect: Dialect, executor: BaseExecutor):
        self.name = name
        self.config = config
        self.dialect = dialect
        self.executor = executor
        self.tz = ZoneInfo(config.timezone)

    # ------------------------------------------------------------------ querying
    def query(self, sql: str, null_sentinel: bool = False) -> pd.DataFrame:
        t0 = time.perf_counter()
        df = self.executor.query(sql, null_sentinel=null_sentinel)
        logger.debug("[%s] %d rows in %.2fs: %s", self.name, len(df), time.perf_counter() - t0, sql[:300])
        return df

    def scalar(self, sql: str) -> Any:
        df = self.query(sql)
        return None if df.empty else df.iat[0, 0]

    def close(self) -> None:
        self.executor.close()

    # ------------------------------------------------------------------ datasets
    def from_clause(self, ds: DatasetRef) -> str:
        if ds.query:
            return f"({ds.query.strip().rstrip(';')}) dv_q"
        schema = ds.schema or self.config.default_schema
        return self.dialect.table(schema, ds.table)

    def describe(self, ds: DatasetRef) -> str:
        if ds.query:
            return f"{self.name}:<query>"
        schema = ds.schema or self.config.default_schema
        return f"{self.name}:{schema + '.' if schema else ''}{ds.table}"

    # ------------------------------------------------------------------ catalog
    def test(self) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            self.query(self.dialect.select_one())
            return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "message": f"{self.config.type} reachable"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "message": str(exc)}

    def list_schemas(self) -> List[str]:
        df = self.query(self.dialect.list_schemas_sql())
        return [str(v) for v in df.iloc[:, 0].tolist()] if not df.empty else []

    def list_tables(self, schema: Optional[str] = None) -> List[str]:
        df = self.query(self.dialect.list_tables_sql(schema or self.config.default_schema))
        return [str(v) for v in df.iloc[:, 0].tolist()] if not df.empty else []

    def get_table_info(self, ds: DatasetRef) -> TableInfo:
        if ds.query:
            return self._infer_query_columns(ds)
        schema = ds.schema or self.config.default_schema
        df = self.query(self.dialect.columns_sql(schema, ds.table))
        if df.empty:
            raise LookupError(f"table not found or has no columns: {self.describe(ds)}")
        df.columns = [str(c).lower() for c in df.columns]
        cols = []
        for i, r in enumerate(df.itertuples(index=False), start=1):
            rec = r._asdict()
            cols.append(ColumnInfo(
                name=str(rec["column_name"]),
                type=parse_type(str(rec["data_type"]), self.dialect.name),
                nullable=str(rec.get("is_nullable", "YES")).upper() != "NO",
                ordinal=int(rec.get("ordinal_position") or i),
            ))
        return TableInfo(cols)

    def primary_keys(self, ds: DatasetRef) -> List[str]:
        if ds.query:
            return []
        sql = self.dialect.primary_key_sql(ds.schema or self.config.default_schema, ds.table)
        if not sql:
            return []
        try:
            df = self.query(sql)
        except Exception:  # noqa: BLE001
            return []
        return [str(v) for v in df.iloc[:, 0].tolist()] if not df.empty else []

    def _infer_query_columns(self, ds: DatasetRef) -> TableInfo:
        sample = self.query(self.dialect.limit(f"SELECT * FROM {self.from_clause(ds)}", 200))
        cols = []
        for i, c in enumerate(sample.columns, start=1):
            cols.append(ColumnInfo(name=str(c), type=_infer_type(sample[c]), ordinal=i))
        return TableInfo(cols)


def _infer_type(series: pd.Series) -> TypeInfo:
    import datetime as _dt
    from decimal import Decimal

    vals = [v for v in series.tolist() if v is not None and not (isinstance(v, float) and pd.isna(v))]
    if not vals:
        return TypeInfo("unknown", Logical.STRING)
    v = vals[0]
    if isinstance(v, bool):
        return TypeInfo("boolean", Logical.BOOLEAN)
    if isinstance(v, int):
        return TypeInfo("bigint", Logical.INTEGER)
    if isinstance(v, Decimal):
        scale = max((-x.as_tuple().exponent if isinstance(x, Decimal) else 0) for x in vals)
        return TypeInfo("decimal", Logical.DECIMAL, scale=max(scale, 0))
    if isinstance(v, float):
        return TypeInfo("double", Logical.FLOAT)
    if isinstance(v, _dt.datetime):
        return TypeInfo("timestamp", Logical.TIMESTAMP_TZ if v.tzinfo else Logical.TIMESTAMP)
    if isinstance(v, _dt.date):
        return TypeInfo("date", Logical.DATE)
    return TypeInfo("string", Logical.STRING)


# =========================================================================== factories
def _register_sqlite_udfs(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.create_function(
        "md5", 1, lambda s: None if s is None else hashlib.md5(str(s).encode("utf-8")).hexdigest(),
        deterministic=True)
    conn.create_function("hex_to_int", 1, lambda s: None if s is None else int(s, 16), deterministic=True)
    return conn


def _sqlite_connect(path: str, uri: bool = False):
    def connect():
        return _register_sqlite_udfs(sqlite3.connect(path, uri=uri, check_same_thread=False))
    return connect


def _mysql_connect(cfg: ConnectionConfig):
    try:
        import pymysql  # type: ignore
    except ImportError as exc:
        raise ImportError("pymysql is required for MySQL-family connections; pip install 'datavalidator[mysql]'") from exc

    def connect():
        kwargs: Dict[str, Any] = dict(
            host=cfg.host, port=cfg.port or 3306, user=cfg.user, password=cfg.password or "",
            database=cfg.database, charset="utf8mb4", connect_timeout=cfg.connect_timeout,
            read_timeout=cfg.query_timeout_seconds, autocommit=True,
        )
        if cfg.ssl:
            kwargs["ssl"] = cfg.ssl if isinstance(cfg.ssl, dict) else {"ssl": {}}
        return pymysql.connect(**kwargs)
    return connect


def _postgres_connect(cfg: ConnectionConfig):
    sslmode = None
    if isinstance(cfg.ssl, dict):
        sslmode = cfg.ssl.get("sslmode")
    elif cfg.ssl is True:
        sslmode = "require"
    try:
        import psycopg  # type: ignore

        def connect():
            kw = dict(host=cfg.host, port=cfg.port or 5432, dbname=cfg.database, user=cfg.user,
                      password=cfg.password, connect_timeout=cfg.connect_timeout, autocommit=True)
            if sslmode:
                kw["sslmode"] = sslmode
            if isinstance(cfg.ssl, dict) and cfg.ssl.get("sslrootcert"):
                kw["sslrootcert"] = cfg.ssl["sslrootcert"]
            return psycopg.connect(**kw)
        return connect
    except ImportError:
        pass
    try:
        import psycopg2  # type: ignore
    except ImportError as exc:
        raise ImportError("psycopg (v3) or psycopg2 is required for PostgreSQL-family connections; "
                          "pip install 'datavalidator[postgres]'") from exc

    def connect2():
        kw = dict(host=cfg.host, port=cfg.port or 5432, dbname=cfg.database, user=cfg.user,
                  password=cfg.password, connect_timeout=cfg.connect_timeout)
        if sslmode:
            kw["sslmode"] = sslmode
        conn = psycopg2.connect(**kw)
        conn.autocommit = True
        return conn
    return connect2


def _files_connector(name: str, cfg: ConnectionConfig) -> Connector:
    """Expose Parquet/CSV/JSON files (local, S3 or OSS) as SQL tables through DuckDB."""
    try:
        import duckdb  # type: ignore
    except ImportError:
        duckdb = None

    if duckdb is None:
        return _files_connector_sqlite_fallback(name, cfg)

    is_oss = cfg.type == "alibaba-oss"
    init: List[str] = []
    needs_remote = any(
        str(p).startswith(("s3://", "oss://"))
        for t in cfg.tables.values() for p in ([t.path] if isinstance(t.path, str) else t.path)
    )
    if needs_remote:
        init += ["INSTALL httpfs", "LOAD httpfs"]
        if is_oss:
            endpoint = cfg.endpoint or f"oss-{cfg.region}.aliyuncs.com"
            init += [
                f"SET s3_endpoint='{endpoint}'", "SET s3_url_style='vhost'",
                f"SET s3_region='{cfg.region or 'oss'}'",
                f"SET s3_access_key_id='{cfg.access_key_id or ''}'",
                f"SET s3_secret_access_key='{cfg.access_key_secret or ''}'",
            ]
        elif cfg.access_key_id:
            init += [
                f"SET s3_region='{cfg.region or 'us-east-1'}'",
                f"SET s3_access_key_id='{cfg.access_key_id}'",
                f"SET s3_secret_access_key='{cfg.access_key_secret or ''}'",
            ]
            if cfg.session_token:
                init.append(f"SET s3_session_token='{cfg.session_token}'")
        else:
            init += ["INSTALL aws", "LOAD aws",
                     "CREATE OR REPLACE SECRET dv_s3 (TYPE S3, PROVIDER CREDENTIAL_CHAIN"
                     + (f", REGION '{cfg.region}'" if cfg.region else "") + ")"]

    for tname, t in cfg.tables.items():
        paths = [t.path] if isinstance(t.path, str) else list(t.path)
        paths = ["s3://" + p[6:] if p.startswith("oss://") else p for p in paths]
        plist = "[" + ", ".join("'" + p.replace("'", "''") + "'" for p in paths) + "]"
        reader = {"parquet": "read_parquet", "csv": "read_csv_auto", "json": "read_json_auto"}[t.format]
        opts = "".join(f", {k}={v!r}" if isinstance(v, str) else f", {k}={v}" for k, v in t.options.items())
        init.append(f'CREATE OR REPLACE VIEW "{tname}" AS SELECT * FROM {reader}({plist}{opts})')

    shared = duckdb.connect(database=":memory:")
    for stmt in init:
        shared.execute(stmt)
    lock = __import__("threading").Lock()

    class _DuckExec(BaseExecutor):
        def query(self, sql: str, null_sentinel: bool = False) -> pd.DataFrame:
            with lock:
                cur = shared.cursor()
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            from datavalidator.executors import _frame
            return _frame(rows, cols)

        def close(self) -> None:
            shared.close()

    return Connector(name, cfg, get_dialect("duckdb"), _DuckExec())


def _files_connector_sqlite_fallback(name: str, cfg: ConnectionConfig) -> Connector:
    """Local CSV/Parquet without DuckDB: load into an in-memory SQLite database."""
    path = f"file:dv_{name}_{id(cfg)}?mode=memory&cache=shared"
    keeper = sqlite3.connect(path, uri=True, check_same_thread=False)
    for tname, t in cfg.tables.items():
        paths = [t.path] if isinstance(t.path, str) else list(t.path)
        for p in paths:
            if p.startswith(("s3://", "oss://")):
                raise ImportError("duckdb is required to read files from S3/OSS; pip install 'datavalidator[files]'")
        frames = []
        for p in paths:
            if t.format == "csv":
                frames.append(pd.read_csv(p, **t.options))
            elif t.format == "parquet":
                frames.append(pd.read_parquet(p))
            else:
                frames.append(pd.read_json(p, lines=True))
        pd.concat(frames, ignore_index=True).to_sql(tname, keeper, index=False, if_exists="replace")

    ex = DbApiExecutor(_sqlite_connect(path, uri=True), name=name)
    ex._keeper = keeper  # keep the shared in-memory DB alive
    return Connector(name, cfg, get_dialect("sqlite"), ex)


class MaxComputeConnector(Connector):
    """MaxCompute catalog uses the PyODPS API (no information_schema for all projects)."""

    def _odps(self):
        return self.executor.odps  # type: ignore[attr-defined]

    def list_schemas(self) -> List[str]:
        return [self.config.project or ""]

    def list_tables(self, schema: Optional[str] = None) -> List[str]:
        kwargs = {}
        if schema and schema != self.config.project:
            kwargs["project"] = schema
        return [t.name for t in self._odps().list_tables(**kwargs)]

    def get_table_info(self, ds: DatasetRef) -> TableInfo:
        if ds.query:
            return self._infer_query_columns(ds)
        kwargs = {}
        schema = ds.schema or self.config.default_schema
        if schema:
            kwargs["project"] = schema.split(".")[0]
        t = self._odps().get_table(ds.table, **kwargs)
        sch = getattr(t, "table_schema", None) or t.schema
        cols = []
        for i, c in enumerate(list(sch.simple_columns if hasattr(sch, "simple_columns") else sch.columns), start=1):
            cols.append(ColumnInfo(name=c.name, type=parse_type(str(c.type), "maxcompute"),
                                   nullable=getattr(c, "nullable", True), ordinal=i))
        return TableInfo(cols)


def create_connector(name: str, cfg: ConnectionConfig) -> Connector:
    driver = cfg.driver
    if driver == "sqlite":
        path = cfg.database or ":memory:"
        ex = DbApiExecutor(_sqlite_connect(path), cfg.session_init, cfg.max_connections, name)
        return Connector(name, cfg, get_dialect("sqlite"), ex)
    if driver == "mysql":
        init = ["SET time_zone = '+00:00'", *cfg.session_init]
        ex = DbApiExecutor(_mysql_connect(cfg), init, cfg.max_connections, name)
        return Connector(name, cfg, get_dialect(cfg.dialect), ex)
    if driver == "postgres":
        init = list(cfg.session_init)
        if cfg.dialect != "redshift":
            init = ["SET TIME ZONE 'UTC'", f"SET statement_timeout = {int(cfg.query_timeout_seconds) * 1000}", *init]
        else:
            init = ["SET TIMEZONE TO 'UTC'", f"SET statement_timeout TO {int(cfg.query_timeout_seconds) * 1000}", *init]
        ex = DbApiExecutor(_postgres_connect(cfg), init, cfg.max_connections, name)
        return Connector(name, cfg, get_dialect(cfg.dialect), ex)
    if driver == "athena":
        ex = AthenaExecutor(
            database=cfg.database or cfg.default_schema, workgroup=cfg.workgroup,
            s3_staging_dir=cfg.s3_staging_dir, catalog=cfg.catalog, region=cfg.region,
            profile=cfg.profile, access_key_id=cfg.access_key_id,
            access_key_secret=cfg.access_key_secret, session_token=cfg.session_token,
            query_timeout_seconds=cfg.query_timeout_seconds,
            athena_client=getattr(cfg, "athena_client", None), s3_client=getattr(cfg, "s3_client", None),
        )
        return Connector(name, cfg, get_dialect("athena"), ex)
    if driver == "maxcompute":
        if not cfg.project or not cfg.endpoint:
            if getattr(cfg, "odps_client", None) is None:
                raise ValueError(f"connection '{name}': maxcompute needs 'project' and 'endpoint'")
        ex = MaxComputeExecutor(
            project=cfg.project or "", endpoint=cfg.endpoint or "", access_key_id=cfg.access_key_id,
            access_key_secret=cfg.access_key_secret, tunnel_endpoint=cfg.tunnel_endpoint,
            hints=cfg.hints, timezone=cfg.timezone, odps_client=getattr(cfg, "odps_client", None),
            schema=getattr(cfg, "odps_schema", None),
        )
        return MaxComputeConnector(name, cfg, get_dialect("maxcompute"), ex)
    if driver == "files":
        return _files_connector(name, cfg)
    if driver == "duckdb":
        import duckdb  # type: ignore
        conn = duckdb.connect(cfg.database or ":memory:", read_only=bool(cfg.database))
        ex = DbApiExecutor(lambda: conn.cursor(), cfg.session_init, cfg.max_connections, name)
        return Connector(name, cfg, get_dialect("duckdb"), ex)
    raise ValueError(f"unsupported driver '{driver}'")
