"""
The eight screens of the dvtool web UI.

Screens only edit the project dict held by :class:`~datavalidator.workspace.Workspace` and call
its methods; all discovery, SQL generation and validation happen in the engine.
"""

from __future__ import annotations

import copy
import json
import os
import typing
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from datavalidator.config import (
    ALL_CHECKS,
    CompareOptions,
    ConnectionConfig,
    CustomCheck,
    TableDefaults,
    _deep_merge,
)
from datavalidator.models import RunResult
from datavalidator.reports import REPORT_FORMATS
from datavalidator.ui import forms
from datavalidator.ui_schema import (
    DRIVERS,
    fields_for,
    missing_required,
    type_label,
    types_by_driver,
)
from datavalidator.workspace import Workspace, format_validation_error

STATUS_COLOR = {"PASS": "green", "WARN": "orange", "FAIL": "red", "ERROR": "violet", "SKIPPED": "gray"}
NEW = "+ New connection"


def badge(status: Optional[str]) -> str:
    if not status:
        return ""
    return f":{STATUS_COLOR.get(status, 'gray')}[**{status}**]"


def ws() -> Workspace:
    return st.session_state["ws"]


def go(screen: str) -> None:
    st.session_state["nav_request"] = screen
    st.rerun(scope="app")


def clear_widgets(prefix: str) -> None:
    """Forget widget values so they are re-initialised from the project."""
    for k in [k for k in st.session_state if isinstance(k, str) and k.startswith(prefix)]:
        del st.session_state[k]


def set_workspace(new: Workspace) -> None:
    """Replace the open project and reset every widget and run tied to the old one."""
    old = st.session_state.get("ws")
    if old is not None:
        old.close()
    for k in [k for k in st.session_state if k != "nav"]:
        del st.session_state[k]
    st.session_state["ws"] = new


def _try(fn, what: str):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        st.error(f"{what} failed: {'; '.join(format_validation_error(exc))}")
        return None


# ============================================================================ 1. Project
def project_screen() -> None:
    w = ws()
    st.header("Project")
    st.caption("The project is the same YAML file the CLI runs. Build it here, then export it, "
               "or import an existing one.")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("New")
        name = st.text_input("Project name", value="new-project", key="new_project_name")
        if st.button("Start a new project", key="new_project_btn"):
            set_workspace(Workspace(data={"project": name}))
            st.rerun()
    with c2:
        st.subheader("Import YAML")
        up = st.file_uploader("Project file", type=["yaml", "yml"], key="import_upload")
        if up is not None and st.button("Import uploaded file", key="import_btn"):
            new = _try(lambda: Workspace.from_yaml(up.getvalue().decode("utf-8")), "Import")
            if new:
                set_workspace(new)
                st.session_state["flash"] = (f"Imported {len(new.connection_names())} connection(s), "
                                             f"{len(new.table_names())} table(s).")
                st.rerun()
    with c3:
        st.subheader("Open from path")
        path = st.text_input("Path on this machine", key="import_path",
                             help="Use this for files with 'include:' lists.")
        if path and st.button("Open", key="open_path_btn"):
            new = _try(lambda: Workspace.from_path(path), "Open")
            if new:
                set_workspace(new)
                st.session_state["project_path"] = path
                st.rerun()

    st.divider()
    st.subheader("Settings")
    d = w.data
    d["project"] = st.text_input("Project name", value=d.get("project", ""), key="p_name")
    d["description"] = st.text_input("Description", value=d.get("description") or "", key="p_desc") or None
    if d["description"] is None:
        d.pop("description")
    c1, c2 = st.columns(2)
    d["parallel_tables"] = int(c1.number_input("Tables validated in parallel", min_value=1, step=1,
                                               value=int(d.get("parallel_tables") or 1), key="p_parallel"))
    d["output_dir"] = c2.text_input("Report directory", value=d.get("output_dir", ""), key="p_out",
                                    help="Relative paths are relative to where 'dvtool ui' was started.")

    errs = w.errors()
    if errs:
        st.warning("The project is not valid yet:\n\n" + "\n".join(f"- {e}" for e in errs))

    st.divider()
    st.subheader("Export YAML")
    secrets = w.plaintext_secrets()
    if secrets:
        st.warning("Some secret fields hold typed values. They are never written to a file. "
                   "Convert them to environment variable references; the values stay available "
                   "to this session so runs keep working.")
        df = pd.DataFrame([{"connection": s.connection, "field": s.key, "environment variable": s.suggested_env}
                           for s in secrets])
        edited = st.data_editor(df, key="secret_names", width="stretch", disabled=["connection", "field"])
        if st.button("Convert to ${env:...} references", key="externalize_btn"):
            names = {(r["connection"], r["field"]): r["environment variable"] for r in edited.to_dict("records")}
            env = w.externalize_secrets(names)
            st.session_state.setdefault("exported_env", set()).update(env)
            clear_widgets("conn_")  # password widgets still hold the typed values
            st.rerun()
    if st.session_state.get("exported_env"):
        st.info("Before running the exported file with the CLI, set these environment variables: "
                + ", ".join(f"`{v}`" for v in sorted(st.session_state["exported_env"])))
    if errs or secrets:
        st.button("Download YAML", disabled=True, key="dl_disabled",
                  help="Fix the problems above first.")
        return
    text = _try(w.export_yaml, "Export")
    if text is None:
        return
    fname = f"{d.get('project') or 'project'}.yaml"
    c1, c2 = st.columns([1, 2])
    c1.download_button("Download YAML", text, file_name=fname, mime="application/x-yaml", key="dl_yaml")
    with c2:
        save_to = st.text_input("Or save to path", value=st.session_state.get("project_path", ""), key="save_path")
        if save_to and st.button("Save", key="save_btn"):
            with open(save_to, "w", encoding="utf-8") as fh:
                fh.write(text)
            st.session_state["project_path"] = save_to
            st.success(f"Saved {save_to}. Run it with: dvtool validate -c {save_to}")
    with st.expander("Preview"):
        st.code(text, language="yaml")


