"""Streamlit Page: Compare Tables Configuration."""
import json
import streamlit as st

st.set_page_config(page_title="Compare Tables — Validator", page_icon="⚖️", layout="wide")

from data_validator.app_services.connections import ConnectionService
from data_validator.app_services.metadata import MetadataService
from data_validator.app_services.run_service import RunService
from data_validator.config.models import (
    ComparisonConfig,
    ExecutionConfig,
    NormalizationConfig,
    PartitionConfig,
    PartitionStrategy,
    ReportingConfig,
    TableEndpointConfig,
    ValidationJobConfig,
)
from data_validator.config.validators import preflight_validate_job
from data_validator.connectors.registry import is_connector_available

conn_service = ConnectionService()
meta_service = MetadataService()
run_service = RunService()

st.title("⚖️ Compare Tables Setup")
st.caption("Configure source and target endpoints independently, discover table schemas, and define validation rules.")

# Top action bar: Load from JSON or Demo Pre-fill
top_col1, top_col2, top_col3 = st.columns([2, 1, 1])
with top_col2:
    if st.button("🧪 Pre-fill Synthetic Demo", use_container_width=True):
        st.session_state["src_prov"] = "Other / Self-Hosted"
        st.session_state["src_eng"] = "Synthetic (In-Memory/Demo)"
        st.session_state["src_conn_id"] = "synthetic_src"
        st.session_state["tgt_prov"] = "Other / Self-Hosted"
        st.session_state["tgt_eng"] = "Synthetic (In-Memory/Demo)"
        st.session_state["tgt_conn_id"] = "synthetic_tgt"
        st.session_state["src_db"] = "default_db"
        st.session_state["tgt_db"] = "default_db"
        st.session_state["src_tbl"] = "customers"
        st.session_state["tgt_tbl"] = "customers"
        st.session_state["keys"] = ["customer_id"]
        st.rerun()

with top_col3:
    if st.button("🚀 Load Athena → Postgres Example", use_container_width=True):
        st.session_state["src_prov"] = "AWS"
        st.session_state["src_eng"] = "Athena"
        st.session_state["src_conn_id"] = "aws_analytics"
        st.session_state["tgt_prov"] = "AWS"
        st.session_state["tgt_eng"] = "PostgreSQL"
        st.session_state["tgt_conn_id"] = "postgres_dev"
        st.session_state["src_db"] = "migration_validator"
        st.session_state["tgt_db"] = "migration_validator"
        st.session_state["src_tbl"] = "customers"
        st.session_state["tgt_tbl"] = "customers"
        st.session_state["keys"] = ["customer_id"]
        st.rerun()

st.markdown("---")

col_src, col_tgt = st.columns(2)

def get_engine_options(provider_str: str) -> list[str]:
    if "AWS" in provider_str:
        return ["Athena", "PostgreSQL", "MySQL (Planned)"]
    elif "Other" in provider_str:
        return ["PostgreSQL", "Synthetic (In-Memory/Demo)", "MySQL (Planned)", "MSSQL (Planned)"]
    elif "Azure" in provider_str:
        return ["MSSQL (Planned)", "PostgreSQL"]
    elif "GCP" in provider_str:
        return ["PostgreSQL"]
    else:
        return ["MaxCompute (Planned)"]

def to_prov_key(s: str) -> str:
    if "AWS" in s: return "aws"
    if "Azure" in s: return "azure"
    if "GCP" in s: return "gcp"
    if "Alibaba" in s: return "alibaba"
    return "other"

def to_eng_key(s: str) -> str:
    if "Athena" in s: return "athena"
    if "PostgreSQL" in s: return "postgresql"
    if "Synthetic" in s: return "synthetic"
    if "MySQL" in s: return "mysql"
    if "MSSQL" in s: return "mssql"
    return "maxcompute"

