"""Streamlit Page: Run Progress and Active Execution Monitoring."""
import streamlit as st

st.set_page_config(page_title="Run Progress — Validator", page_icon="⏱️", layout="wide")

from data_validator.app_services.run_service import RunService
from data_validator.persistence.models import RunStatus

run_service = RunService()

st.title("⏱️ Run Progress & Checkpoints")

# Resolve active run ID
runs = run_service.list_runs(limit=30)
run_ids = [r.run_id for r in runs]

current_id = st.session_state.get("current_run_id")
default_idx = run_ids.index(current_id) if current_id in run_ids else 0

col_sel, col_btn = st.columns([3, 1])
with col_sel:
    selected_run_id = st.selectbox(
        "Select Run to Inspect",
        run_ids or ["No runs yet"],
        index=default_idx if run_ids else 0,
    )
with col_btn:
    st.write("")
    if st.button("🔄 Refresh State", use_container_width=True):
        st.rerun()

if not run_ids or selected_run_id == "No runs yet":
    st.info("No runs found. Start a run from Compare Tables to see execution progress.")
    st.stop()

run = run_service.get_run(selected_run_id)
if not run:
    st.error("Run not found.")
    st.stop()

# Status badge formatting
status_color = (
    "🟢" if run.status == RunStatus.COMPLETED
    else "🟡" if run.status == RunStatus.COMPLETED_WITH_DISCREPANCIES
    else "🔵" if run.status == RunStatus.RUNNING
    else "🔴" if run.status in (RunStatus.FAILED, RunStatus.INTERRUPTED)
    else "⚪"
)

st.markdown(f"### Job: **{run.job_name}** | Status: {status_color} **{run.status.value}**")

# Progress bar
total_p = max(1, run.total_partitions)
completed_p = run.completed_partitions
pct = min(1.0, completed_p / total_p)
st.progress(pct, text=f"Partition Progress: {completed_p} of {run.total_partitions} partitions completed ({pct*100:.1f}%)")

# Metrics cards
c1, c2, c3, c4 = st.columns(4)
c1.metric("Rows Compared", f"{run.rows_compared:,}")
c2.metric("Missing in Target", f"{run.missing_count:,}")
c3.metric("Extra in Target", f"{run.extra_count:,}")
c4.metric("Changed Records", f"{run.changed_count:,}")

c5, c6, c7, c8 = st.columns(4)
c5.metric("Duplicate Keys", f"{run.duplicate_key_count:,}")
c6.metric("NULL Keys", f"{run.null_key_count:,}")
c7.metric("Failed Partitions", f"{run.failed_partitions}")
duration_str = "-"
if run.started_at and run.completed_at:
    duration_str = f"{(run.completed_at - run.started_at).total_seconds():.1f}s"
elif run.started_at:
    duration_str = "Running..."
c8.metric("Duration", duration_str)

st.divider()

# Actions row
act_col1, act_col2, act_col3 = st.columns([1, 1, 2])
with act_col1:
    if run.status in (RunStatus.INTERRUPTED, RunStatus.FAILED, RunStatus.PAUSED):
        if st.button("▶️ Resume Interrupted Run", type="primary", use_container_width=True):
            with st.spinner("Resuming unfinished partitions..."):
                resumed = run_service.resume_validation(run.run_id)
                st.success(f"Resume completed with status: {resumed.status.value}")
                st.rerun()
    elif run.status == RunStatus.RUNNING:
        if st.button("⏹️ Request Cancellation", type="secondary", use_container_width=True):
            run_service.cancel_run(run.run_id)
            st.warning("Cancellation requested.")
            st.rerun()

with act_col2:
    if st.button("📊 View Detailed Results", use_container_width=True):
        st.session_state["selected_result_run_id"] = run.run_id
        st.switch_page("pages/4_Results.py")

# Partitions Breakdown
st.subheader("📦 Partitions Status")
partitions = run_service.repo.get_partitions(run.run_id)
if partitions:
    part_table = []
    for p in partitions:
        part_table.append(
            {
                "Partition ID": p.partition_id,
                "Status": p.status.value,
                "Lower Bound": p.lower_bound or "-",
                "Upper Bound": p.upper_bound or "-",
                "Rows Compared": f"{p.rows_compared:,}",
                "Missing": p.missing_count,
                "Extra": p.extra_count,
                "Changed": p.changed_count,
                "Attempts": p.attempts,
                "Error": p.error_message or "-",
            }
        )
    st.dataframe(part_table, use_container_width=True, hide_index=True)
else:
    st.info("No partition details available yet.")

# Checks Breakdown
st.subheader("✅ Checks Log")
checks = run_service.repo.get_checks(run.run_id)
if checks:
    chk_table = [
        {
            "Check": c.check_name,
            "Status": c.status.value,
            "Summary": c.summary or "-",
            "Executed At": c.executed_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for c in checks
    ]
    st.dataframe(chk_table, use_container_width=True, hide_index=True)
