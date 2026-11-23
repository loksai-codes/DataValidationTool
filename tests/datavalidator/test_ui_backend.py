"""
The engine side of the web UI: connection field spec, project <-> YAML round trip, secrets,
cancellation, and an Athena -> Athena run through :class:`Workspace` (the UI's code path).
"""

import copy
import threading

import pytest
import yaml
from click.testing import CliRunner
from conftest import write_db
from emulation import FakeAthena, FakeS3

from datavalidator.config import CONNECTION_TYPES, ConnectionConfig, project_from_dict
from datavalidator.connectors import create_connector
from datavalidator.models import Status
from datavalidator.project_io import PlaintextSecretError, dump_project, find_plaintext_secrets
from datavalidator.runner import ProjectRunner
from datavalidator.ui_schema import (
    COMMON_FIELDS,
    DRIVERS,
    field_default,
    fields_for,
    is_reference,
    missing_required,
    secret_keys,
    types_by_driver,
)
from datavalidator.workspace import Workspace


# ============================================================================ field spec
def test_every_connection_type_has_a_spec():
    for conn_type, (_dialect, driver) in CONNECTION_TYPES.items():
        assert driver in DRIVERS, f"{conn_type}: no DriverSpec for driver '{driver}'"
        assert fields_for(conn_type), conn_type


def test_specs_only_use_connection_config_fields():
    model_fields = set(ConnectionConfig.model_fields)
    for spec in DRIVERS.values():
        for f in (*spec.fields, *COMMON_FIELDS):
            assert f.name in model_fields, f"{spec.driver}: '{f.name}' is not a ConnectionConfig field"
            assert f.label and f.kind


def test_type_groups_cover_every_type_once():
    grouped = [t for ts in types_by_driver().values() for t in ts]
    assert sorted(grouped) == sorted(CONNECTION_TYPES)


def test_defaults_come_from_the_model_and_keys_use_aliases():
    assert field_default("timezone") == ConnectionConfig.model_fields["timezone"].default
    assert field_default("session_init") == []
    schema_field = next(f for f in fields_for("aws-athena") if f.name == "schema_")
    assert schema_field.key == "schema"
    assert {"password", "access_key_secret", "session_token"} <= secret_keys()


def test_athena_fields_and_required_checks():
    keys = {f.key for f in fields_for("aws-athena")}
    assert {"region", "profile", "access_key_id", "access_key_secret", "workgroup", "s3_staging_dir",
            "catalog", "database", "schema"} <= keys
    assert "host" not in keys
    assert missing_required("alibaba-maxcompute", {"type": "alibaba-maxcompute"}) == ["Project", "Endpoint"]
    assert missing_required("aws-aurora-mysql", {"host": "h"}) == []


def test_reference_detection():
    assert is_reference("${env:X}") and is_reference("${aws_secret:a/b#k}${env:Y}")
    assert not is_reference("hunter2") and not is_reference("pre-${env:X}") and not is_reference(None)


# ============================================================================ round trip
def _two_athena_project():
    return {
        "project": "athena-to-athena",
        "parallel_tables": 2,
        "output_dir": "reports",
        "connections": {
            "lake_mumbai": {"type": "aws-athena", "cloud": "aws", "region": "ap-south-1", "profile": "acct-a",
                            "workgroup": "wg-a", "catalog": "AwsDataCatalog", "database": "lake",
                            "s3_staging_dir": "s3://results-a/dv/"},
            "lake_singapore": {"type": "aws-athena", "cloud": "aws", "region": "ap-southeast-1",
                               "access_key_id": "${env:B_KEY_ID}", "access_key_secret": "${env:B_KEY_SECRET}",
                               "workgroup": "wg-b", "database": "lake", "s3_staging_dir": "s3://results-b/dv/",
                               "timezone": "Asia/Singapore"},
        },
        "defaults": {"compare": {"mode": "profile", "fetch_batch_rows": 1000},
                     "tolerances": {"timestamp_precision": "ms"}},
        "tables": [
            {"name": "customers", "source": {"connection": "lake_mumbai", "table": "lake.customers"},
             "target": {"connection": "lake_singapore", "table": "lake.customers", "filter": "dt = '2026-11-01'"},
             "keys": ["customer_id"], "tags": ["critical"]},
            # an override back to the model default must survive the round trip
            {"name": "orders", "source": {"connection": "lake_mumbai", "table": "lake.orders"},
             "target": {"connection": "lake_singapore", "table": "lake.orders_v2"},
             "compare": {"mode": "auto"}, "columns": {"amount": "amount_usd"},
             "custom_checks": [{"name": "total", "source_sql": "SELECT 1", "target_sql": "SELECT 1"}]},
        ],
    }