# --- Source Selection ---
with col_src:
    st.subheader("🔵 Source Endpoint")
    s_prov = st.selectbox("Source Provider", ["AWS", "Other / Self-Hosted", "Azure", "GCP", "Alibaba"], index=0, key="src_prov")
    s_eng_choices = get_engine_options(s_prov)
    s_eng = st.selectbox("Source Engine", s_eng_choices, index=0, key="src_eng")
    
    s_prov_k = to_prov_key(s_prov)
    s_eng_k = to_eng_key(s_eng)
    
    if not is_connector_available(s_prov_k, s_eng_k):
        st.error(f"Connector for {s_prov} / {s_eng} is Planned/Unavailable. Only implemented connectors can be run.")
    
    s_conns = [cid for cid, cfg in conn_service.list_connections().items() if cfg.get("engine") == s_eng_k]
    if not s_conns:
        s_conns = ["aws_analytics" if s_eng_k == "athena" else "postgres_dev" if s_eng_k == "postgresql" else "synthetic_src"]
    
    s_conn_id = st.selectbox("Source Named Connection", s_conns, key="src_conn_id")
    
    s_btn_c1, s_btn_c2 = st.columns([1, 1])
    with s_btn_c1:
        if st.button("Test Source Connection", key="btn_test_src"):
            with st.spinner("Testing..."):
                t_res = conn_service.test_connection(s_prov_k, s_eng_k, s_conn_id)
                if t_res.success:
                    st.success(f"Connected! {t_res.message}")
                else:
                    st.error(f"Failed: {t_res.message}")
    with s_btn_c2:
        if st.button("🔄 Refresh Metadata", key="btn_ref_src"):
            meta_service.clear_cache(s_conn_id)
            st.toast("Source metadata cache cleared.")

    # Metadata discovery
    s_dbs = []
    try:
        s_dbs = meta_service.list_databases(s_prov_k, s_eng_k, s_conn_id)
    except Exception:
        s_dbs = ["migration_validator", "default"]
    if not s_dbs:
        s_dbs = ["migration_validator", "default"]
        
    s_db = st.selectbox("Database / Catalog", s_dbs, key="src_db")
    s_sch = st.text_input("Schema (optional)", value="" if s_eng_k == "athena" else "public", key="src_sch")
    
    s_tables = []
    try:
        s_tables = meta_service.list_tables(s_prov_k, s_eng_k, s_conn_id, database=s_db, schema=s_sch or None)
    except Exception:
        s_tables = ["customers", "orders", "transactions"]
    if not s_tables:
        s_tables = ["customers", "orders", "transactions"]
        
    s_table = st.selectbox("Table", s_tables, key="src_tbl")

# --- Target Selection ---
with col_tgt:
    st.subheader("🟢 Target Endpoint")
    t_prov = st.selectbox("Target Provider", ["AWS", "Other / Self-Hosted", "Azure", "GCP", "Alibaba"], index=0, key="tgt_prov")
    t_eng_choices = get_engine_options(t_prov)
    t_eng = st.selectbox("Target Engine", t_eng_choices, index=1 if len(t_eng_choices) > 1 else 0, key="tgt_eng")
    
    t_prov_k = to_prov_key(t_prov)
    t_eng_k = to_eng_key(t_eng)
    
    if not is_connector_available(t_prov_k, t_eng_k):
        st.error(f"Connector for {t_prov} / {t_eng} is Planned/Unavailable. Only implemented connectors can be run.")
        
    t_conns = [cid for cid, cfg in conn_service.list_connections().items() if cfg.get("engine") == t_eng_k]
    if not t_conns:
        t_conns = ["postgres_dev" if t_eng_k == "postgresql" else "aws_analytics" if t_eng_k == "athena" else "synthetic_tgt"]
        
    t_conn_id = st.selectbox("Target Named Connection", t_conns, key="tgt_conn_id")
    
    t_btn_c1, t_btn_c2 = st.columns([1, 1])
    with t_btn_c1:
        if st.button("Test Target Connection", key="btn_test_tgt"):
            with st.spinner("Testing..."):
                t_res = conn_service.test_connection(t_prov_k, t_eng_k, t_conn_id)
                if t_res.success:
                    st.success(f"Connected! {t_res.message}")
                else:
                    st.error(f"Failed: {t_res.message}")
    with t_btn_c2:
        if st.button("🔄 Refresh Metadata", key="btn_ref_tgt"):
            meta_service.clear_cache(t_conn_id)
            st.toast("Target metadata cache cleared.")

    t_dbs = []
    try:
        t_dbs = meta_service.list_databases(t_prov_k, t_eng_k, t_conn_id)
    except Exception:
        t_dbs = ["migration_validator", "postgres"]
    if not t_dbs:
        t_dbs = ["migration_validator", "postgres"]
        
    t_db = st.selectbox("Database", t_dbs, key="tgt_db")
    t_sch = st.text_input("Schema", value="public", key="tgt_sch")
    
    t_tables = []
    try:
        t_tables = meta_service.list_tables(t_prov_k, t_eng_k, t_conn_id, database=t_db, schema=t_sch or None)
    except Exception:
        t_tables = ["customers", "orders", "transactions"]
    if not t_tables:
        t_tables = ["customers", "orders", "transactions"]
        
    t_table = st.selectbox("Table", t_tables, key="tgt_tbl")

st.markdown("---")

# --- Validation Options ---
st.subheader("⚙️ Validation Rules & Checks")

# Retrieve column list for selectors
col_candidates = ["customer_id", "id", "email", "created_at", "status", "amount", "name", "category"]
try:
    s_meta = meta_service.get_table_metadata(s_prov_k, s_eng_k, s_conn_id, s_table, database=s_db, schema=s_sch or None)
    if s_meta and s_meta.columns:
        col_candidates = [c.name for c in s_meta.columns]
except Exception:
    pass

opt_col1, opt_col2 = st.columns(2)