# ============================================================================ 2. Connections
#: role values come from the model: Optional[Literal[...]] -> the literal's values
ROLES = typing.get_args(typing.get_args(ConnectionConfig.model_fields["role"].annotation)[0])
ROLE_LABELS = {"source": "Source", "target": "Target", "both": "Source and target"}


def _conn_row(name: str, role: Optional[str]) -> Dict[str, Any]:
    w = ws()
    raw = w.data["connections"][name]
    secret_keys = {f.key for f in fields_for(raw["type"]) if f.secret} if raw.get("type") else set()
    summary = ", ".join(f"{k}={forms.mask_value(k, v, k in secret_keys)}" for k, v in raw.items()
                        if k not in ("type", "role") and not isinstance(v, (dict, list)))
    used = w.connection_usage(name)
    tables = used.get(role, []) if role else used["source"] + used["target"]
    return {"name": name, "type": raw.get("type", ""), "used by tables": ", ".join(tables), "settings": summary}


def _conn_label(name: str) -> str:
    role = ws().connection_role(name)
    return f"{name}  ({ROLE_LABELS[role].lower()})" if role else name


def _role_index(names: List[str], role: str, fallback: int) -> int:
    """Index of the first connection meant for a side, preferring ones marked for that side only."""
    w = ws()
    for wanted in (role, "both"):
        for i, n in enumerate(names):
            if w.connection_role(n) == wanted:
                return i
    return fallback


def _type_picker(current: Optional[str], key: str) -> str:
    groups = types_by_driver()
    drivers = list(groups)
    cur_driver = next((d for d, ts in groups.items() if current in ts), drivers[0])
    c1, c2 = st.columns([1, 2])
    driver = c1.selectbox("Engine family", drivers, index=drivers.index(cur_driver), key=key + "_family",
                          format_func=lambda d: DRIVERS[d].label if d in DRIVERS else d,
                          help="Any engine can be the source or the target, including the same type on both sides.")
    types = groups[driver]
    idx = types.index(current) if current in types else 0
    t = c2.selectbox("Engine type", types, index=idx, key=key + "_type", format_func=type_label)
    if driver in DRIVERS:
        st.caption(DRIVERS[driver].description)
    return t


