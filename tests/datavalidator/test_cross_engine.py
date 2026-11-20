"""
AWS Athena / MySQL  ->  Alibaba MaxCompute / MySQL, using emulated engines.

Runs the real Athena and MaxCompute executors and the real dialect SQL (MD5, from_base, CONV,
to_unixtime, DATEDIFF ...) through SQLite-backed fakes.
"""

import copy
from datetime import timedelta

import pytest
from conftest import N_ROWS, check, write_db
from emulation import FakeAthena, FakeODPS, FakeS3, mysql_like_executor

from datavalidator.config import ConnectionConfig, ProjectConfig
from datavalidator.connectors import Connector, MaxComputeConnector
from datavalidator.dialects import get_dialect
from datavalidator.executors import AthenaExecutor, MaxComputeExecutor
from datavalidator.models import Status
from datavalidator.runner import ProjectRunner


def _athena(tmp_path, tz="UTC"):
    s3 = FakeS3()
    fake = FakeAthena(str(tmp_path / "src.db"), s3)
    cfg = ConnectionConfig(type="aws-athena", database="lake", s3_staging_dir="s3://results/", timezone=tz)
    ex = AthenaExecutor(database="lake", athena_client=fake, s3_client=s3)
    return Connector("aws_athena", cfg, get_dialect("athena"), ex), fake


def _maxcompute(tmp_path, tz="Asia/Shanghai"):
    fake = FakeODPS(str(tmp_path / "tgt.db"))
    cfg = ConnectionConfig(type="alibaba-maxcompute", project="p", endpoint="e", timezone=tz)
    ex = MaxComputeExecutor(project="p", endpoint="e", odps_client=fake)
    return MaxComputeConnector("ali_mc", cfg, get_dialect("maxcompute"), ex), fake


def _mysql(tmp_path, db, name, tz="UTC"):
    cfg = ConnectionConfig(type="alibaba-polardb-mysql", host="x", timezone=tz)
    return Connector(name, cfg, get_dialect("mysql"), mysql_like_executor(str(tmp_path / db)))


def _project(src_name, tgt_name, **table_kw):
    t = {"name": "customers", "source": {"connection": src_name, "table": "customers"},
         "target": {"connection": tgt_name, "table": "customers"}, "keys": ["customer_id"]}
    t.update(table_kw)
    return ProjectConfig.model_validate({
        "project": "x", "tables": [t],
        "connections": {src_name: {"type": "sqlite"}, tgt_name: {"type": "sqlite"}}})


def _run(proj, connectors):
    r = ProjectRunner(proj, connectors=connectors)
    return r.run().tables[0]


@pytest.mark.parametrize("mode", ["full", "profile"])
def test_athena_to_maxcompute_identical_with_utc8_target(tmp_path, rows, mode):
    write_db(tmp_path / "src.db", rows)
    write_db(tmp_path / "tgt.db", rows, ts_shift=timedelta(hours=8))   # MaxCompute in UTC+8
    src, fake_athena = _athena(tmp_path)
    tgt, fake_odps = _maxcompute(tmp_path)
    t = _run(_project("aws_athena", "ali_mc", compare={"mode": mode}, tolerances={"timestamp_precision": "ms"}),
             {"aws_athena": src, "ali_mc": tgt})
    assert t.status == Status.PASS, [(c.name, c.status, c.message) for c in t.checks]
    schema = check(t, "schema").details["columns"]
    assert {c["source_type"] for c in schema} >= {"timestamp(3)", "decimal(14,2)"}
    assert {c["target_type"] for c in schema} >= {"datetime", "string"}
    assert any("from_base(" in q for q in fake_athena.queries) or mode == "full"
    assert all(h.get("odps.sql.type.system.odps2") == "true" for h in fake_odps.hints_seen)


@pytest.mark.parametrize("mode", ["full", "profile"])
def test_athena_to_maxcompute_detects_differences(tmp_path, rows, mode):
    tgt_rows = copy.deepcopy(rows)
    tgt_rows[5]["city"] = "Shenzhen"
    tgt_rows[6]["email"] = None
    tgt_rows[7]["balance"] = 0
    del tgt_rows[8]
    write_db(tmp_path / "src.db", rows)
    write_db(tmp_path / "tgt.db", tgt_rows, ts_shift=timedelta(hours=8))
    src, _ = _athena(tmp_path)
    tgt, _ = _maxcompute(tmp_path)
    t = _run(_project("aws_athena", "ali_mc", compare={"mode": mode, "leaf_bucket_rows": 100},
                      tolerances={"timestamp_precision": "ms"}),
             {"aws_athena": src, "ali_mc": tgt})
    d = check(t, "data").details
    assert d["missing_in_target"] == 1 and d["changed_rows"] == 3, d
    assert set(d["column_mismatches"]) == {"city", "email", "balance"}
    assert check(t, "row_count").details["difference"] == -1
    # Athena NULLs must survive the CSV round trip (NULL vs empty string)
    nm = [x for x in t.discrepancies if x.kind == "NULL_MISMATCH"][0]
    assert nm.key == "7" and nm.target_value is None


def test_athena_null_vs_empty_string_round_trip(tmp_path, rows):
    src_rows = copy.deepcopy(rows)
    tgt_rows = copy.deepcopy(rows)
    src_rows[0]["email"] = ""        # empty string in source
    tgt_rows[0]["email"] = None      # NULL in target -> must be a NULL_MISMATCH
    write_db(tmp_path / "src.db", src_rows)
    write_db(tmp_path / "tgt.db", tgt_rows)
    src, _ = _athena(tmp_path)
    tgt = _mysql(tmp_path, "tgt.db", "ali_mysql")
    t = _run(_project("aws_athena", "ali_mysql", compare={"mode": "full"}), {"aws_athena": src, "ali_mysql": tgt})
    d = check(t, "data").details
    assert d["column_mismatches"] == {"email": 1}
    assert [x.kind for x in t.discrepancies] == ["NULL_MISMATCH"]


@pytest.mark.parametrize("mode", ["full", "profile", "sample"])
def test_mysql_to_mysql(tmp_path, rows, mode):
    tgt_rows = copy.deepcopy(rows)
    tgt_rows[100]["customer_name"] = "Changed"
    write_db(tmp_path / "src.db", rows)
    write_db(tmp_path / "tgt.db", tgt_rows)
    t = _run(_project("aws_rds", "ali_polardb", compare={"mode": mode, "sample_pct": 100}),
             {"aws_rds": _mysql(tmp_path, "src.db", "aws_rds"), "ali_polardb": _mysql(tmp_path, "tgt.db", "ali_polardb")})
    d = check(t, "data").details
    assert d["changed_rows"] == 1 and d["column_mismatches"] == {"customer_name": 1}
    assert check(t, "nulls").status == Status.PASS
    assert check(t, "row_count").source_value == N_ROWS