def test_round_trip_ui_state_to_yaml_and_back():
    ws = Workspace(_two_athena_project())
    assert ws.errors() == []
    original = ws.project(resolve_secrets=False)
    text = ws.export_yaml()
    reloaded = project_from_dict(yaml.safe_load(text), resolve_secrets=False)
    assert reloaded == original

    a, b = reloaded.connections["lake_mumbai"], reloaded.connections["lake_singapore"]
    assert (a.region, a.workgroup, a.s3_staging_dir) != (b.region, b.workgroup, b.s3_staging_dir)
    assert reloaded.tables[1].compare.mode == "auto"          # override of the project default kept
    assert reloaded.tables[0].compare.mode == "profile"
    # exported entries stay minimal: defaults are not repeated per table
    data = yaml.safe_load(text)
    assert "compare" not in data["tables"][0]
    assert data["tables"][1]["compare"] == {"mode": "auto"}


def test_round_trip_through_workspace_import():
    text = Workspace(_two_athena_project()).export_yaml()
    ws2 = Workspace.from_yaml(text)
    assert ws2.project(resolve_secrets=False) == Workspace(_two_athena_project()).project(resolve_secrets=False)


def test_plaintext_secrets_are_never_exported(monkeypatch):
    data = _two_athena_project()
    data["connections"]["lake_mumbai"]["access_key_secret"] = "typed-secret-value"
    ws = Workspace(data)
    found = ws.plaintext_secrets()
    assert [(s.connection, s.key) for s in found] == [("lake_mumbai", "access_key_secret")]
    with pytest.raises(PlaintextSecretError):
        ws.export_yaml()
    with pytest.raises(PlaintextSecretError):
        dump_project(ws.project(resolve_secrets=False))

    monkeypatch.delenv("DV_LAKE_MUMBAI_ACCESS_KEY_SECRET", raising=False)
    env = ws.externalize_secrets()
    assert env == {"DV_LAKE_MUMBAI_ACCESS_KEY_SECRET": "typed-secret-value"}
    text = ws.export_yaml()
    assert "typed-secret-value" not in text
    assert "${env:DV_LAKE_MUMBAI_ACCESS_KEY_SECRET}" in text
    # the value is still available to runs in this process
    monkeypatch.setenv("B_KEY_ID", "id")
    monkeypatch.setenv("B_KEY_SECRET", "secret")
    assert ws.project().connections["lake_mumbai"].access_key_secret == "typed-secret-value"
    assert find_plaintext_secrets(ws.data["connections"]) == []


def test_connection_rename_updates_tables():
    ws = Workspace(_two_athena_project())
    ws.set_connection("lake_a", ws.data["connections"]["lake_mumbai"], old_name="lake_mumbai")
    assert ws.errors() == []
    assert all(t["source"]["connection"] == "lake_a" for t in ws.data["tables"])
    assert ws.delete_connection("lake_a") == ["customers", "orders"]


# ============================================================================ Athena -> Athena end to end
class AthenaFactory:
    """Builds real Athena connectors whose boto3 clients are SQLite-backed fakes (one per region)."""

    def __init__(self, dbs):
        self.dbs = dbs
        self.fakes = []

    def __call__(self, name, cfg):
        s3 = FakeS3()
        fake = FakeAthena(self.dbs[cfg.region], s3, database=cfg.database)
        self.fakes.append((cfg.region, cfg.workgroup, fake))
        cfg = cfg.model_copy()
        cfg.athena_client, cfg.s3_client = fake, s3
        return create_connector(name, cfg)