def connections_screen() -> None:
    w = ws()
    st.header("Connections")
    st.caption("Each connection is independent. Source and target can be the same engine type with "
               "different accounts, regions or settings.")

    names = w.connection_names()
    if names:
        c1, c2 = st.columns(2)
        for col, role in ((c1, "source"), (c2, "target")):
            with col:
                members = w.connections_for(role)
                st.markdown(f"**{ROLE_LABELS[role]} connections** ({len(members)})")
                if members:
                    st.dataframe(pd.DataFrame([_conn_row(n, role) for n in members]), width="stretch", hide_index=True)
                else:
                    st.caption(f"None yet. Add a connection and set its role to {ROLE_LABELS[role]}.")
        unassigned = [n for n in names if w.connection_role(n) is None]
        if unassigned:
            st.markdown(f"**No role yet** ({len(unassigned)})")
            st.dataframe(pd.DataFrame([_conn_row(n, None) for n in unassigned]), width="stretch", hide_index=True)

    choice = st.selectbox("Edit connection", [NEW] + names, key="conn_choice")
    is_new = choice == NEW
    raw = {} if is_new else copy.deepcopy(w.data["connections"][choice])
    kp = "conn_new" if is_new else f"conn_{choice}"

    name = st.text_input("Connection name", value="" if is_new else choice, key=kp + "_name",
                         help="Used by tables to refer to this connection.")
    if is_new:
        role_now = "target" if w.connections_for("source") else "source"
    else:
        role_now = raw.get("role")
    role_opts = [None] + list(ROLES)
    role = st.radio("Role", role_opts, index=role_opts.index(role_now) if role_now in role_opts else 0,
                    key=kp + "_role", horizontal=True,
                    format_func=lambda r: "Not set" if r is None else ROLE_LABELS[r],
                    help="Which side of the migration this connection is for. A table decides the actual "
                         "side, so this only labels and preselects connections.")
    conn_type = _type_picker(raw.get("type"), kp)
    values, errors = forms.connection_form(conn_type, raw if raw.get("type") == conn_type else
                                           {k: v for k, v in raw.items() if k != "type"}, kp + "_" + conn_type)
    values.pop("role", None)
    if role:
        values["role"] = role

    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Save", type="primary", key=kp + "_save"):
        problems = list(errors)
        if not name.strip():
            problems.append("Connection name is required")
        elif name != choice and name in names:
            problems.append(f"A connection named '{name}' already exists")
        problems += [f"{lbl} is required" for lbl in missing_required(conn_type, values)]
        if not problems:
            try:
                ConnectionConfig.model_validate(values)
            except Exception as exc:  # noqa: BLE001
                problems += format_validation_error(exc)
        if problems:
            st.error("\n".join(f"- {p}" for p in problems))
        else:
            w.set_connection(name.strip(), values, old_name=None if is_new else choice)
            st.session_state["conn_choice_request"] = name.strip()
            st.rerun()
    if not is_new:
        if c2.button("Test connection", key=kp + "_test"):
            with st.spinner("Connecting..."):
                r = w.test_connection(choice)
            lat = f" in {r['latency_ms']} ms" if r.get("latency_ms") is not None else ""
            (st.success if r["ok"] else st.error)(f"{choice}: {'OK' if r['ok'] else 'FAILED'}{lat}. {r['message']}")
            if values != raw:
                st.caption("Tested the saved settings. Save first to test your edits.")
        if c3.button("Duplicate", key=kp + "_dup",
                     help="Copy as a new connection, e.g. the other side of an Athena to Athena migration. "
                          "A source copy becomes a target and vice versa."):
            new_name = f"{choice}_copy"
            i = 2
            while new_name in names:
                new_name, i = f"{choice}_copy{i}", i + 1
            dup = copy.deepcopy(w.data["connections"][choice])
            flip = {"source": "target", "target": "source"}
            if dup.get("role") in flip:
                dup["role"] = flip[dup["role"]]
            w.set_connection(new_name, dup)
            st.session_state["conn_choice_request"] = new_name
            st.rerun()
        if c4.button("Delete", key=kp + "_del"):
            still = w.delete_connection(choice)
            if still:
                st.session_state["flash"] = f"Deleted '{choice}'. Tables still using it: {', '.join(still)}."
            st.rerun()


# ============================================================================ 3. Tables
def _schema_picker(conn: str, key: str) -> Optional[str]:
    w = ws()
    schemas = None
    try:
        schemas = w.schemas(conn)
    except Exception as exc:  # noqa: BLE001
        st.caption(f"Could not list schemas ({exc.__class__.__name__}); type one instead.")
    if schemas:
        opts = ["(connection default)"] + schemas
        s = st.selectbox("Schema", opts, key=key)
        return None if s == opts[0] else s
    return st.text_input("Schema", key=key + "_text") or None


def _split(ref: Dict[str, Any]) -> tuple:
    t = ref.get("table") or ""
    schema = ref.get("schema")
    if not schema and "." in t:
        schema, t = t.rsplit(".", 1)
    return schema, t


def _join(schema: Optional[str], table: str) -> str:
    return f"{schema}.{table}" if schema else table


