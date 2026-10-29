"""Streamlit Data Validation Platform — Main Application Dashboard."""
import streamlit as st

st.set_page_config(
    page_title="Data Migration Validator",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from data_validator.app_services.connections import ConnectionService
from data_validator.app_services.run_service import RunService
from data_validator.connectors.registry import CONNECTOR_SUPPORT_MATRIX, ConnectorStatus
from data_validator.persistence.models import RunStatus

# Global services
run_service = RunService()
conn_service = ConnectionService()

# Recover any stale running leases from previous app/process crashes
recovered = run_service.recover_stale_runs()

# Custom CSS for rich aesthetics
st.markdown(
    """
    <style>
    .metric-card {
        background-color: #1e293b;
        color: #ffffff;
        padding: 1.25rem;
        border-radius: 10px;
        border: 1px solid #334155;
        text-align: center;
    }
    .metric-card h3 {
        margin: 0;
        font-size: 0.85rem;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .metric-card p {
        margin: 0.5rem 0 0 0;
        font-size: 1.8rem;
        font-weight: 700;
        color: #38bdf8;
    }
    .status-badge {
        display: inline-block;
        padding: 0.25rem 0.6rem;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .status-implemented { background-color: #065f46; color: #34d399; }
    .status-planned { background-color: #374151; color: #9ca3af; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🛡️ Data Migration Validation Platform")
st.caption(
    "High-throughput, partition-based cross-database validation engine with durable resume, actual value comparison, and zero cross-platform SQL joins."
)

if recovered > 0:
    st.info(f"🔄 Recovered {recovered} stale run(s) from previous process interruption.")

# Summary metrics
runs = run_service.list_runs(limit=100)
total_runs = len(runs)
completed_runs = sum(1 for r in runs if r.status == RunStatus.COMPLETED)
with_disc_runs = sum(1 for r in runs if r.status == RunStatus.COMPLETED_WITH_DISCREPANCIES)
interrupted_runs = sum(1 for r in runs if r.status in (RunStatus.INTERRUPTED, RunStatus.FAILED))

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown(
        f"<div class='metric-card'><h3>Total Executions</h3><p>{total_runs}</p></div>",
        unsafe_allow_html=True,
    )
with col2:
    st.markdown(
        f"<div class='metric-card'><h3>Clean Matches</h3><p>{completed_runs}</p></div>",
        unsafe_allow_html=True,
    )
with col3:
    st.markdown(
        f"<div class='metric-card'><h3>Discrepancies Caught</h3><p>{with_disc_runs}</p></div>",
        unsafe_allow_html=True,
    )
with col4:
    st.markdown(
        f"<div class='metric-card'><h3>Interrupted / Failed</h3><p>{interrupted_runs}</p></div>",
        unsafe_allow_html=True,
    )

st.divider()

col_left, col_right = st.columns([3, 2])

with col_left:
    st.subheader("⚡ Quick Navigation")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("🔌 Manage Connections", use_container_width=True):
            st.switch_page("pages/1_Connections.py")
    with c2:
        if st.button("⚖️ Compare Tables", use_container_width=True, type="primary"):
            st.switch_page("pages/2_Compare_Tables.py")
    with c3:
        if st.button("📜 Execution History", use_container_width=True):
            st.switch_page("pages/5_History.py")

    st.subheader("📋 Recent Validation Runs")
    if runs:
        recent_data = []
        for r in runs[:8]:
            recent_data.append(
                {
                    "Run ID": r.run_id,
                    "Job Name": r.job_name,
                    "Status": r.status.value,
                    "Compared Rows": f"{r.rows_compared:,}",
                    "Discrepancies": f"{(r.missing_count + r.extra_count + r.changed_count + r.duplicate_key_count + r.null_key_count):,}",
                    "Started": r.started_at.strftime("%Y-%m-%d %H:%M:%S") if r.started_at else "-",
                }
            )
        st.dataframe(recent_data, use_container_width=True, hide_index=True)
    else:
        st.info("No validation runs recorded yet. Click 'Compare Tables' to create and run your first job.")

with col_right:
    st.subheader("🧩 Connector Support Matrix")
    matrix_rows = []
    for (prov, eng), stat in sorted(CONNECTOR_SUPPORT_MATRIX.items()):
        badge = "🟢 Implemented" if stat == ConnectorStatus.IMPLEMENTED else "⚪ Planned"
        matrix_rows.append(
            {"Provider": prov.upper(), "Engine": eng.capitalize(), "Status": badge}
        )
    st.dataframe(matrix_rows, use_container_width=True, hide_index=True)

    st.subheader("🔒 Security & Architecture Principles")
    st.markdown(
        """
        - **Read-Only Business Access:** Connections run in read-only mode; source and target data is never modified.
        - **Zero Secret Ingestion:** Passwords and cloud keys are resolved from local secure stores or environment variables, never committed to configs, databases, or logs.
        - **Durable Checkpoints:** Every partition commit is durable in SQLite WAL storage. Any interruption can be cleanly resumed without re-processing finished partitions.
        - **Actual Value Reconciliation:** Compares field values directly using unique keys; no hash collisions or cross-platform SQL joins.
        """
    )