with opt_col1:
    job_name = st.text_input("Job Name", value=f"{s_eng_k}_to_{t_eng_k}_{s_table}")
    unique_keys = st.multiselect(
        "Unique Key Column(s)",
        col_candidates,
        default=[col_candidates[0]] if col_candidates else [],
        help="Primary key or composite unique key used for record-by-record reconciliation.",
    )
    ignored_columns = st.multiselect(
        "Ignored Columns (optional)",
        [c for c in col_candidates if c not in unique_keys],
        help="Columns to exclude from data-level comparison.",
    )

    st.markdown("**Enabled Checks**")
    c_schema = st.checkbox("Schema & Data Type Compatibility", value=True)
    c_counts = st.checkbox("Row Count Comparison", value=True)
    c_dup_keys = st.checkbox("Duplicate Key Detection", value=True)
    c_nulls = st.checkbox("NULL Value Checks", value=True)
    c_data_level = st.checkbox("Data-Level Actual Value Comparison", value=True)
    c_aggregates = st.checkbox("Aggregates / Metrics Comparison", value=False)

with opt_col2:
    st.markdown("**Partitioning Strategy**")
    part_strat = st.selectbox(
        "Partition Strategy",
        ["Key Range (Numeric)", "Date Range", "Keyset Pagination", "Bounded Full Scan (Single)"],
        index=0,
    )
    strat_key = (
        PartitionStrategy.KEY_RANGE if "Key Range" in part_strat
        else PartitionStrategy.DATE_RANGE if "Date Range" in part_strat
        else PartitionStrategy.KEYSET_PAGINATION if "Keyset" in part_strat
        else PartitionStrategy.BOUNDED_FULL_SCAN
    )
    
    part_column = st.selectbox(
        "Partition Column",
        col_candidates,
        index=0 if col_candidates else 0,
        disabled=(strat_key == PartitionStrategy.BOUNDED_FULL_SCAN),
    )
    range_size = st.number_input("Partition Chunk Size (Rows/Range)", value=25000, step=5000)
    incl_null = st.checkbox("Include NULL Partition for unbounded values", value=True)

    st.markdown("**Normalization Rules**")
    norm_trim = st.checkbox("Trim String Whitespace", value=False)
    norm_case = st.checkbox("Case-Sensitive String Comparison", value=True)
    norm_dec_tol = st.text_input("Decimal Tolerance Threshold", value="0")
    norm_tz = st.selectbox("Normalize Timestamps to Timezone", ["UTC", "Local"], index=0)

st.markdown("---")

# Execution & Launch
exec_c1, exec_c2, exec_c3 = st.columns([1, 1, 2])

job_config = ValidationJobConfig(
    config_version=1,
    job_name=job_name.strip() or "validation_job",
    source=TableEndpointConfig(
        provider=s_prov_k,  # type: ignore
        engine=s_eng_k,      # type: ignore
        connection_id=s_conn_id,
        database=s_db,
        schema=s_sch or None,
        table=s_table,
    ),
    target=TableEndpointConfig(
        provider=t_prov_k,  # type: ignore
        engine=t_eng_k,      # type: ignore
        connection_id=t_conn_id,
        database=t_db,
        schema=t_sch or None,
        table=t_table,
    ),
    comparison=ComparisonConfig(
        unique_keys=unique_keys or ["id"],
        checks={
            "schema": c_schema,
            "row_count": c_counts,
            "duplicate_keys": c_dup_keys,
            "nulls": c_nulls,
            "data_level": c_data_level,
            "aggregates": c_aggregates,
        },
        partition=PartitionConfig(
            strategy=strat_key,
            column=part_column if strat_key != PartitionStrategy.BOUNDED_FULL_SCAN else None,
            range_size=int(range_size),
            include_null_partition=incl_null,
            allow_full_scan_fallback=(strat_key == PartitionStrategy.BOUNDED_FULL_SCAN),
        ),
        ignored_columns=ignored_columns,
        normalization=NormalizationConfig(
            trim_strings=norm_trim,
            case_sensitive_strings=norm_case,
            decimal_tolerance=norm_dec_tol,
            timestamp_timezone=norm_tz,
        ),
    ),
    execution=ExecutionConfig(
        mode="local",
        batch_size=5000,
        max_workers=4,
        retry_attempts=3,
        query_timeout_seconds=300,
    ),
    reporting=ReportingConfig(
        formats=["json", "csv", "html"],
        mask_sensitive_values=True,
    ),
)

with exec_c1:
    if st.button("🔍 Preflight Validation", use_container_width=True):
        try:
            warnings = preflight_validate_job(job_config, secret_store=conn_service.secret_store)
            if warnings:
                st.warning("Preflight Passed with Warnings:\n" + "\n".join(f"- {w}" for w in warnings))
            else:
                st.success("✅ Preflight Passed! Configuration and connections are valid.")
        except Exception as e:
            st.error(f"❌ Preflight Failed: {e}")

with exec_c2:
    if st.button("🚀 Start Validation Run", type="primary", use_container_width=True):
        if not unique_keys:
            st.error("Please configure at least one unique key for comparison.")
        else:
            with st.spinner("Launching validation run..."):
                try:
                    run_rec = run_service.start_validation(job_config)
                    st.session_state["current_run_id"] = run_rec.run_id
                    st.success(f"Run completed with status: {run_rec.status.value}")
                    st.switch_page("pages/3_Run_Progress.py")
                except Exception as e:
                    st.error(f"Execution Error: {e}")

with exec_c3:
    with st.expander("📄 View Secret-Free Job JSON Config"):
        st.json(job_config.model_dump(mode="json"))