def tables_screen() -> None:
    w = ws()
    st.header("Tables")
    names = w.connection_names()
    if not names:
        st.info("Add connections first.")
        return

    st.subheader("Discover and pair")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Source**")
        src = st.selectbox("Connection", names, key="disc_src", index=_role_index(names, "source", 0),
                           format_func=_conn_label)
        src_schema = _schema_picker(src, "disc_src_schema")
    with c2:
        st.markdown("**Target**")
        tgt = st.selectbox("Connection", names, key="disc_tgt",
                           index=_role_index(names, "target", min(1, len(names) - 1)), format_func=_conn_label)
        tgt_schema = _schema_picker(tgt, "disc_tgt_schema")
    c1, c2, c3 = st.columns([1, 1, 1])
    include = c1.text_input("Include tables (glob)", value="*", key="disc_inc")
    exclude = c2.text_input("Exclude tables (glob)", value="", key="disc_exc")
    if c3.button("Discover tables", type="primary", key="disc_btn"):
        with st.spinner("Listing tables on both sides..."):
            res = _try(lambda: w.match(src, src_schema, tgt, tgt_schema, include, exclude), "Discovery")
            src_tables = _try(lambda: w.tables(src, src_schema), "Listing source tables") or []
            tgt_tables = _try(lambda: w.tables(tgt, tgt_schema), "Listing target tables") or []
        if res is not None:
            clear_widgets("disc_editor")
            st.session_state["disc"] = {"res": res, "src": src, "tgt": tgt, "src_schema": src_schema,
                                        "tgt_schema": tgt_schema, "src_tables": src_tables, "tgt_tables": tgt_tables}

    disc = st.session_state.get("disc")
    if disc:
        res = disc["res"]
        st.caption(f"{len(res.entries)} matched by name · {len(res.unmatched)} source table(s) without a match · "
                   f"{len(res.target_only)} target table(s) without a match")
        if res.entries:
            existing = set(w.table_names())
            df = pd.DataFrame([{
                "add": e["name"] not in existing, "name": e["name"], "source table": e["source"]["table"],
                "target table": e["target"]["table"], "keys": ", ".join(e["keys"]),
                "note": "already in project" if e["name"] in existing else ("" if e["keys"] else "no primary key found: set keys"),
            } for e in res.entries])
            edited = st.data_editor(df, key="disc_editor", width="stretch", hide_index=True,
                                    disabled=["source table", "target table", "note"])
            if st.button("Add checked pairs", key="disc_add"):
                entries = []
                for r in edited.to_dict("records"):
                    if not r["add"]:
                        continue
                    e = next(x for x in res.entries if x["source"]["table"] == r["source table"])
                    entries.append({"name": r["name"], "source": e["source"], "target": e["target"],
                                    "keys": forms.csv_list(r["keys"])})
                skipped = w.add_tables(entries)
                st.session_state["flash"] = f"Added {len(entries) - len(skipped)} table(s)." + (
                    f" Skipped existing: {', '.join(skipped)}." if skipped else "")
                st.rerun()

        with st.expander("Pair tables manually (different names)"):
            c1, c2 = st.columns(2)
            s_t = c1.selectbox("Source table", disc["src_tables"] or [""], key="man_src")
            t_t = c2.selectbox("Target table", disc["tgt_tables"] or [""], key="man_tgt")
            pname = st.text_input("Name in the project", value=s_t or "", key="man_name")
            if st.button("Add pair", key="man_add") and s_t and t_t:
                desc = _try(lambda: w.describe(disc["src"], s_t, disc["src_schema"]), "Reading source columns")
                entry = {"name": pname or s_t,
                         "source": {"connection": disc["src"], "table": _join(disc["src_schema"], s_t)},
                         "target": {"connection": disc["tgt"], "table": _join(disc["tgt_schema"], t_t)},
                         "keys": desc.primary_keys if desc else []}
                skipped = w.add_tables([entry])
                st.session_state["flash"] = f"'{entry['name']}' already exists." if skipped else f"Added '{entry['name']}'."
                st.rerun()

    st.divider()
    st.subheader("Tables in the project")
    tables = w.data.get("tables", [])
    if not tables:
        st.info("No tables yet.")
        return
    st.dataframe(pd.DataFrame([{
        "name": t.get("name"), "source": f"{t.get('source', {}).get('connection')}:{t.get('source', {}).get('table') or '<query>'}",
        "target": f"{t.get('target', {}).get('connection')}:{t.get('target', {}).get('table') or '<query>'}",
        "keys": ", ".join(t.get("keys", [])) or "(detected)", "tags": ", ".join(t.get("tags", [])),
        "filters": " / ".join(x for x in (t.get("source", {}).get("filter"), t.get("target", {}).get("filter")) if x),
    } for t in tables]), width="stretch", hide_index=True)

    sel = st.selectbox("Edit table", w.table_names(), key="tbl_choice")
    if sel:
        _table_editor(sel)


def _columns_of(conn: str, ref: Dict[str, Any]) -> tuple:
    """(column names, primary keys) from discovery, or ([], []) if unavailable."""
    if ref.get("query") or not ref.get("table"):
        return [], []
    schema, table = _split(ref)
    try:
        d = ws().describe(conn, table, schema)
        return d.column_names, d.primary_keys
    except Exception as exc:  # noqa: BLE001
        st.caption(f"Columns of {conn}:{_join(schema, table)} unavailable ({exc.__class__.__name__}).")
        return [], []