def _athena_workspace(tmp_path, rows, monkeypatch):
    tgt_rows = copy.deepcopy(rows)
    tgt_rows[5]["city"] = "Shenzhen"          # VALUE_MISMATCH
    tgt_rows[6]["email"] = None               # NULL_MISMATCH
    tgt_rows[7]["balance"] = 0                # VALUE_MISMATCH
    del tgt_rows[8]                           # MISSING_IN_TARGET
    write_db(tmp_path / "mumbai.db", rows)
    write_db(tmp_path / "singapore.db", tgt_rows)
    monkeypatch.setenv("B_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("B_KEY_SECRET", "test-secret-value")
    factory = AthenaFactory({"ap-south-1": str(tmp_path / "mumbai.db"),
                             "ap-southeast-1": str(tmp_path / "singapore.db")})
    data = _two_athena_project()
    data["tables"] = []
    data["connections"]["lake_singapore"].pop("timezone")   # both lakes store naive UTC timestamps
    data["defaults"] = {"tolerances": {"timestamp_precision": "ms"}}
    data["output_dir"] = str(tmp_path / "reports")
    return Workspace(data, connector_factory=factory), factory


@pytest.mark.parametrize("mode", ["full", "profile"])
def test_athena_to_athena_end_to_end_through_workspace(tmp_path, rows, monkeypatch, mode):
    ws, factory = _athena_workspace(tmp_path, rows, monkeypatch)

    # 1. test both connections
    for name in ("lake_mumbai", "lake_singapore"):
        res = ws.test_connection(name)
        assert res["ok"], res
        assert res["type"] == "aws-athena"

    # 2. discover and pair (Athena exposes no primary keys, so the key is set by hand)
    assert ws.schemas("lake_mumbai") == ["lake"]
    match = ws.match("lake_mumbai", "lake", "lake_singapore", "lake")
    assert [e["name"] for e in match.entries] == ["customers"]
    assert match.no_pk == ["customers"]
    entry = dict(match.entries[0], keys=["customer_id"])
    assert ws.add_tables([entry]) == []
    desc = ws.describe("lake_mumbai", "customers", "lake")
    assert "customer_id" in desc.column_names and desc.primary_keys == []
    ws.data["tables"][0]["compare"] = {"mode": mode}
    assert ws.errors() == []

    # 3. review SQL: Athena SQL on both sides and the billing warning
    ex = ws.explain("customers")
    assert [s.dialect for s in ex.sides] == ["athena", "athena"]
    assert all(s.billing_note and not s.has_filter for s in ex.sides)
    assert "lower(to_hex(md5(to_utf8(" in ex.sides[0].bucket_sql

    # 4. run in the background, exactly as the Run screen does
    job = ws.start_run()
    assert job.wait(timeout=120)
    snap = job.snapshot()
    assert snap.error is None and snap.state == "done"
    run = job.result
    t = run.tables[0]
    assert t.status == Status.FAIL, [(c.name, c.status, c.message) for c in t.checks]
    kinds = {d.kind for d in t.discrepancies}
    assert {"VALUE_MISMATCH", "MISSING_IN_TARGET", "NULL_MISMATCH"} <= kinds
    data_check = next(c for c in t.checks if c.check == "data")
    assert data_check.details["changed_rows"] == 3
    assert data_check.details["missing_in_target"] == 1
    assert data_check.details["extra_in_target"] == 0
    assert {d.key for d in t.discrepancies} == {"6", "7", "8", "9"}
    assert snap.tables[0].status == "FAIL"
    assert any("[customers]" in line for line in snap.log)

    # each side ran in its own account / region / workgroup
    used = {(region, wg) for region, wg, fake in factory.fakes if fake.queries}
    assert ("ap-south-1", "wg-a") in used and ("ap-southeast-1", "wg-b") in used

    # 5. reports + history, as the Results / History screens read them
    files = ws.run_files(run.run_id)
    assert {"report.html", "results.json", "checks.csv", "discrepancies.csv", "junit.xml", "summary.md"} <= set(files)
    assert ws.history()[0]["run_id"] == run.run_id
    assert ws.load_run(run.run_id) == run

    # 6. the exported YAML runs with the same result
    text = ws.export_yaml()
    assert "test-secret-value" not in text
    path = tmp_path / "exported.yaml"
    path.write_text(text, encoding="utf-8")
    from datavalidator.config import load_project
    reloaded = load_project(str(path))
    assert reloaded == ws.project()
    runner = ProjectRunner(reloaded, connector_factory=AthenaFactory(factory.dbs))
    again = runner.run().tables[0]
    assert again.status == t.status
    assert sorted((d.kind, d.key, d.column) for d in again.discrepancies) == \
        sorted((d.kind, d.key, d.column) for d in t.discrepancies)


def test_identical_athena_tables_pass(tmp_path, rows, monkeypatch):
    ws, _ = _athena_workspace(tmp_path, rows, monkeypatch)
    write_db(tmp_path / "singapore.db", rows)
    ws.add_tables([{"name": "customers", "source": {"connection": "lake_mumbai", "table": "lake.customers"},
                    "target": {"connection": "lake_singapore", "table": "lake.customers"},
                    "keys": ["customer_id"]}])
    job = ws.start_run(mode="profile")
    assert job.wait(timeout=120)
    assert job.result.status == Status.PASS, [(c.name, c.message) for c in job.result.tables[0].checks]


# ============================================================================ cancellation
def test_cancel_before_start_marks_tables_cancelled(tmp_path, rows):
    from conftest import project, table
    write_db(tmp_path / "src.db", rows)
    write_db(tmp_path / "tgt.db", rows)
    proj = project(tmp_path, [table(), table(name="again")])
    ev = threading.Event()
    ev.set()
    run = ProjectRunner(proj, cancel_event=ev).run()
    assert run.cancelled
    assert all(t.status == Status.ERROR and t.checks[0].name == "Cancelled" for t in run.tables)


def test_cancel_during_table_stops_at_next_step(tmp_path, rows):
    from conftest import project, table
    write_db(tmp_path / "src.db", rows)
    write_db(tmp_path / "tgt.db", rows)
    ev = threading.Event()

    def progress(tbl, msg):
        if msg.startswith("profiling"):
            ev.set()

    run = ProjectRunner(project(tmp_path, [table()]), progress=progress, cancel_event=ev).run()
    t = run.tables[0]
    assert t.status == Status.ERROR and run.cancelled
    assert t.checks[-1].name == "Cancelled"


# ============================================================================ CLI
def test_ui_command_without_streamlit(monkeypatch):
    import importlib.util

    from datavalidator.cli import main
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: None if n == "streamlit" else real(n, *a))
    r = CliRunner().invoke(main, ["ui"])
    assert r.exit_code == 3
    assert 'pip install ".[ui]"' in r.output


