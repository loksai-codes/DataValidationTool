"""Shared fixtures: two SQLite databases standing in for an AWS source and an Alibaba target."""

import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))

from datavalidator.config import ProjectConfig  # noqa: E402
from datavalidator.runner import ProjectRunner  # noqa: E402

N_ROWS = 3000


def make_rows(n=N_ROWS, seed=7):
    rnd = random.Random(seed)
    base = datetime(2025, 3, 1, 8, 0, 0)
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "customer_id": i,
            "customer_name": f"Customer {i}",
            "email": None if i % 97 == 0 else f"user{i}@example.com",
            "city": rnd.choice(["Mumbai", "Pune", "Singapore", "Hangzhou", "Jakarta"]),
            "balance": round(rnd.uniform(-500, 50000), 2),
            "score": rnd.random() * 100,
            "is_active": i % 3 != 0,
            "created_at": base + timedelta(minutes=17 * i, seconds=i % 60, microseconds=(i * 1013) % 1000000),
        })
    return rows


SRC_DDL = """CREATE TABLE customers (
  customer_id BIGINT PRIMARY KEY, customer_name VARCHAR(120), email VARCHAR(255), city VARCHAR(60),
  balance DECIMAL(14,2), score DOUBLE, is_active BOOLEAN, created_at TIMESTAMP)"""
TGT_DDL = """CREATE TABLE customers (
  customer_id BIGINT PRIMARY KEY, customer_name VARCHAR(120), email VARCHAR(255), city VARCHAR(60),
  balance NUMERIC(14,2), score REAL, is_active BOOLEAN, created_at DATETIME)"""
COLS = ["customer_id", "customer_name", "email", "city", "balance", "score", "is_active", "created_at"]


def write_db(path, rows, ddl=SRC_DDL, table="customers", cols=COLS, ts_shift=timedelta(0)):
    conn = sqlite3.connect(path)
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(ddl)
    def val(r, c):
        v = r[c]
        if isinstance(v, datetime):
            return (v + ts_shift).strftime("%Y-%m-%d %H:%M:%S.%f")
        if isinstance(v, bool):
            return int(v)
        return v
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [tuple(val(r, c) for c in cols) for r in rows])
    conn.commit()
    conn.close()


def project(tmp_path, tables, src_tz="UTC", tgt_tz="UTC", **extra):
    return ProjectConfig.model_validate({
        "project": "test",
        "connections": {
            "aws": {"type": "sqlite", "database": str(tmp_path / "src.db"), "cloud": "aws", "timezone": src_tz},
            "ali": {"type": "sqlite", "database": str(tmp_path / "tgt.db"), "cloud": "alibaba", "timezone": tgt_tz},
        },
        "tables": tables,
        "output_dir": str(tmp_path / "reports"),
        **extra,
    })


def table(name="customers", **kw):
    t = {"name": name, "source": {"connection": "aws", "table": "customers"},
         "target": {"connection": "ali", "table": "customers"}}
    t.update(kw)
    return t


def run(proj):
    r = ProjectRunner(proj)
    try:
        return r.run()
    finally:
        r.close()


def check(table_result, name):
    for c in table_result.checks:
        if c.check == name:
            return c
    raise AssertionError(f"no check {name}: {[c.check for c in table_result.checks]}")


@pytest.fixture
def rows():
    return make_rows()