def _side_editor(side: str, t: Dict[str, Any], kp: str) -> Dict[str, Any]:
    w = ws()
    ref = dict(t.get(side, {}))
    names = w.connection_names()
    conn = st.selectbox("Connection", names, index=names.index(ref["connection"]) if ref.get("connection") in names else 0,
                        key=f"{kp}_{side}_conn")
    schema, table = _split(ref)
    use_query = st.checkbox("Use a SELECT query instead of a table", value=bool(ref.get("query")), key=f"{kp}_{side}_useq")
    out: Dict[str, Any] = {"connection": conn}
    if use_query:
        q = st.text_area("Query", value=ref.get("query") or "", key=f"{kp}_{side}_query", height=100)
        if q.strip():
            out["query"] = q.strip()
    else:
        sc = st.text_input("Schema", value=schema or "", key=f"{kp}_{side}_schema")
        tb = st.text_input("Table", value=table, key=f"{kp}_{side}_table")
        if tb.strip():
            out["table"] = _join(sc.strip() or None, tb.strip())
    flt = st.text_input("Row / partition filter (SQL predicate)", value=ref.get("filter") or "", key=f"{kp}_{side}_filter",
                        help="Added as WHERE on every query for this side, e.g. a partition column condition.")
    if flt.strip():
        out["filter"] = flt.strip()
    bke = st.text_input("Bucket key expression (advanced)", value=ref.get("bucket_key_expr") or "",
                        key=f"{kp}_{side}_bke", help="Only when the key's text form differs between engines.")
    if bke.strip():
        out["bucket_key_expr"] = bke.strip()
    return out


def _table_editor(name: str) -> None:
    w = ws()
    i = w.table_index(name)
    t = copy.deepcopy(w.data["tables"][i])
    kp = f"tbl_{i}_{name}"
    new_name = st.text_input("Name", value=name, key=kp + "_name")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Source**")
        src = _side_editor("source", t, kp)
    with c2:
        st.markdown("**Target**")
        tgt = _side_editor("target", t, kp)

    s_cols, s_pk = _columns_of(src["connection"], src)
    t_cols, t_pk = _columns_of(tgt["connection"], tgt)
    detected = s_pk or t_pk
    key_opts = sorted(set(s_cols) | set(t.get("keys", [])))
    if key_opts:
        keys = st.multiselect("Key columns", key_opts, default=t.get("keys", []), key=kp + "_keys",
                              help="Empty = use the detected primary key.")
    else:
        keys = forms.csv_list(st.text_input("Key columns (comma separated)", value=", ".join(t.get("keys", [])),
                                            key=kp + "_keys_txt", help="Empty = use the detected primary key."))
    st.caption(f"Detected primary key: {', '.join(detected) if detected else 'none'}"
               + ("" if keys or detected else " · row-level comparison needs key columns"))

    st.markdown("**Column renames** (source column → target column; others match by name)")
    ren = pd.DataFrame([{"source column": k, "target column": v} for k, v in (t.get("columns") or {}).items()]
                       or [{"source column": None, "target column": None}])
    cfg = {}
    if s_cols:
        cfg["source column"] = st.column_config.SelectboxColumn(options=s_cols)
    if t_cols:
        cfg["target column"] = st.column_config.SelectboxColumn(options=t_cols)
    ren = st.data_editor(ren, key=kp + "_ren", num_rows="dynamic", width="stretch", column_config=cfg, hide_index=True)
    excl_opts = sorted(set(s_cols) | set(t.get("exclude_columns", [])))
    excl = (st.multiselect("Exclude columns", excl_opts, default=t.get("exclude_columns", []), key=kp + "_excl")
            if excl_opts else forms.csv_list(st.text_input("Exclude columns (comma separated)",
                                                           value=", ".join(t.get("exclude_columns", [])), key=kp + "_excl_txt")))
    tags = forms.csv_list(st.text_input("Tags (comma separated)", value=", ".join(t.get("tags", [])), key=kp + "_tags"))

    c1, c2 = st.columns([1, 5])
    if c1.button("Save table", type="primary", key=kp + "_save"):
        t.update({"name": new_name.strip(), "source": src, "target": tgt})
        for k, v in (("keys", keys), ("exclude_columns", excl), ("tags", tags),
                     ("columns", {r["source column"]: r["target column"] for r in ren.to_dict("records")
                                  if r.get("source column") and r.get("target column")})):
            if v:
                t[k] = v
            else:
                t.pop(k, None)
        if new_name.strip() != name and new_name.strip() in w.table_names():
            st.error(f"A table named '{new_name}' already exists.")
            return
        before = copy.deepcopy(w.data["tables"][i])
        w.data["tables"][i] = t
        errs = w.errors()
        if errs:
            w.data["tables"][i] = before
            st.error("\n".join(f"- {e}" for e in errs))
            return
        st.session_state["tbl_choice_request"] = t["name"]
        st.rerun()
    if c2.button("Remove table", key=kp + "_del"):
        del w.data["tables"][i]
        st.rerun()


# ============================================================================ 4. Checks & options
_SECTION_MODELS = {n: f.annotation for n, f in TableDefaults.model_fields.items()
                   if isinstance(f.annotation, type) and hasattr(f.annotation, "model_fields")}
_SCALARS = [n for n in TableDefaults.model_fields if n not in _SECTION_MODELS and n != "checks"]


