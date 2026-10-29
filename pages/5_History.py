"""Streamlit Page: Execution History and Audit Logs."""
import streamlit as st

st.set_page_config(page_title="History — Validator", page_icon="📜", layout="wide")

from data_validator.app_services.report_service import ReportService
from data_validator.app_services.run_service import RunService
from data_validator.persistence.models import RunStatus

run_service = RunService()
report_service = ReportService()

st.title("📜 Execution History & Audit Logs")
st.caption("Inspect past validation runs, resume interrupted executions, and export audit-ready reports.")

# Filter bar
f_col1, f_col2, f_col3 = st.columns([2, 1, 1])
with f_col1:
    search_job = st.text_input("Filter by Job Name", placeholder="e.g. customers")
with f_col2:
    status_filter = st.selectbox(
        "Filter by Status",
        ["All"] + [s.value for s in RunStatus],
        index=0,
    )
with f_col3:
    st.write("")
    if st.button("🔄 Refresh History", use_container_width=True):
        st.rerun()

status_enum = RunStatus(status_filter) if status_filter != "All" else None
runs = run_service.list_runs(limit=100, status=status_enum, job_name=search_job or None)

if not runs:
    st.info("No matching validation runs found.")
    st.stop()

st.markdown(f"**Found {len(runs)} Run(s)**")

for r in runs:
    status_icon = (
        "🟢" if r.status == RunStatus.COMPLETED
        else "🟡" if r.status == RunStatus.COMPLETED_WITH_DISCREPANCIES
        else "🔵" if r.status == RunStatus.RUNNING
        else "🔴" if r.status in (RunStatus.FAILED, RunStatus.INTERRUPTED)
        else "⚪"
    )
    
    total_disc = r.missing_count + r.extra_count + r.changed_count + r.duplicate_key_count + r.null_key_count
    
    with st.expander(f"{status_icon} **{r.job_name}** — `{r.run_id}` ({r.status.value})", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.write(f"**Started:** {r.started_at.strftime('%Y-%m-%d %H:%M:%S') if r.started_at else '-'}")
        c2.write(f"**Compared Rows:** {r.rows_compared:,}")
        c3.write(f"**Partitions:** {r.completed_partitions} / {r.total_partitions}")
        c4.write(f"**Total Discrepancies:** {total_disc:,}")
        
        btn_c1, btn_c2, btn_c3 = st.columns([1, 1, 2])
        with btn_c1:
            if st.button("📊 View Results", key=f"view_{r.run_id}", use_container_width=True):
                st.session_state["selected_result_run_id"] = r.run_id
                st.switch_page("pages/4_Results.py")
        with btn_c2:
            if r.status in (RunStatus.INTERRUPTED, RunStatus.FAILED):
                if st.button("▶️ Resume", key=f"resume_{r.run_id}", type="primary", use_container_width=True):
                    with st.spinner("Resuming..."):
                        run_service.resume_validation(r.run_id)
                        st.toast(f"Run {r.run_id} resumed!")
                        st.rerun()
            else:
                if st.button("📥 Export HTML", key=f"dl_html_{r.run_id}", use_container_width=True):
                    path = report_service.get_or_create_report(r.run_id, "html")
                    with open(path, "rb") as f:
                        st.download_button("Download", f, file_name=path.name, mime="text/html", key=f"dl_btn_{r.run_id}")
        with btn_c3:
            st.caption(f"Error: {r.error_message or 'None'}")