# ============================================================================ source / target roles
def test_connection_roles_explicit_and_from_tables():
    data = _two_athena_project()
    ws = Workspace(data)
    # no role set: derived from the tables that use each connection
    assert ws.connection_role("lake_mumbai") == "source"
    assert ws.connection_role("lake_singapore") == "target"
    assert ws.connection_usage("lake_mumbai") == {"source": ["customers", "orders"], "target": []}
    # an explicit role wins; "both" counts for either side
    ws.data["connections"]["lake_singapore"]["role"] = "both"
    assert ws.connections_for("source") == ["lake_mumbai", "lake_singapore"]
    assert ws.connections_for("target") == ["lake_singapore"]
    ws.set_connection("spare", {"type": "sqlite", "database": ":memory:"})
    assert ws.connection_role("spare") is None


def test_role_survives_yaml_round_trip():
    data = _two_athena_project()
    data["connections"]["lake_mumbai"]["role"] = "source"
    data["connections"]["lake_singapore"]["role"] = "target"
    ws = Workspace(data)
    text = ws.export_yaml()
    assert "role: source" in text and "role: target" in text
    assert project_from_dict(yaml.safe_load(text), resolve_secrets=False) == ws.project(resolve_secrets=False)


def test_invalid_role_is_rejected():
    data = _two_athena_project()
    data["connections"]["lake_mumbai"]["role"] = "sideways"
    assert any("role" in e for e in Workspace(data).errors())