def _options_form(current: Dict[str, Any], kp: str, column_options: List[str]) -> tuple:
    errors: List[str] = []
    out: Dict[str, Any] = {}
    out["checks"] = st.multiselect("Checks", list(ALL_CHECKS), default=current.get("checks", []), key=kp + "_checks",
                                   help="Which checks run. Profile-based checks share one scan per side.")
    for section, model in _SECTION_MODELS.items():
        with st.expander(forms.humanize(section), expanded=section == "compare"):
            vals, errs = forms.model_form(model, current.get(section, {}), f"{kp}_{section}",
                                          column_options=column_options or None)
            out[section] = vals
            errors += errs
    scal = {n: current.get(n) for n in _SCALARS}
    with st.expander("Other table options"):
        grid = st.columns(2)
        for j, n in enumerate(_SCALARS):
            f = TableDefaults.model_fields[n]
            with grid[j % 2]:
                if f.annotation is bool:
                    out[n] = st.checkbox(forms.humanize(n), value=bool(scal[n]), key=f"{kp}_{n}", help=f.description)
                elif column_options:
                    opts = sorted(set(column_options) | set(scal[n] or []))
                    out[n] = st.multiselect(forms.humanize(n), opts, default=list(scal[n] or []), key=f"{kp}_{n}")
                else:
                    out[n] = forms.csv_list(st.text_input(forms.humanize(n), value=", ".join(scal[n] or []), key=f"{kp}_{n}"))
    return out, errors


