"""
Query executors: run SQL on an engine and return a pandas DataFrame.

* :class:`DbApiExecutor`   — any PEP 249 driver (pymysql, psycopg 3 / psycopg2, sqlite3, duckdb)
                             with a small thread-safe connection pool.
* :class:`AthenaExecutor`  — boto3 ``StartQueryExecution`` + CSV result download from S3
                             (fast for large results, unlike paging ``GetQueryResults``).
* :class:`MaxComputeExecutor` — PyODPS ``execute_sql`` + Tunnel reader.

All values are returned with ``dtype=object`` so 64-bit integers and decimals keep full
precision (pandas would otherwise coerce NULL-containing integer columns to float64).
"""

from __future__ import annotations

import io
import queue
import random
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, List, Optional

import pandas as pd

from datavalidator.dialects import NULL_SENTINEL
from datavalidator.log import get_logger

logger = get_logger("executors")


class QueryError(RuntimeError):
    def __init__(self, message: str, sql: str = ""):
        super().__init__(message)
        self.sql = sql


def _frame(rows: List[tuple], columns: List[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({c: pd.Series([], dtype=object) for c in columns})
    return pd.DataFrame.from_records(rows, columns=columns, coerce_float=False).astype(object)


class BaseExecutor:
    def query(self, sql: str, null_sentinel: bool = False) -> pd.DataFrame:
        raise NotImplementedError

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- DB-API
class DbApiExecutor(BaseExecutor):
    def __init__(
        self,
        connect: Callable[[], Any],
        session_init: Optional[List[str]] = None,
        max_connections: int = 4,
        name: str = "db",
        retries: int = 2,
    ):
        self._connect = connect
        self._session_init = session_init or []
        self._max = max(1, max_connections)
        self._pool: "queue.LifoQueue[Any]" = queue.LifoQueue()
        self._created = 0
        self._lock = threading.Lock()
        self.name = name
        self.retries = retries

    def _new_conn(self) -> Any:
        conn = self._connect()
        if self._session_init:
            cur = conn.cursor()
            for stmt in self._session_init:
                cur.execute(stmt)
            try:
                cur.close()
            except Exception:  # noqa: BLE001
                pass
        return conn

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = None
        try:
            conn = self._pool.get_nowait()
        except queue.Empty:
            with self._lock:
                can_create = self._created < self._max
                if can_create:
                    self._created += 1
            if can_create:
                try:
                    conn = self._new_conn()
                except Exception:
                    with self._lock:
                        self._created -= 1
                    raise
            else:
                conn = self._pool.get()  # wait for a free connection
        broken = False
        try:
            yield conn
        except Exception:
            broken = True
            raise
        finally:
            if broken:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                with self._lock:
                    self._created -= 1
            else:
                self._pool.put(conn)

    def query(self, sql: str, null_sentinel: bool = False) -> pd.DataFrame:
        attempt = 0
        while True:
            try:
                with self._conn() as conn:
                    cur = conn.cursor()
                    try:
                        cur.execute(sql)
                        cols = [d[0] for d in (cur.description or [])]
                        rows = cur.fetchall() if cur.description else []
                    finally:
                        try:
                            cur.close()
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        conn.commit()  # end read transaction (avoid idle-in-transaction)
                    except Exception:  # noqa: BLE001
                        pass
                df = _frame([tuple(r) for r in rows], cols)
                if null_sentinel:
                    df = df.replace({NULL_SENTINEL: None})
                return df
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                if attempt > self.retries or not _is_transient(exc):
                    raise QueryError(f"[{self.name}] query failed: {exc}", sql) from exc
                wait = min(30.0, (2 ** attempt) + random.random())
                logger.warning("[%s] transient error (%s); retry %d in %.1fs", self.name, exc, attempt, wait)
                time.sleep(wait)

    def close(self) -> None:
        while True:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                break
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._created = 0


_TRANSIENT_MARKERS = (
    "server has gone away", "lost connection", "connection reset", "timed out", "timeout",
    "too many connections", "could not connect", "connection refused", "throttl",
    "toomanyrequests", "rate exceeded", "temporarily unavailable", "ssl syscall",
    "terminating connection", "broken pipe",
)


def _is_transient(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


# --------------------------------------------------------------------------- Athena
class AthenaExecutor(BaseExecutor):
    """Runs queries on AWS Athena and streams the CSV result file from S3."""

    TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}

    def __init__(
        self,
        database: Optional[str],
        workgroup: Optional[str] = None,
        s3_staging_dir: Optional[str] = None,
        catalog: Optional[str] = None,
        region: Optional[str] = None,
        profile: Optional[str] = None,
        access_key_id: Optional[str] = None,
        access_key_secret: Optional[str] = None,
        session_token: Optional[str] = None,
        query_timeout_seconds: int = 1800,
        athena_client: Any = None,
        s3_client: Any = None,
    ):
        self.database = database
        self.workgroup = workgroup or "primary"
        self.s3_staging_dir = s3_staging_dir
        self.catalog = catalog
        self.timeout = query_timeout_seconds
        if athena_client is None or s3_client is None:
            try:
                import boto3  # type: ignore
            except ImportError as exc:
                raise ImportError("boto3 is required for Athena; pip install 'datavalidator[aws]'") from exc
            kwargs = {"region_name": region}
            if profile:
                kwargs["profile_name"] = profile
            if access_key_id:
                kwargs.update(aws_access_key_id=access_key_id, aws_secret_access_key=access_key_secret,
                              aws_session_token=session_token)
            session = boto3.Session(**kwargs)
            athena_client = athena_client or session.client("athena")
            s3_client = s3_client or session.client("s3")
        self.athena = athena_client
        self.s3 = s3_client

    def _start(self, sql: str) -> str:
        params: dict = {"QueryString": sql, "WorkGroup": self.workgroup}
        ctx = {}
        if self.database:
            ctx["Database"] = self.database
        if self.catalog:
            ctx["Catalog"] = self.catalog
        if ctx:
            params["QueryExecutionContext"] = ctx
        if self.s3_staging_dir:
            params["ResultConfiguration"] = {"OutputLocation": self.s3_staging_dir}
        for attempt in range(6):
            try:
                return self.athena.start_query_execution(**params)["QueryExecutionId"]
            except Exception as exc:  # noqa: BLE001
                if attempt < 5 and _is_transient(exc):
                    time.sleep(min(20, 2 ** attempt + random.random()))
                    continue
                raise
        raise QueryError("unreachable", sql)

    def _wait(self, qid: str, sql: str) -> dict:
        deadline = time.monotonic() + self.timeout
        delay = 0.25
        while True:
            info = self.athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
            state = info["Status"]["State"]
            if state in self.TERMINAL:
                if state != "SUCCEEDED":
                    reason = info["Status"].get("StateChangeReason", "unknown reason")
                    raise QueryError(f"[athena] query {qid} {state}: {reason}", sql)
                return info
            if time.monotonic() > deadline:
                try:
                    self.athena.stop_query_execution(QueryExecutionId=qid)
                finally:
                    raise QueryError(f"[athena] query {qid} exceeded {self.timeout}s and was cancelled", sql)
            time.sleep(delay)
            delay = min(delay * 1.5, 5.0)

    def query(self, sql: str, null_sentinel: bool = False) -> pd.DataFrame:
        try:
            qid = self._start(sql)
        except QueryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise QueryError(f"[athena] failed to start query: {exc}", sql) from exc
        info = self._wait(qid, sql)
        location = info.get("ResultConfiguration", {}).get("OutputLocation")
        if not location or not location.startswith("s3://"):
            raise QueryError(f"[athena] query {qid} has no S3 output location", sql)
        bucket, key = location[5:].split("/", 1)
        body = self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if not body.strip():
            return pd.DataFrame()
        # Athena CSV: NULL -> empty unquoted field; we read everything as text and
        # let the normalizer apply logical types.
        df = pd.read_csv(io.BytesIO(body), dtype=str, keep_default_na=False, na_filter=False)
        df = df.astype(object)
        if null_sentinel:
            return df.replace({NULL_SENTINEL: None})
        return df.replace({"": None})


# --------------------------------------------------------------------------- MaxCompute
class MaxComputeExecutor(BaseExecutor):
    """Runs ODPS SQL with PyODPS and reads results through the Tunnel service."""

    def __init__(
        self,
        project: str,
        endpoint: str,
        access_key_id: Optional[str] = None,
        access_key_secret: Optional[str] = None,
        tunnel_endpoint: Optional[str] = None,
        hints: Optional[dict] = None,
        timezone: str = "Asia/Shanghai",
        odps_client: Any = None,
        schema: Optional[str] = None,
    ):
        if odps_client is None:
            try:
                from odps import ODPS, options  # type: ignore
            except ImportError as exc:
                raise ImportError("pyodps is required for MaxCompute; pip install 'datavalidator[alibaba]'") from exc
            options.local_timezone = timezone
            kwargs = {"project": project, "endpoint": endpoint}
            if schema:
                kwargs["schema"] = schema
            if tunnel_endpoint:
                kwargs["tunnel_endpoint"] = tunnel_endpoint
            odps_client = ODPS(access_key_id, access_key_secret, **kwargs)
        self.odps = odps_client
        self.hints = {"odps.sql.type.system.odps2": "true", "odps.sql.decimal.odps2": "true", **(hints or {})}

    def query(self, sql: str, null_sentinel: bool = False) -> pd.DataFrame:
        try:
            inst = self.odps.execute_sql(sql, hints=self.hints)
            with inst.open_reader(tunnel=True) as reader:
                schema = getattr(reader, "schema", None) or getattr(reader, "_schema", None)
                cols = [c.name for c in schema.columns] if schema is not None else None
                rows = [tuple(r.values) for r in reader]
                if cols is None:
                    cols = [f"c{i}" for i in range(len(rows[0]) if rows else 0)]
        except Exception as exc:  # noqa: BLE001
            raise QueryError(f"[maxcompute] query failed: {exc}", sql) from exc
        df = _frame(rows, cols)
        if null_sentinel:
            df = df.replace({NULL_SENTINEL: None})
        return df
