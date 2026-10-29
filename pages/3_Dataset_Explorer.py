"""
Page 3: Dataset Explorer
Discovers databases, schemas, and tables across source and destination systems.
Provides interactive column mapping, business key selection (single or composite),
and schema compatibility diagnostics.
"""

import pandas as pd
import streamlit as st

from migration_validator.services.schema_discovery import SchemaDiscoveryService
from migration_validator.ui_common import (
    apply_theme,
    get_connectors,
    init_session_state,
    render_sidebar_status,
)

st.set_page_config(page_title="Dataset Explorer | Migration Validator", page_icon="🔍", layout="wide")
apply_theme()
init_session_state()
render_sidebar_status()

st.title("🔍 Dataset Discovery & Column Mapping")
st.caption("Inspect table structures, map fields, and select primary/composite business keys.")

athena_conn, pg_conn = get_connectors()
discovery_service = SchemaDiscoveryService(athena_conn, pg_conn)

# 1. Table Selection & Discovery
col_sel1, col_sel2 = st.columns(2)

with col_sel1:
    st.subheader("Source Selection (Athena / S3)")
    src_databases = discovery_service.discover_source_schemas()
    selected_src_db = st.selectbox("Source Database", options=src_databases, index=0)

    src_tables = discovery_service.discover_source_tables(selected_src_db)
    selected_src_table = st.selectbox(
        "Source Table",
        options=src_tables,
        index=src_tables.index("customers") if "customers" in src_tables else 0
    )

with col_sel2:
    st.subheader("Destination Selection (PostgreSQL)")
    dst_schemas = discovery_service.discover_dest_schemas()
    default_schema_idx = dst_schemas.index("public") if "public" in dst_schemas else 0
    selected_dst_schema = st.selectbox("Destination Schema", options=dst_schemas, index=default_schema_idx)

    dst_tables = discovery_service.discover_dest_tables(selected_dst_schema)
    default_table_idx = dst_tables.index("customers") if "customers" in dst_tables else 0
    selected_dst_table = st.selectbox("Destination Table", options=dst_tables, index=default_table_idx)

# Refresh button
if st.button("🔄 Refresh Schema Information"):
    st.rerun()

st.divider()

# 2. Schema Inspection & Mapping
try:
    with st.spinner("Inspecting schemas..."):
        src_schema = discovery_service.get_source_schema(selected_src_table, schema_or_db=selected_src_db)
        dst_schema = discovery_service.get_dest_schema(selected_dst_table, schema=selected_dst_schema)

    st.subheader("Column Schemas")
    c1, c2 = st.columns(2)

    with c1:
        st.write(f"**Source Columns ({src_schema.full_name})**")
        src_df = pd.DataFrame([
            {
                "Column": col.name,
                "Data Type": col.data_type,
                "Nullable": col.is_nullable
            }
            for col in src_schema.columns.values()
        ])
        st.dataframe(src_df, use_container_width=True, hide_index=True)

    with c2:
        st.write(f"**Destination Columns ({dst_schema.full_name})**")
        dst_df = pd.DataFrame([
            {
                "Column": col.name,
                "Data Type": col.data_type,
                "Nullable": col.is_nullable
            }
            for col in dst_schema.columns.values()
        ])
        st.dataframe(dst_df, use_container_width=True, hide_index=True)

    st.divider()

    # 3. Column Mapping Configuration
    st.subheader("Field Mappings & Key Configuration")

    default_mapping = discovery_service.generate_default_mapping(src_schema, dst_schema)
    dst_col_options = ["<IGNORE / DO NOT MAP>"] + list(dst_schema.columns.keys())

    mapping_results = {}
    st.write("Configure how each source field maps to destination fields:")

    cols_grid = st.columns(2)
    for idx, (s_col, s_meta) in enumerate(src_schema.columns.items()):
        grid_target = cols_grid[idx % 2]
        with grid_target:
            def_dst = default_mapping.get(s_col, "<IGNORE / DO NOT MAP>")
            def_idx = dst_col_options.index(def_dst) if def_dst in dst_col_options else 0
            chosen_dst = st.selectbox(
                f"Source: **{s_col}** ({s_meta.data_type})",
                options=dst_col_options,
                index=def_idx,
                key=f"map_{s_col}"
            )
            if chosen_dst != "<IGNORE / DO NOT MAP>":
                mapping_results[s_col] = chosen_dst

    # 4. Business Key Selection
    st.write("")
    st.write("#### Select Business Key(s)")
    all_mapped_src_cols = list(mapping_results.keys())
    default_keys = ["customer_id"] if "customer_id" in all_mapped_src_cols else all_mapped_src_cols[:1]

    selected_keys = st.multiselect(
        "Business Primary / Composite Key",
        options=all_mapped_src_cols,
        default=default_keys,
        help="Unique identifier used for keyset pagination and record-level SHA-256 fingerprint matching."
    )

    if not selected_keys:
        st.error("At least one business key must be configured for record-level validation.")

    # Save mapping to session state
    st.session_state["active_mapping"] = mapping_results
    st.session_state["active_keys"] = selected_keys
    st.session_state["active_src_table"] = selected_src_table
    st.session_state["active_src_db"] = selected_src_db
    st.session_state["active_dst_table"] = selected_dst_table
    st.session_state["active_dst_schema"] = selected_dst_schema

    # 5. Schema Comparison Summary
    if selected_keys and mapping_results:
        comp_res = discovery_service.compare_schemas(
            source_schema=src_schema,
            dest_schema=dst_schema,
            column_mapping=mapping_results,
            key_columns=selected_keys
        )

        st.subheader("Compatibility Analysis")
        if comp_res.is_compatible and not comp_res.warnings:
            st.success("✅ Schemas are fully compatible! All mapped columns match valid type semantics.")
        elif comp_res.is_compatible and comp_res.warnings:
            st.warning("⚠️ Schemas are structurally compatible with warnings:")
            for w in comp_res.warnings:
                st.write(f"- {w}")
        else:
            st.error("❌ Schema incompatibilities detected:")
            for m in comp_res.missing_in_dest:
                st.write(f"- Missing in destination: `{m}`")
            for t in comp_res.type_mismatches:
                st.write(f"- Type mismatch: {t}")

except Exception as e:
    st.error(f"Error inspecting schemas: {e}")
finally:
    athena_conn.close()
    pg_conn.close()
