"""Unit tests: type parsing, dialect SQL, normalization, config & secrets, executors."""

import hashlib
import io
import logging
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from datavalidator.config import CONNECTION_TYPES, load_project
from datavalidator.datatypes import Logical, check_compatibility, parse_type
from datavalidator.dialects import DIALECTS, NULL_SENTINEL, get_dialect
from datavalidator.executors import AthenaExecutor, QueryError
from datavalidator.log import get_logger, mask, register_secret
from datavalidator.normalize import NormOptions, SideContext, comparison_logical, normalize_series


# ------------------------------------------------------------------------------ types
@pytest.mark.parametrize("native,engine,logical", [
    ("tinyint(1)", "mysql", Logical.BOOLEAN),
    ("bigint(20) unsigned", "mysql", Logical.INTEGER),
    ("timestamp", "mysql", Logical.TIMESTAMP_TZ),
    ("datetime(6)", "mysql", Logical.TIMESTAMP),
    ("decimal(12,2)", "mysql", Logical.DECIMAL),
    ("character varying(255)", "postgres", Logical.STRING),
    ("timestamp with time zone", "postgres", Logical.TIMESTAMP_TZ),
    ("timestamp(3)", "athena", Logical.TIMESTAMP),
    ("array<string>", "athena", Logical.COMPLEX),
    ("map<string,int>", "maxcompute", Logical.COMPLEX),
    ("DATETIME", "maxcompute", Logical.TIMESTAMP),
    ("STRING", "maxcompute", Logical.STRING),
    ("jsonb", "postgres", Logical.JSON),
    ("bytea", "postgres", Logical.BINARY),
    ("geometry", "postgres", Logical.UNKNOWN),
])
def test_parse_type(native, engine, logical):
    assert parse_type(native, engine).logical == logical


def test_compatibility_rules():
    vc = lambda n: parse_type(f"varchar({n})")  # noqa: E731
    assert check_compatibility(vc(100), vc(255)).level == "ok"
    assert check_compatibility(vc(255), vc(100)).level == "fail"
    assert check_compatibility(parse_type("decimal(12,4)"), parse_type("decimal(12,2)")).level == "fail"
    assert check_compatibility(parse_type("decimal(18,2)"), parse_type("decimal(10,2)")).level == "fail"
    assert check_compatibility(parse_type("int"), parse_type("bigint")).level == "ok"
    assert check_compatibility(parse_type("decimal(10,2)"), parse_type("double")).level == "warn"
    assert check_compatibility(parse_type("timestamp"), parse_type("timestamptz")).level == "warn"
    assert check_compatibility(parse_type("date"), parse_type("boolean")).level == "fail"


# ------------------------------------------------------------------------------ dialects
def test_every_connection_type_has_a_dialect():
    for name, (dialect, _driver) in CONNECTION_TYPES.items():
        assert dialect in DIALECTS, name


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
def test_dialect_building_blocks(dialect):
    d = get_dialect(dialect)
    assert d.quote('we"ird`col').count("we") == 1
    assert d.lit("it's") in ("'it''s'",)
    assert "1, 7" in d.substr("x", 1, 7)
    assert d.hex_to_int("h")
    assert d.epoch_seconds("c", Logical.TIMESTAMP)


def test_identifier_quoting_prevents_injection():
    assert get_dialect("postgres").quote('a"; DROP TABLE x; --') == '"a""; DROP TABLE x; --"'
    assert get_dialect("mysql").quote("a`b") == "`a``b`"
    assert get_dialect("mysql").lit("a\\' OR 1=1") == "'a\\\\'' OR 1=1'"


def test_portable_hex_parsing_matches_python():
    """The STRPOS-based fallback (used by Hologres) must equal int(hex, 16)."""
    d = get_dialect("hologres")
    expr = d.hex_to_int("h").replace("STRPOS", "INSTR")
    conn = sqlite3.connect(":memory:")
    for word in ("abc", "migration", "阿里云"):
        h = hashlib.md5(word.encode()).hexdigest()[:7]
        assert conn.execute(f"SELECT {expr} FROM (SELECT '{h}' AS h)").fetchone()[0] == int(h, 16)


def test_mysql_never_uses_double_pipe_concat():
    sql = get_dialect("mysql").concat(["a", "'|'", "b"])
    assert "||" not in sql and sql.startswith("CONCAT(")


