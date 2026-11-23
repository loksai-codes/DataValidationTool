"""Smoke tests of the Streamlit app itself, run headlessly with streamlit.testing.AppTest."""

import os

import pytest
from conftest import write_db

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from datavalidator.ui import screens  # noqa: E402
from datavalidator.workspace import Workspace  # noqa: E402

APP = os.path.join(os.path.dirname(screens.__file__), "app.py")


def _app(ws, screen):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["ws"] = ws
    at.session_state["nav"] = screen
    return at.run()


def _workspace(tmp_path, rows):
    write_db(tmp_path / "src.db", rows)
    tgt = [dict(r) for r in rows]
    tgt[3]["city"] = "Elsewhere"
    write_db(tmp_path / "tgt.db", tgt)
    return Workspace({
        "project": "ui-smoke", "output_dir": str(tmp_path / "reports"),
        "connections": {"src": {"type": "sqlite", "database": str(tmp_path / "src.db")},
                        "tgt": {"type": "sqlite", "database": str(tmp_path / "tgt.db")}},
        "tables": [{"name": "customers", "source": {"connection": "src", "table": "customers"},
                    "target": {"connection": "tgt", "table": "customers"}}],
    })


@pytest.mark.parametrize("screen", list(screens.SCREENS))
def test_every_screen_renders(tmp_path, rows, screen):
    at = _app(_workspace(tmp_path, rows), screen)
    assert not at.exception, [e.value for e in at.exception]
    assert at.header[0].value == screen


def test_new_connection_form_lists_every_engine_family(tmp_path, rows):
    at = _app(_workspace(tmp_path, rows), "Connections")
    family = next(s for s in at.selectbox if s.label == "Engine family")
    from datavalidator.ui_schema import types_by_driver
    assert family.options == [screens.DRIVERS[d].label for d in types_by_driver()]


def test_test_connection_button(tmp_path, rows):
    at = _app(_workspace(tmp_path, rows), "Connections")
    at.selectbox(key="conn_choice").set_value("src").run()
    next(b for b in at.button if b.label == "Test connection").click().run()
    assert not at.exception
    assert any("src: OK" in s.value for s in at.success)


def test_review_sql_and_run_to_results(tmp_path, rows):
    ws = _workspace(tmp_path, rows)
    at = _app(ws, "Review SQL")
    next(b for b in at.button if b.label == "Show SQL").click().run()
    assert not at.exception
    assert any("SELECT COUNT(*) AS dv_rows" in c.value for c in at.code)

    job = ws.start_run()
    assert job.wait(timeout=60)
    at.session_state["job"] = job
    at.session_state["nav"] = "Results"
    at.run()
    assert not at.exception
    assert any("FAIL" in m.value for m in at.markdown)
    assert any(d.value is not None for d in at.dataframe)

    at.session_state["nav"] = "History"
    at.run()
    assert not at.exception
    assert job.result.run_id in at.selectbox(key="hist_sel").options


def test_export_blocks_plaintext_secrets(tmp_path, rows):
    ws = _workspace(tmp_path, rows)
    ws.data["connections"]["src"]["password"] = "typed-value"
    at = _app(ws, "Project")
    assert not at.exception
    assert any("typed values" in w.value for w in at.warning)
    assert next(b for b in at.button if b.label == "Download YAML").disabled


def test_connections_screen_groups_source_and_target(tmp_path, rows):
    at = _app(_workspace(tmp_path, rows), "Connections")
    assert not at.exception
    labels = [m.value for m in at.markdown]
    assert any(v.startswith("**Source connections** (1)") for v in labels)
    assert any(v.startswith("**Target connections** (1)") for v in labels)
    # a new connection defaults to Target once a source exists
    assert at.radio(key="conn_new_role").value == "target"
