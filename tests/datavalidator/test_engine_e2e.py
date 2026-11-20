"""End-to-end validation scenarios (SQLite stands in for the AWS source and Alibaba target)."""

import copy
from datetime import timedelta

import pytest
from conftest import N_ROWS, check, project, run, table, write_db

from datavalidator.models import Status

MODES = ["full", "profile", "sample"]


def _setup(tmp_path, src_rows, tgt_rows, **kw):
    write_db(tmp_path / "src.db", src_rows)
    write_db(tmp_path / "tgt.db", tgt_rows, **kw)


@pytest.mark.parametrize("mode", MODES + ["auto"])
def test_identical_tables_pass_every_mode(tmp_path, rows, mode):
    _setup(tmp_path, rows, rows)
    res = run(project(tmp_path, [table(compare={"mode": mode, "fetch_batch_rows": 700,
                                                "leaf_bucket_rows": 100})]))
    t = res.tables[0]
    assert t.status == Status.PASS, [(c.name, c.status, c.message) for c in t.checks]
    assert t.stats["keys"] == ["customer_id"]           # detected from primary key
    if mode == "profile":
        # identical data -> nothing but checksums cross the wire
        assert t.stats["rows_fetched_source"] == 0 and t.stats["rows_fetched_target"] == 0


@pytest.mark.parametrize("mode", ["full", "profile"])
def test_detects_every_anomaly_type(tmp_path, rows, mode):
    tgt = copy.deepcopy(rows)
    tgt[10]["customer_name"] = "Changed Name"           # value change (string)
    tgt[20]["balance"] = tgt[20]["balance"] + 0.01      # value change (decimal, 1 cent)
    tgt[30]["email"] = None                             # NULL introduced
    tgt[40]["is_active"] = not tgt[40]["is_active"]     # boolean flip
    tgt[50]["created_at"] = tgt[50]["created_at"] + timedelta(seconds=1)
    tgt[60]["city"] = tgt[60]["city"][:-1] + "X"        # same length change -> needs MD5 checksum
    del tgt[70]                                         # missing row (id 71)
    extra = copy.deepcopy(rows[0])
    extra["customer_id"] = 999_999
    tgt.append(extra)                                   # extra row
    _setup(tmp_path, rows, tgt)

    res = run(project(tmp_path, [table(compare={"mode": mode, "leaf_bucket_rows": 50,
                                                "initial_bucket_hex_digits": 1})]))
    t = res.tables[0]
    data = check(t, "data")
    assert t.status == Status.FAIL and data.status == Status.FAIL
    d = data.details
    assert d["missing_in_target"] == 1
    assert d["extra_in_target"] == 1
    assert d["changed_rows"] == 6
    assert set(d["column_mismatches"]) == {"customer_name", "balance", "email", "is_active", "created_at", "city"}
    kinds = {(x.kind, x.key, x.column) for x in t.discrepancies}
    assert ("MISSING_IN_TARGET", "71", None) in kinds
    assert ("EXTRA_IN_TARGET", "999999", None) in kinds
    assert ("NULL_MISMATCH", "31", "email") in kinds
    null_disc = [x for x in t.discrepancies if x.kind == "NULL_MISMATCH"][0]
    assert null_disc.target_value is None and null_disc.source_value == "user31@example.com"
    if mode == "profile":
        assert t.stats["rows_fetched_source"] < N_ROWS / 3   # only mismatching buckets transferred
    # profile-level checks also catch these
    assert check(t, "nulls").status == Status.FAIL
    assert check(t, "aggregates").status == Status.FAIL
    assert check(t, "row_count").status == Status.PASS      # -1 +1 still equal counts


def test_alibaba_local_time_is_normalized_with_timezone_setting(tmp_path, rows):
    """Target stores naive timestamps in Asia/Shanghai (UTC+8) — a classic AWS->Alibaba issue."""
    write_db(tmp_path / "src.db", rows)
    write_db(tmp_path / "tgt.db", rows, ts_shift=timedelta(hours=8))

    wrong = run(project(tmp_path, [table(compare={"mode": "full"})]))
    assert check(wrong.tables[0], "data").details["column_mismatches"] == {"created_at": N_ROWS}

    for mode in ("full", "profile"):
        ok = run(project(tmp_path, [table(compare={"mode": mode})], tgt_tz="Asia/Shanghai"))
        assert ok.tables[0].status == Status.PASS, [(c.name, c.message) for c in ok.tables[0].checks]