def _nested_diff(values: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in values.items():
        b = base.get(k)
        if isinstance(v, dict) and isinstance(b, dict) and k in _SECTION_MODELS:
            sub = {kk: vv for kk, vv in v.items() if b.get(kk) != vv}
            if sub:
                out[k] = sub
        elif v != b:
            out[k] = v
    return out


def checks_screen() -> None:
    w = ws()
    st.header("Checks & options")
    scopes = ["Project defaults"] + w.table_names()
    scope = st.selectbox("Apply to", scopes, key="opt_scope",
                         help="Project defaults apply to every table; a table's own settings override them.")
    model_base = TableDefaults().model_dump(mode="json")
    project_base = _deep_merge(model_base, w.data.get("defaults") or {})

    if scope == scopes[0]:
        vals, errors = _options_form(project_base, "opt_defaults", [])
        if st.button("Save project defaults", type="primary", key="opt_save_defaults"):
            if errors:
                st.error("\n".join(errors))
            else:
                diff = _nested_diff(vals, model_base)
                if diff:
                    w.data["defaults"] = diff
                else:
                    w.data.pop("defaults", None)
                _report_save(w)
        return

    i = w.table_index(scope)
    t = w.data["tables"][i]
    current = _deep_merge(project_base, {k: v for k, v in t.items() if k in model_base})
    s_cols, _ = _columns_of(t["source"]["connection"], t["source"]) if t.get("source") else ([], [])
    vals, errors = _options_form(current, f"opt_t{i}_{scope}", s_cols)

    st.subheader("Custom SQL checks")
    st.caption("Each runs on both sides; scalars or whole result sets are compared.")
    cc_fields = list(CustomCheck.model_fields)
    df = pd.DataFrame(t.get("custom_checks") or [], columns=cc_fields)
    col_cfg = {}
    for n, f in CustomCheck.model_fields.items():
        if typing.get_origin(f.annotation) is typing.Literal:
            col_cfg[n] = st.column_config.SelectboxColumn(options=list(typing.get_args(f.annotation)),
                                                          default=f.default, help=f.description)
        elif f.annotation is float:
            col_cfg[n] = st.column_config.NumberColumn(default=f.default, format="%g")
    cc_edit = st.data_editor(df, key=f"opt_cc_{i}_{scope}", num_rows="dynamic", width="stretch",
                             column_config=col_cfg, hide_index=True)

    c1, c2 = st.columns([1, 4])
    if c1.button("Save table options", type="primary", key=f"opt_save_{i}"):
        if errors:
            st.error("\n".join(errors))
            return
        before = copy.deepcopy(t)
        for k in model_base:
            t.pop(k, None)
        t.update(_nested_diff(vals, project_base))
        checks = []
        for r in cc_edit.to_dict("records"):
            r = {k: v for k, v in r.items() if v is not None and not (isinstance(v, float) and pd.isna(v))}
            if r.get("name") or r.get("source_sql") or r.get("target_sql"):
                checks.append(r)
        if checks:
            t["custom_checks"] = checks
        else:
            t.pop("custom_checks", None)
        errs = w.errors()
        if errs:
            w.data["tables"][i] = before
            st.error("\n".join(f"- {e}" for e in errs))
        else:
            _report_save(w)
    if c2.button("Reset to project defaults", key=f"opt_reset_{i}"):
        for k in model_base:
            t.pop(k, None)
        clear_widgets(f"opt_t{i}_")
        st.rerun()


def _report_save(w: Workspace) -> None:
    errs = w.errors()
    if errs:
        st.error("\n".join(f"- {e}" for e in errs))
    else:
        st.session_state["flash"] = "Saved."
        st.rerun()


# ============================================================================ 5. Review SQL
def review_screen() -> None:
    w = ws()
    st.header("Review SQL")
    st.caption("The exact statements a run sends to each side, for DBA approval. Building them runs "
               "only catalog queries.")
    names = w.table_names()
    if not names:
        st.info("Add tables first.")
        return
    sel = st.selectbox("Table", names, key="review_table")
    if not st.button("Show SQL", type="primary", key="review_btn"):
        return
    with st.spinner("Reading catalogs..."):
        ex = _try(lambda: w.explain(sel), "Building SQL")
    if ex is None:
        return
    chk = ex.schema_check
    st.markdown(f"Schema check: {badge(chk.status.value)} {chk.message}")
    cols = chk.details.get("columns") or []
    if cols:
        st.dataframe(pd.DataFrame(cols), width="stretch", hide_index=True)
    for side in ex.sides:
        st.subheader(f"{side.role.capitalize()}: {side.connection} ({side.connection_type}, {side.dialect} SQL)")
        if side.billing_note:
            msg = side.billing_note + (" A filter is set for this side." if side.has_filter
                                       else " No filter is set for this side.")
            (st.info if side.has_filter else st.warning)(msg)
        st.markdown("Table profile (one scan)")
        st.code(side.profile_sql + ";", language="sql")
        if side.bucket_sql:
            st.markdown(f"Bucket checksums (level {ex.bucket_digits}, profile mode)")
            st.code(side.bucket_sql + ";", language="sql")
            st.markdown("Row fetch, shown for buckets 'a0' and 'a1'")
            st.code(side.fetch_sql + ";", language="sql")
        else:
            st.caption("No key columns: row-level comparison is skipped for this table.")


# ============================================================================ 6. Run
def run_screen() -> None:
    w = ws()
    st.header("Run")
    errs = w.errors()
    if errs:
        st.error("Fix the project first:\n\n" + "\n".join(f"- {e}" for e in errs))
        return
    if not w.table_names():
        st.info("Add tables first.")
        return

    job = st.session_state.get("job")
    running = job is not None and not job.done
    if not running:
        tags = sorted({tag for t in w.data["tables"] for tag in t.get("tags", [])})
        c1, c2 = st.columns(2)
        sel_tables = c1.multiselect("Tables (empty = all)", w.table_names(), key="run_tables")
        sel_tags = c2.multiselect("Only tables with tags", tags, key="run_tags") if tags else []
        modes = ["(each table's setting)"] + list(typing.get_args(CompareOptions.model_fields["mode"].annotation))
        c1, c2, c3 = st.columns(3)
        mode = c1.selectbox("Row comparison mode", modes, key="run_mode")
        parallel = int(c2.number_input("Tables in parallel", min_value=1, step=1,
                                       value=int(w.data.get("parallel_tables") or 1), key="run_parallel"))
        formats = c3.multiselect("Report formats", list(REPORT_FORMATS), default=list(REPORT_FORMATS), key="run_formats")
        if st.button("Start validation", type="primary", key="run_start"):
            try:
                st.session_state["job"] = w.start_run(sel_tables or None, sel_tags or None,
                                                      None if mode == modes[0] else mode, parallel, formats)
                st.session_state["job_seen_done"] = False
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error("Could not start: " + "; ".join(format_validation_error(exc)))
    if job is not None:
        _job_panel()


@st.fragment(run_every="1s")
def _job_panel() -> None:
    job = st.session_state.get("job")
    if job is None:
        return
    snap = job.snapshot()
    done = snap.state == "done"
    head = "Finished" if done else ("Cancelling..." if snap.cancel_requested else "Running")
    st.markdown(f"**{head}** · {snap.elapsed_s}s"
                + (f" · run {snap.result.run_id} {badge(snap.result.status.value)}" if snap.result else ""))
    n_done = sum(1 for t in snap.tables if t.state == "done")
    st.progress(n_done / max(1, len(snap.tables)), text=f"{n_done} of {len(snap.tables)} tables done")
    st.dataframe(pd.DataFrame([{"table": t.name, "state": t.state, "status": t.status or "",
                                "latest": t.message} for t in snap.tables]), width="stretch", hide_index=True)
    with st.expander("Log"):
        st.code("\n".join(snap.log[-300:]) or "(empty)")
    if not done:
        if st.button("Cancel run", key="run_cancel", disabled=snap.cancel_requested,
                     help="Stops at the next step; a query already sent to an engine finishes first."):
            job.cancel()
        return
    if snap.error:
        st.error(f"Run failed: {snap.error}")
    if snap.result is not None:
        if st.button("Open results", type="primary", key="run_open"):
            st.session_state["result_run"] = snap.result
            go("Results")
    if not st.session_state.get("job_seen_done"):
        st.session_state["job_seen_done"] = True
        st.rerun(scope="app")


# ============================================================================ 7. Results
def _render_run(run: RunResult) -> None:
    w = ws()
    st.markdown(f"Run **{run.run_id}** · {run.project} · started {run.started_at} · {run.duration_s}s · "
                f"overall {badge(run.status.value)}" + (" · cancelled" if run.cancelled else ""))
    counts = run.counts
    cols = st.columns(len(counts))
    for c, (k, v) in zip(cols, counts.items(), strict=True):
        c.metric(k, v)
    st.dataframe(pd.DataFrame([{
        "table": t.name, "status": t.status.value, "source": t.source, "target": t.target,
        "source rows": t.stats.get("source_rows"), "target rows": t.stats.get("target_rows"),
        "differences": len(t.discrepancies), "duration (s)": t.duration_s, "error": t.error or "",
    } for t in run.tables]), width="stretch", hide_index=True)

    names = [t.name for t in run.tables]
    if names:
        sel = st.selectbox("Table details", names, key=f"res_tbl_{run.run_id}")
        t = next(x for x in run.tables if x.name == sel)
        st.markdown(f"{badge(t.status.value)} **{t.name}** · {t.source} → {t.target}")
        st.dataframe(pd.DataFrame([{
            "check": c.check, "name": c.name, "status": c.status.value, "message": c.message,
            "source": _cell(c.source_value), "target": _cell(c.target_value), "seconds": c.duration_s,
        } for c in t.checks]), width="stretch", hide_index=True)
        for c in t.checks:
            if c.details:
                with st.expander(f"{c.name}: details"):
                    st.json(c.details, expanded=False)

    st.subheader("Discrepancies")
    rows = [{"table": t.name, "kind": d.kind, "key": d.key, "column": d.column or "",
             "source value": d.source_value, "target value": d.target_value}
            for t in run.tables for d in t.discrepancies]
    if not rows:
        st.caption("No differing rows were sampled.")
    else:
        df = pd.DataFrame(rows)
        c1, c2, c3, c4 = st.columns(4)
        f_tbl = c1.multiselect("Table", sorted(df["table"].unique()), key=f"df_t_{run.run_id}")
        f_kind = c2.multiselect("Kind", sorted(df["kind"].unique()), key=f"df_k_{run.run_id}")
        f_col = c3.multiselect("Column", sorted(x for x in df["column"].unique() if x), key=f"df_c_{run.run_id}")
        f_key = c4.text_input("Key contains", key=f"df_key_{run.run_id}")
        if f_tbl:
            df = df[df["table"].isin(f_tbl)]
        if f_kind:
            df = df[df["kind"].isin(f_kind)]
        if f_col:
            df = df[df["column"].isin(f_col)]
        if f_key:
            df = df[df["key"].astype(str).str.contains(f_key, regex=False)]
        st.caption(f"{len(df)} of {len(rows)} sampled differences")
        st.dataframe(df, width="stretch", hide_index=True)

    st.subheader("Reports")
    files = w.run_files(run.run_id)
    if not files:
        st.caption(f"No report files found under {os.path.join(w.output_dir, run.run_id)}.")
    cols = st.columns(3)
    for j, (fname, path) in enumerate(files.items()):
        with open(path, "rb") as fh:
            cols[j % 3].download_button(fname, fh.read(), file_name=fname, key=f"dl_{run.run_id}_{fname}")


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return str(v)


def results_screen() -> None:
    st.header("Results")
    run = st.session_state.get("result_run")
    job = st.session_state.get("job")
    if run is None and job is not None and job.result is not None:
        run = job.result
    if run is None:
        st.info("No run selected. Start one on the Run screen or open one from History.")
        return
    _render_run(run)


# ============================================================================ 8. History
def history_screen() -> None:
    w = ws()
    st.header("History")
    st.caption(f"Runs recorded in {os.path.join(w.output_dir, 'history.jsonl')}")
    runs = w.history()
    if not runs:
        st.info("No runs recorded yet.")
        return
    st.dataframe(pd.DataFrame([{
        "run id": r.get("run_id"), "project": r.get("project"), "started": r.get("started_at"),
        "status": r.get("status"), "seconds": r.get("duration_s"),
        "tables": ", ".join(f"{k}={v}" for k, v in (r.get("tables") or {}).items()),
    } for r in runs]), width="stretch", hide_index=True)
    ids = [r.get("run_id") for r in runs if r.get("run_id")]
    sel = st.selectbox("Run", ids, key="hist_sel")
    if st.button("Open results", type="primary", key="hist_open"):
        run = _try(lambda: w.load_run(sel), "Opening run")
        if run is not None:
            st.session_state["result_run"] = run
            go("Results")


SCREENS = {
    "Project": project_screen,
    "Connections": connections_screen,
    "Tables": tables_screen,
    "Checks & options": checks_screen,
    "Review SQL": review_screen,
    "Run": run_screen,
    "Results": results_screen,
    "History": history_screen,
}