def test_athena_fetch_column_uses_null_sentinel():
    d = get_dialect("athena")
    assert d.uses_null_sentinel
    assert NULL_SENTINEL in d.fetch_column('"c"', Logical.STRING)
    assert "json_format" in d.fetch_column('"c"', Logical.COMPLEX)


# ------------------------------------------------------------------------------ normalization
UTC = SideContext(Logical.TIMESTAMP, ZoneInfo("UTC"))
SHANGHAI = SideContext(Logical.TIMESTAMP, ZoneInfo("Asia/Shanghai"))


def norm(values, cmp, ctx=UTC, **opts):
    return normalize_series(pd.Series(values, dtype=object), cmp, ctx, NormOptions(**opts)).tolist()


def test_decimal_normalization_is_exact():
    a = norm([Decimal("1.50"), 1.5, "1.5000", 2, None, "abc"], Logical.DECIMAL)
    assert a[:4] == [Decimal("1.5")] * 3 + [Decimal(2)]
    assert a[4] is None and a[5].startswith("<unparsable")
    assert norm([2**63 - 1], Logical.INTEGER) == [Decimal(2**63 - 1)]   # no float precision loss


def test_timestamp_normalization_across_time_zones():
    utc_aware = datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc)
    shanghai_naive = datetime(2025, 1, 1, 10, 0)
    athena_text = "2025-01-01 02:00:00.000"
    assert norm([utc_aware], Logical.TIMESTAMP_TZ) == norm([shanghai_naive], Logical.TIMESTAMP_TZ, SHANGHAI) \
        == norm([athena_text], Logical.TIMESTAMP_TZ) == ["2025-01-01 02:00:00.000000"]
    assert norm(["2025-01-01 02:00:00.123456"], Logical.TIMESTAMP, timestamp_precision="ms") == ["2025-01-01 02:00:00.123"]
    assert norm(["not a date"], Logical.TIMESTAMP)[0].startswith("<unparsable")
    # MySQL TIMESTAMP (tz-aware type, naive value from a UTC session) is treated as UTC
    tz_typed = SideContext(Logical.TIMESTAMP_TZ, ZoneInfo("Asia/Shanghai"))
    assert norm([datetime(2025, 1, 1, 2, 0)], Logical.TIMESTAMP_TZ, tz_typed) == ["2025-01-01 02:00:00.000000"]


def test_boolean_json_binary_string_normalization():
    assert norm([True, 0, "t", "YES", "0", 2], Logical.BOOLEAN) == ["true", "false", "true", "true", "false", "true"]
    assert norm(['{"b": 1, "a": [1, 2]}', {"a": [1, 2], "b": 1}], Logical.JSON) == ['{"a":[1,2],"b":1}'] * 2
    assert norm([b"\x01\xff", "01FF", "\\x01ff"], Logical.BINARY) == ["01ff"] * 3
    assert norm(["  a  ", ""], Logical.STRING, trim=True, empty_as_null=True) == ["a", None]
    assert norm(["ABC "], Logical.STRING, rtrim=True, lower=True) == ["abc"]


def test_comparison_logical():
    assert comparison_logical(Logical.INTEGER, Logical.DECIMAL) == Logical.DECIMAL
    assert comparison_logical(Logical.DECIMAL, Logical.FLOAT) == Logical.FLOAT
    assert comparison_logical(Logical.BOOLEAN, Logical.INTEGER) == Logical.BOOLEAN
    assert comparison_logical(Logical.TIMESTAMP, Logical.TIMESTAMP_TZ) == Logical.TIMESTAMP_TZ
    assert comparison_logical(Logical.STRING, Logical.DATE) == Logical.DATE
    assert comparison_logical(Logical.JSON, Logical.STRING) == Logical.JSON


# ------------------------------------------------------------------------------ config & secrets
PROJECT = """
project: p
connections:
  src: {type: aws-aurora-mysql, host: h, user: u, password: "${env:DV_TEST_PW}", database: d}
  tgt: {type: alibaba-polardb-mysql, host: h2, user: u, password: "${file:%s}", database: d}
defaults:
  compare: {mode: profile}
  tolerances: {numeric_abs: 0.01}
tables:
  - name: a
    source: {connection: src, table: crm.a}
    target: {connection: tgt, table: crm.a}
    compare: {fetch_batch_rows: 10}
include: [more.yaml]
"""