def test_composite_keys_and_duplicates(tmp_path):
    ddl = "CREATE TABLE lines (invoice_id INTEGER, line_no INTEGER, sku TEXT, qty INTEGER, amount DECIMAL(10,2))"
    cols = ["invoice_id", "line_no", "sku", "qty", "amount"]
    src = [{"invoice_id": i // 5, "line_no": i % 5, "sku": f"SKU{i % 13}", "qty": i % 7, "amount": i * 1.25}
           for i in range(2000)]
    tgt = copy.deepcopy(src)
    tgt[5]["qty"] = 99
    tgt.append(copy.deepcopy(tgt[6]))   # duplicate composite key in target
    write_db(tmp_path / "src.db", src, ddl=ddl, table="lines", cols=cols)
    write_db(tmp_path / "tgt.db", tgt, ddl=ddl, table="lines", cols=cols)
    t = {"name": "lines", "source": {"connection": "aws", "table": "lines"},
         "target": {"connection": "ali", "table": "lines"}, "keys": ["invoice_id", "line_no"]}
    res = run(project(tmp_path, [dict(t, compare={"mode": "full"})])).tables[0]
    assert check(res, "keys").status == Status.FAIL
    assert check(res, "keys").details["target"]["duplicate_keys"] == 1
    d = check(res, "data").details
    assert d["duplicate_keys_target"] == 1 and d["changed_rows"] == 1
    assert any(x.key == "1 | 0" and x.column == "qty" for x in res.discrepancies)

    res_p = run(project(tmp_path, [dict(t, compare={"mode": "profile"})])).tables[0]
    assert check(res_p, "data").details["changed_rows"] == 1


def test_column_mapping_exclusions_and_schema_problems(tmp_path, rows):
    src_cols = ["customer_id", "customer_name", "email", "city", "balance", "score", "is_active", "created_at"]
    tgt_ddl = """CREATE TABLE customers (customer_id BIGINT PRIMARY KEY, full_name VARCHAR(120),
                 email VARCHAR(100), city VARCHAR(60), balance NUMERIC(14,2), score REAL, is_active BOOLEAN,
                 created_at DATETIME, migrated_at DATETIME)"""
    tgt_cols = ["customer_id", "full_name", "email", "city", "balance", "score", "is_active", "created_at"]
    tgt_rows = [{("full_name" if k == "customer_name" else k): v for k, v in r.items()} for r in rows]
    write_db(tmp_path / "src.db", rows, cols=src_cols)
    write_db(tmp_path / "tgt.db", tgt_rows, ddl=tgt_ddl, cols=tgt_cols)

    res = run(project(tmp_path, [table(columns={"customer_name": "full_name"}, exclude_columns=["score"])]))
    t = res.tables[0]
    schema = check(t, "schema")
    # email VARCHAR(255) -> VARCHAR(100) is a truncation risk; migrated_at only exists in target
    assert schema.status == Status.FAIL
    assert "length shrinks 255 -> 100" in schema.message
    assert "migrated_at" in schema.message
    assert check(t, "data").status == Status.PASS            # the data itself is identical
    mapped = {c["source"]: c["target"] for c in schema.details["columns"]}
    assert mapped["customer_name"] == "full_name" and "score" not in mapped


def test_filters_custom_checks_and_row_count_tolerance(tmp_path, rows):
    tgt = rows[:-30]                                      # 30 rows not yet replicated
    _setup(tmp_path, rows, tgt)
    custom = [{"name": "rows per city", "source_sql": "SELECT city, COUNT(*) FROM customers GROUP BY city",
               "target_sql": "SELECT city, COUNT(*) FROM customers GROUP BY city"},
              {"name": "total balance (cents tolerance)", "source_sql": "SELECT SUM(balance) FROM customers WHERE customer_id <= 100",
               "target_sql": "SELECT SUM(balance) + 0.004 FROM customers WHERE customer_id <= 100",
               "tolerance_abs": 0.01}]
    res = run(project(tmp_path, [table(
        tolerances={"row_count_pct": 2.0}, custom_checks=custom, checks=["row_count", "custom", "data"],
        source={"connection": "aws", "table": "customers", "filter": f"customer_id <= {len(tgt)}"},
        target={"connection": "ali", "table": "customers", "filter": f"customer_id <= {len(tgt)}"})]))
    t = res.tables[0]
    assert check(t, "row_count").status == Status.PASS
    assert check(t, "data").status == Status.PASS
    customs = [c for c in t.checks if c.check == "custom"]
    assert customs[0].status == Status.FAIL               # city counts differ (30 missing rows)
    assert customs[1].status == Status.PASS               # within tolerance


def test_query_datasets_and_null_keys(tmp_path, rows):
    tgt = copy.deepcopy(rows)
    _setup(tmp_path, rows, tgt)
    res = run(project(tmp_path, [{
        "name": "active customers",
        "source": {"connection": "aws", "query": "SELECT customer_id, city, balance FROM customers WHERE is_active = 1"},
        "target": {"connection": "ali", "query": "SELECT customer_id, city, balance FROM customers WHERE is_active = 1"},
        "keys": ["customer_id"], "checks": ["schema", "row_count", "keys", "data"]}]))
    assert res.tables[0].status == Status.PASS, [(c.name, c.message) for c in res.tables[0].checks]


def test_numeric_tolerance_per_column(tmp_path, rows):
    tgt = copy.deepcopy(rows)
    for r in tgt:
        r["score"] = r["score"] * (1 + 1e-7)      # float drift, e.g. DOUBLE -> FLOAT conversion
    _setup(tmp_path, rows, tgt)
    strict = run(project(tmp_path, [table(compare={"mode": "full"})])).tables[0]
    assert check(strict, "data").details["column_mismatches"].get("score")
    lenient = run(project(tmp_path, [table(compare={"mode": "full"},
                                           tolerances={"columns": {"score": {"rel": 1e-6}}})])).tables[0]
    assert lenient.status == Status.PASS, [(c.name, c.message) for c in lenient.checks]


def test_missing_table_is_reported_as_error(tmp_path, rows):
    _setup(tmp_path, rows, rows)
    res = run(project(tmp_path, [table(target={"connection": "ali", "table": "does_not_exist"})]))
    assert res.tables[0].status == Status.ERROR
    assert res.status == Status.ERROR


def test_parallel_tables(tmp_path, rows):
    _setup(tmp_path, rows, rows)
    tables = [table(name=f"customers_{i}", compare={"mode": m}) for i, m in enumerate(MODES * 2)]
    res = run(project(tmp_path, tables, parallel_tables=4))
    assert [t.name for t in res.tables] == [t["name"] for t in tables]
    assert all(t.status == Status.PASS for t in res.tables)


def test_profile_mode_detects_values_swapped_between_rows_in_same_bucket(tmp_path, rows):
    """Plain per-bucket column sums cannot see swaps; key-weighted checksums must."""
    import hashlib
    by_bucket = {}
    for i, r in enumerate(rows):
        b = hashlib.md5(str(r["customer_id"]).encode()).hexdigest()[:2]
        by_bucket.setdefault(b, []).append(i)
    i, j = next(v for v in by_bucket.values() if len(v) >= 2)[:2]
    tgt = copy.deepcopy(rows)
    for col in ("balance", "city", "created_at", "is_active"):
        tgt[i][col], tgt[j][col] = tgt[j][col], tgt[i][col]
    _setup(tmp_path, rows, tgt)
    res = run(project(tmp_path, [table(compare={"mode": "profile", "initial_bucket_hex_digits": 2})]))
    t = res.tables[0]
    assert check(t, "aggregates").status == Status.PASS      # table totals are unchanged by a swap ...
    d = check(t, "data").details
    assert d["changed_rows"] == 2                             # ... but row-level profiling catches it
    assert t.stats["rows_fetched_source"] < 100
