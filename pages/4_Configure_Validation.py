"""
Page 4: Configure Validation
Select validation rules, configure numeric tolerances, choose full-dataset vs sampling,
and review execution parameters before launching validation.
"""

import streamlit as st

from migration_validator.services.orchestration import ValidationOrchestrator
from migration_validator.ui_common import (
    apply_theme,
    get_connectors,
    get_metadata_repo,
    init_session_state,
    render_sidebar_status,
)
from migration_validator.validation.models import ValidationRuleConfig

st.set_page_config(page_title="Configure Validation | Migration Validator", page_icon="⚙️", layout="wide")
apply_theme()
init_session_state()
render_sidebar_status()

st.title("⚙️ Validation Rule Configuration")
st.caption("Customize test suites, numeric tolerances, batch sizes, and dataset scope.")

# Active Tables and Mappings
src_table = st.session_state.get("active_src_table", "customers")
src_db = st.session_state.get("active_src_db", "migration_validator")
dst_table = st.session_state.get("active_dst_table", "customers")
dst_schema = st.session_state.get("active_dst_schema", "public")
key_cols = st.session_state.get("active_keys", ["customer_id"])
col_mapping = st.session_state.get(
    "active_mapping",
    {
        "customer_id": "customer_id",
        "customer_name": "customer_name",
        "email": "email",
        "city": "city",
        "account_balance": "account_balance",
        "created_at": "created_at"
    }
)

st.markdown(f"""
<div style="background: #111827; border: 1px solid #1F2937; border-radius: 8px; padding: 14px 20px; margin-bottom: 20px;">
    <strong>Active Target:</strong> Source <code>{src_db}.{src_table}</code> &rarr; Destination <code>{dst_schema}.{dst_table}</code> &bull; Primary Key: <code>{', '.join(key_cols)}</code>
</div>
""", unsafe_allow_html=True)

# 1. Rule Selection Toggles
st.subheader("1. Active Validation Rules")
r_col1, r_col2 = st.columns(2)

with r_col1:
    chk_schema = st.checkbox("Schema & Data Type Compatibility", value=True, help="Validate column presence and type compatibility.")
    chk_count = st.checkbox("Total Record Count Parity", value=True, help="Dynamically query both systems for exact count match.")
    chk_key = st.checkbox("Key Uniqueness & NULL Detection", value=True, help="Verify 0 duplicate keys and 0 NULL keys.")
    chk_null = st.checkbox("Column-Level NULL Distribution", value=True, help="Compare NULL counts across all mapped fields.")

with r_col2:
    chk_aggs = st.checkbox("Numeric Aggregates (SUM, AVG, MIN, MAX)", value=True, help="Push down financial and numeric calculations.")
    chk_distinct = st.checkbox("Distinct Value Counts (e.g. Emails)", value=True, help="Validate COUNT(DISTINCT) parity.")
    chk_record = st.checkbox("Record-Level SHA-256 Fingerprint Parity", value=True, help="Stream key-based batches to detect missing, extra, and changed rows.")
    chk_dq = st.checkbox("Data Quality Rules (Advisory Only)", value=True, help="Check email formatting & occurrences without failing migration.")

st.divider()

# 2. Tolerances & Scaling Parameters
st.subheader("2. Tolerances & Execution Parameters")
p_col1, p_col2, p_col3 = st.columns(3)

with p_col1:
    tol_abs = st.number_input(
        "Absolute Numeric Tolerance",
        value=0.01,
        step=0.001,
        format="%.4f",
        help="Permissible absolute variance for float/decimal fields."
    )
    tol_rel = st.number_input(
        "Relative Numeric Tolerance",
        value=0.0001,
        step=0.00001,
        format="%.5f",
        help="Permissible relative ratio (e.g. 0.01% variance)."
    )

with p_col2:
    batch_size = st.select_slider(
        "Record Comparison Batch Size",
        options=[1000, 2500, 5000, 10000, 25000],
        value=5000,
        help="Number of records streamed per keyset batch to bound memory."
    )

with p_col3:
    sampling_mode = st.radio(
        "Validation Scope",
        options=["Full Dataset (100,000+ Records)", "Sampled Validation"],
        index=0,
        help="Never describe a sampled comparison as a full-dataset check."
    )
    use_sample = (sampling_mode == "Sampled Validation")
    sample_size = 10000
    if use_sample:
        sample_size = st.number_input("Sample Record Limit", value=10000, min_value=100, step=1000)
        st.warning("⚠️ SAMPLING ACTIVE: Results validate only the initial sampled records.")

st.divider()

# 3. Rule Summary & Launch Button
rule_config = ValidationRuleConfig(
    run_schema_validation=chk_schema,
    run_record_count=chk_count,
    run_key_validation=chk_key,
    run_null_validation=chk_null,
    run_numeric_aggregates=chk_aggs,
    run_distinct_counts=chk_distinct,
    run_record_comparison=chk_record,
    run_data_quality=chk_dq,
    key_columns=key_cols,
    column_mapping=col_mapping,
    numeric_tolerance_abs=tol_abs,
    numeric_tolerance_rel=tol_rel,
    use_sampling=use_sample,
    sample_size=sample_size,
    batch_size=batch_size
)

st.subheader("3. Execution Summary")
active_rules_count = sum([
    chk_schema, chk_count, chk_key, chk_null, chk_aggs, chk_distinct, chk_record, chk_dq
])
st.write(f"**Selected Rules:** {active_rules_count} checks active &bull; **Scope:** {'Sampled (' + str(sample_size) + ')' if use_sample else 'Full Dataset (100,000 records)'}")

launch_col, _ = st.columns([1, 3])
with launch_col:
    if st.button("🚀 Run Validation Suite", type="primary", use_container_width=True):
        progress_bar = st.progress(0.0)
        status_text = st.empty()

        def on_progress(stage: str, ratio: float, msg: str):
            progress_bar.progress(min(1.0, max(0.0, ratio)))
            status_text.markdown(f"**Stage: {stage}** &mdash; {msg or ''}")

        athena_conn, pg_conn = get_connectors()
        repo = get_metadata_repo()
        orchestrator = ValidationOrchestrator(athena_conn, pg_conn, repo)

        try:
            summary = orchestrator.execute_validation_run(
                source_table=src_table,
                dest_table=dst_table,
                rule_config=rule_config,
                source_db=src_db,
                dest_schema=dst_schema,
                progress_callback=on_progress
            )
            st.session_state["latest_summary"] = summary
            st.success(f"Validation Finished: Overall Status is **{summary.overall_status.value}** ({summary.duration_seconds:.2f}s)")
            st.info("Navigate to **Results** page to inspect detailed check results and charts.")
        except Exception as e:
            st.error(f"Validation execution failed: {e}")
        finally:
            athena_conn.close()
            pg_conn.close()
            repo.close()
