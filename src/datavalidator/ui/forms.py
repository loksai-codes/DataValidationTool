"""
Widgets generated from metadata: connection forms from :mod:`datavalidator.ui_schema`, option
forms from the Pydantic models in :mod:`datavalidator.config`. Nothing engine-specific lives here.
"""

from __future__ import annotations

import typing
from typing import Any, Dict, List, Optional, Sequence, Type

import pandas as pd
import streamlit as st
import yaml
from pydantic import BaseModel

from datavalidator.config import FileTable
from datavalidator.ui_schema import GROUP_LABELS, FieldSpec, field_default, fields_for, is_reference


def humanize(name: str) -> str:
    return name.rstrip("_").replace("_", " ").capitalize()


def _yaml_text(value: Any) -> str:
    if value in (None, {}, []):
        return ""
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, default_flow_style=False).strip()


def _parse_yaml(text: str, label: str, errors: List[str]) -> Any:
    if not text.strip():
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        errors.append(f"{label}: invalid YAML ({exc.__class__.__name__})")
        return None


# ----------------------------------------------------------------------------- connection form
def _field_widget(f: FieldSpec, value: Any, key: str, errors: List[str]) -> Any:
    default = field_default(f.name)
    label = f.label + (" *" if f.required else "")
    help_ = f.help or None
    current = value if value is not None else default

    if f.kind == "int":
        v = st.number_input(label, value=int(current) if current not in (None, "") else None, step=1,
                            key=key, help=help_, placeholder=f.placeholder or None)
        return int(v) if v is not None else None
    if f.kind == "bool":
        return st.checkbox(label, value=bool(current), key=key, help=help_)
    if f.kind == "secret":
        v = st.text_input(label, value=current or "", type="password", key=key, help=help_,
                          placeholder=f.placeholder or "value or ${env:VAR}")
        if v:
            st.caption("Reference, resolved at run time." if is_reference(v)
                       else "Typed value: kept in this session only. Exported YAML will use ${env:...}.")
        return v
    if f.kind == "list":
        text = st.text_area(label, value="\n".join(current or []), key=key, help=help_, height=80)
        return [line.strip() for line in text.splitlines() if line.strip()]
    if f.kind == "mapping":
        parsed = _parse_yaml(st.text_area(label, value=_yaml_text(current), key=key, help=help_, height=80),
                             f.label, errors)
        if parsed is not None and not isinstance(parsed, dict):
            errors.append(f"{f.label}: expected key: value lines")
            return None
        return {str(k): str(v) for k, v in (parsed or {}).items()}
    if f.kind == "ssl":
        modes = ["off", "on", "options"]
        mode = "options" if isinstance(current, dict) else ("on" if current else "off")
        choice = st.radio(label, modes, index=modes.index(mode), key=key, help=help_, horizontal=True)
        if choice == "on":
            return True
        if choice == "options":
            parsed = _parse_yaml(st.text_area("TLS options (YAML)", value=_yaml_text(current if isinstance(current, dict) else {}),
                                              key=key + "_opts", height=80), f.label, errors)
            return parsed if isinstance(parsed, dict) and parsed else True
        return None
    if f.kind == "file_tables":
        return _file_tables_widget(label, current or {}, key, help_, errors)
    v = st.text_input(label, value="" if current is None else str(current), key=key, help=help_,
                      placeholder=f.placeholder or None)
    return v.strip() or None


def _file_tables_widget(label: str, current: Dict[str, Any], key: str, help_: Optional[str],
                        errors: List[str]) -> Dict[str, Any]:
    formats = list(typing.get_args(FileTable.model_fields["format"].annotation))
    default_format = FileTable.model_fields["format"].default
    rows = []
    for name, t in current.items():
        t = t if isinstance(t, dict) else {}
        paths = t.get("path", "")
        rows.append({"table": name, "paths": "\n".join(paths) if isinstance(paths, list) else paths,
                     "format": t.get("format", default_format), "options": _yaml_text(t.get("options"))})
    df = pd.DataFrame(rows or [{"table": "", "paths": "", "format": default_format, "options": ""}])
    st.markdown(f"**{label}**")
    if help_:
        st.caption(help_)
    edited = st.data_editor(
        df, key=key, num_rows="dynamic", use_container_width=True,
        column_config={
            "table": st.column_config.TextColumn("Table name"),
            "paths": st.column_config.TextColumn("Path(s), one per line or comma separated"),
            "format": st.column_config.SelectboxColumn("Format", options=formats),
            "options": st.column_config.TextColumn("Reader options (YAML)"),
        })
    out: Dict[str, Any] = {}
    for r in edited.to_dict("records"):
        name = str(r.get("table") or "").strip()
        if not name:
            continue
        paths = [p.strip() for p in str(r.get("paths") or "").replace(",", "\n").splitlines() if p.strip()]
        entry: Dict[str, Any] = {"path": paths[0] if len(paths) == 1 else paths}
        if r.get("format") and r["format"] != default_format:
            entry["format"] = r["format"]
        opts = _parse_yaml(str(r.get("options") or ""), f"{name} options", errors)
        if opts:
            entry["options"] = opts
        out[name] = entry
    return out


