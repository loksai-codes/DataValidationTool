"""Streamlit entry point for the dvtool web UI (``dvtool ui [--config project.yaml]``)."""

from __future__ import annotations

import os
import sys

try:
    import datavalidator  # noqa: F401
except ImportError:  # running from a source checkout without installing
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import streamlit as st

from datavalidator import __version__
from datavalidator.ui import screens
from datavalidator.workspace import Workspace


def _config_arg() -> str | None:
    args = sys.argv[1:]
    if "--config" in args and args.index("--config") + 1 < len(args):
        return args[args.index("--config") + 1]
    return None


def _init() -> None:
    if "ws" in st.session_state:
        return
    path = _config_arg()
    ws = None
    if path:
        try:
            ws = Workspace.from_path(path)
            st.session_state["project_path"] = path
        except Exception as exc:  # noqa: BLE001
            st.session_state["flash_error"] = f"Could not open {path}: {exc}"
    st.session_state["ws"] = ws or Workspace()


def _apply_requests() -> None:
    """Apply selections requested by a screen before the widgets are drawn."""
    for req, key in (("nav_request", "nav"), ("conn_choice_request", "conn_choice"),
                     ("tbl_choice_request", "tbl_choice")):
        if req in st.session_state:
            st.session_state[key] = st.session_state.pop(req)
    ws = st.session_state["ws"]
    valid = {"conn_choice": [screens.NEW] + ws.connection_names(), "tbl_choice": ws.table_names(),
             "opt_scope": ["Project defaults"] + ws.table_names()}
    for key, options in valid.items():
        if key in st.session_state and st.session_state[key] not in options:
            del st.session_state[key]


def main() -> None:
    st.set_page_config(page_title="dvtool", layout="wide")
    _init()
    _apply_requests()
    ws = st.session_state["ws"]
    with st.sidebar:
        st.title("dvtool")
        st.caption(f"v{__version__} · {ws.data.get('project') or 'untitled'}")
        screen = st.radio("Screen", list(screens.SCREENS), key="nav", label_visibility="collapsed")
        errs = ws.errors()
        st.caption(f"{len(ws.connection_names())} connection(s) · {len(ws.table_names())} table(s)")
        if errs:
            st.caption(f":orange[{len(errs)} problem(s) in the project]")
        job = st.session_state.get("job")
        if job is not None and not job.done:
            st.caption(":blue[validation running]")
    if "flash_error" in st.session_state:
        st.error(st.session_state.pop("flash_error"))
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))
    screens.SCREENS[screen]()


main()