def test_project_loading_defaults_includes_and_secrets(tmp_path, monkeypatch):
    secret_file = tmp_path / "pw.txt"
    secret_file.write_text("file-secret-123\n")
    (tmp_path / "p.yaml").write_text(PROJECT % secret_file.as_posix())
    (tmp_path / "more.yaml").write_text("tables:\n  - {name: b, source: {connection: src, table: b}, target: {connection: tgt, table: b}}\n")
    monkeypatch.setenv("DV_TEST_PW", "env-secret-456")
    p = load_project(str(tmp_path / "p.yaml"))
    assert p.connections["src"].password == "env-secret-456"
    assert p.connections["tgt"].password == "file-secret-123"
    assert p.connections["src"].dialect == "mysql"
    a, b = p.tables
    assert a.source.schema == "crm" and a.source.table == "a"
    assert a.compare.mode == "profile" and a.compare.fetch_batch_rows == 10   # deep-merged defaults
    assert b.tolerances.numeric_abs == 0.01
    # resolved secrets are masked in logs
    assert "env-secret-456" not in mask("connecting with env-secret-456")
    assert "file-secret-123" not in mask("pw=file-secret-123")


def test_bad_config_is_rejected(tmp_path):
    (tmp_path / "p.yaml").write_text(
        "project: p\nconnections: {x: {type: oracle-on-mars}}\ntables: []\n")
    with pytest.raises(Exception, match="unknown connection type"):
        load_project(str(tmp_path / "p.yaml"))
    (tmp_path / "q.yaml").write_text(
        "project: p\nconnections: {x: {type: sqlite}}\ntables: [{name: t, source: {connection: nope, table: a}, target: {connection: x, table: a}}]\n")
    with pytest.raises(Exception, match="unknown connection 'nope'"):
        load_project(str(tmp_path / "q.yaml"))


def test_missing_env_var_is_an_error(tmp_path):
    (tmp_path / "p.yaml").write_text("project: p\nconnections: {x: {type: sqlite, password: '${env:DV_DOES_NOT_EXIST}'}}\ntables: []\n")
    with pytest.raises(Exception, match="DV_DOES_NOT_EXIST"):
        load_project(str(tmp_path / "p.yaml"))


def test_log_masking(caplog):
    register_secret("SuperSecret99")
    assert mask("postgresql://user:hunter2@host/db") == "postgresql://user:********@host/db"
    assert "SuperSecret99" not in mask("password=SuperSecret99")
    log = get_logger("t")
    with caplog.at_level(logging.INFO, logger="datavalidator.t"):
        log.info("token %s", "SuperSecret99")
    assert "SuperSecret99" not in caplog.text


# ------------------------------------------------------------------------------ Athena executor
class _Athena:
    def __init__(self, states, reason="boom"):
        self.states, self.reason, self.stopped = list(states), reason, False

    def start_query_execution(self, **kw):
        return {"QueryExecutionId": "q1"}

    def get_query_execution(self, QueryExecutionId):
        st = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"QueryExecution": {"Status": {"State": st, "StateChangeReason": self.reason},
                                   "ResultConfiguration": {"OutputLocation": "s3://b/q1.csv"}}}

    def stop_query_execution(self, QueryExecutionId):
        self.stopped = True


class _S3:
    def __init__(self, body):
        self.body = body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.body)}


def test_athena_executor_reads_csv_and_preserves_nulls():
    body = f'"a","b"\n"1","{NULL_SENTINEL}"\n"","x"\n'.encode()
    ex = AthenaExecutor("db", athena_client=_Athena(["RUNNING", "SUCCEEDED"]), s3_client=_S3(body))
    df = ex.query("select", null_sentinel=True)
    assert df.to_dict("records") == [{"a": "1", "b": None}, {"a": "", "b": "x"}]
    df2 = AthenaExecutor("db", athena_client=_Athena(["SUCCEEDED"]), s3_client=_S3(b'"n"\n""\n"5"\n')).query("s")
    assert df2["n"].tolist() == [None, "5"]


def test_athena_executor_failure_and_timeout():
    with pytest.raises(QueryError, match="FAILED: bad column"):
        AthenaExecutor("db", athena_client=_Athena(["FAILED"], "bad column"), s3_client=_S3(b"")).query("x")
    fake = _Athena(["RUNNING"])
    with pytest.raises(QueryError, match="exceeded"):
        AthenaExecutor("db", athena_client=fake, s3_client=_S3(b""), query_timeout_seconds=0).query("x")
    assert fake.stopped