def connection_form(conn_type: str, values: Dict[str, Any], key_prefix: str) -> tuple:
    """Render the fields of one connection type. Returns (values to store, errors).

    Values equal to the model default are left out so exported YAML stays minimal.
    """
    errors: List[str] = []
    out: Dict[str, Any] = {"type": conn_type}
    fields = fields_for(conn_type)
    for group, glabel in GROUP_LABELS.items():
        group_fields = [f for f in fields if f.group == group]
        if not group_fields:
            continue
        with st.expander(glabel, expanded=group != "behaviour"):
            cols = st.columns(2)
            for i, f in enumerate(group_fields):
                wide = f.kind in ("file_tables", "list", "mapping", "ssl")
                holder = st.container() if wide else cols[i % 2]
                with holder:
                    v = _field_widget(f, values.get(f.key), f"{key_prefix}_{f.name}", errors)
                if v in (None, "", [], {}) or (v == field_default(f.name) and not f.required):
                    continue
                out[f.key] = v
    # keep keys this form does not know about (extra settings written by hand in YAML)
    known = {f.key for f in fields} | {"type"}
    for k, v in values.items():
        if k not in known:
            out[k] = v
    return out, errors


def mask_value(key: str, value: Any, secret: bool) -> str:
    if value in (None, ""):
        return ""
    if secret and not is_reference(value):
        return "********"
    return str(value)


# ----------------------------------------------------------------------------- model forms
def _annotation(model: Type[BaseModel], name: str):
    ann = model.model_fields[name].annotation
    origin = typing.get_origin(ann)
    if origin is typing.Union:
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        ann = args[0] if len(args) == 1 else ann
    return ann


def model_form(model: Type[BaseModel], current: Dict[str, Any], key_prefix: str,
               column_options: Optional[Sequence[str]] = None, columns: int = 2) -> tuple:
    """One widget per model field, initialised from ``current`` (already merged with defaults).

    Returns (values for every field, errors).
    """
    errors: List[str] = []
    out: Dict[str, Any] = {}
    grid = st.columns(columns)
    for i, (name, f) in enumerate(model.model_fields.items()):
        ann = _annotation(model, name)
        origin = typing.get_origin(ann)
        val = current.get(name, f.get_default(call_default_factory=True))
        label, help_, key = humanize(name), f.description, f"{key_prefix}_{name}"
        with grid[i % columns]:
            if origin is typing.Literal:
                opts = list(typing.get_args(ann))
                out[name] = st.selectbox(label, opts, index=opts.index(val) if val in opts else 0, key=key, help=help_)
            elif ann is bool:
                out[name] = st.checkbox(label, value=bool(val), key=key, help=help_)
            elif ann is int:
                out[name] = int(st.number_input(label, value=int(val), step=1, key=key, help=help_))
            elif ann is float:
                out[name] = float(st.number_input(label, value=float(val), key=key, help=help_, format="%g",
                                                  step=None))
            elif origin in (list, List):
                if column_options:
                    opts = sorted(set(column_options) | set(val or []))
                    out[name] = st.multiselect(label, opts, default=list(val or []), key=key, help=help_)
                else:
                    text = st.text_input(label, value=", ".join(val or []), key=key, help=help_)
                    out[name] = [x.strip() for x in text.split(",") if x.strip()]
            elif origin in (dict, Dict):
                parsed = _parse_yaml(st.text_area(label, value=_yaml_text(val), key=key, help=help_, height=80),
                                     label, errors)
                out[name] = parsed if isinstance(parsed, dict) else {}
            else:
                text = st.text_input(label, value="" if val is None else str(val), key=key, help=help_)
                out[name] = text or None
    return out, errors


def diff_from(values: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Keys of ``values`` that differ from ``baseline``."""
    return {k: v for k, v in values.items() if baseline.get(k) != v}


def model_defaults(model: Type[BaseModel]) -> Dict[str, Any]:
    return model().model_dump(mode="json")


def csv_list(text: str) -> List[str]:
    return [x.strip() for x in str(text or "").split(",") if x.strip()]
