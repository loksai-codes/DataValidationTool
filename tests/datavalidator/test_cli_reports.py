"""CLI commands, exit codes and report formats."""

import copy
import json
import os
import xml.etree.ElementTree as ET

import yaml
from click.testing import CliRunner
from conftest import write_db

from datavalidator.cli import main


def _project_file(tmp_path, **table_kw):
    t = {"name": "customers", "source": {"connection": "aws", "table": "customers"},
         "target": {"connection": "ali", "table": "customers"}}
    t.update(table_kw)
    cfg = {"project": "cli-test", "output_dir": str(tmp_path / "reports"),
           "connections": {"aws": {"type": "sqlite", "database": str(tmp_path / "src.db"), "cloud": "aws"},
                           "ali": {"type": "sqlite", "database": str(tmp_path / "tgt.db"), "cloud": "alibaba"}},
           "tables": [t]}
    p = tmp_path / "project.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return str(p)


def test_validate_pass_and_reports(tmp_path, rows):
    write_db(tmp_path / "src.db", rows)
    write_db(tmp_path / "tgt.db", rows)
    res = CliRunner().invoke(main, ["validate", "-c", _project_file(tmp_path), "-q"])
    assert res.exit_code == 0, res.output
    run_dir = next((tmp_path / "reports").glob("2*"))
    data = json.loads((run_dir / "results.json").read_text())
    assert data["status"] == "PASS" and data["tables"][0]["stats"]["source_rows"] == len(rows)
    html = (run_dir / "report.html").read_text()
    assert "cli-test" in html and "PASS" in html
    junit = ET.parse(run_dir / "junit.xml").getroot()
    assert junit.tag == "testsuites" and junit.attrib["failures"] == "0"
    assert (run_dir / "summary.md").read_text().startswith("## Migration validation")
    assert (tmp_path / "reports" / "history.jsonl").exists()


def test_validate_fail_exit_code_and_discrepancy_csv(tmp_path, rows):
    tgt = copy.deepcopy(rows)
    tgt[0]["city"] = "<script>alert(1)</script>"
    write_db(tmp_path / "src.db", rows)
    write_db(tmp_path / "tgt.db", tgt)
    res = CliRunner().invoke(main, ["validate", "-c", _project_file(tmp_path), "-q", "--mode", "full"])
    assert res.exit_code == 1, res.output
    run_dir = next((tmp_path / "reports").glob("2*"))
    csv_text = (run_dir / "discrepancies.csv").read_text()
    assert "VALUE_MISMATCH" in csv_text and "city" in csv_text
    html = (run_dir / "report.html").read_text()
    assert "<script>alert(1)</script>" not in html          # report output is HTML-escaped
    junit = ET.parse(run_dir / "junit.xml").getroot()
    assert int(junit.attrib["failures"]) >= 1


def test_validate_error_exit_code(tmp_path, rows):
    write_db(tmp_path / "src.db", rows)
    write_db(tmp_path / "tgt.db", rows)
    res = CliRunner().invoke(main, ["validate", "-c", _project_file(
        tmp_path, target={"connection": "ali", "table": "nope"}), "-q"])
    assert res.exit_code == 2


def test_config_error_exit_code(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("project: x\nconnections: {}\ntables: [{name: a}]\n")
    assert CliRunner().invoke(main, ["validate", "-c", str(p)]).exit_code == 3


def test_test_connections_discover_explain_generate(tmp_path, rows):
    write_db(tmp_path / "src.db", rows)
    write_db(tmp_path / "tgt.db", rows)
    cfg = _project_file(tmp_path)
    r = CliRunner()
    out = r.invoke(main, ["test-connections", "-c", cfg])
    assert out.exit_code == 0 and out.output.count("OK") == 2, out.output

    out = r.invoke(main, ["discover", "-c", cfg, "--connection", "aws", "--table", "customers"])
    assert "customer_id" in out.output and "[PK]" in out.output

    out = r.invoke(main, ["explain", "-c", cfg, "-t", "customers"])
    assert out.exit_code == 0 and "GROUP BY" in out.output and "md5(" in out.output

    out = r.invoke(main, ["generate-tables", "-c", cfg, "--source", "aws", "--target", "ali"])
    gen = yaml.safe_load(out.output)
    assert gen["tables"][0]["keys"] == ["customer_id"]


def test_init_writes_valid_template(tmp_path, monkeypatch):
    path = tmp_path / "new.yaml"
    res = CliRunner().invoke(main, ["init", str(path)])
    assert res.exit_code == 0 and path.exists()
    from datavalidator.config import load_project
    p = load_project(str(path), resolve_secrets=False)
    assert {c.cloud for c in p.connections.values()} == {"aws", "alibaba"}
    assert len(p.tables) >= 4
    assert os.path.getsize(path) > 1000
