"""Streamlit Page: Discrepancy Inspection and Saved Report Exports."""
import json
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Validation Results — Validator", page_icon="📊", layout="wide")

from data_validator.app_services.report_service import ReportService
from data_validator.app_services.run_service import RunService
from data_validator.persistence.models import DiscrepancyType

run_service = RunService()
report_service = ReportService()

st.title("📊 Discrepancy Inspection & Reports")

runs = run_service.list_runs(limit=50)
run_ids = [r.run_id for r in runs]

selected_from_state = st.session_state.get("selected_result_run_id")
default_idx = run_ids.index(selected_from_state) if selected_from_state in run_ids else 0

top_c1, top_c2 = st.columns([3, 1])
with top_c1:
    selected_run_id = st.selectbox(
        "Select Run to Inspect",
        run_ids or ["No runs"],
        index=default_idx if run_ids else 0,
    )

if not run_ids or selected_run_id == "No runs":
    st.info("No runs available to inspect.")
    st.stop()

run = run_service.get_run(selected_run_id)
if not run:
    st.error("Run not found.")
    st.stop()

# Header Card
st.markdown(
    f"""
    <div style="background-color: #1e293b; color: white; padding: 1.25rem; border-radius: 10px; margin-bottom: 1.5rem;">
        <h2 style="margin: 0; color: #38bdf8;">{run.job_name}</h2>
        <p style="margin: 0.25rem 0 0 0; color: #94a3b8;">
            Run ID: <code>{run.run_id}</code> | Status: <strong>{run.status.value}</strong> | 
            Compared: <strong>{run.rows_compared:,}</strong> rows | 
            Total Discrepancies: <strong>{(run.missing_count + run.extra_count + run.changed_count + run.duplicate_key_count + run.null_key_count):,}</strong>
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

# Export Action Bar
exp_c1, exp_c2, exp_c3, exp_c4 = st.columns([1, 1, 1, 2])
with exp_c1:
    if st.button("📥 Export CSV", use_container_width=True):
        with st.spinner("Generating CSV..."):
            p = report_service.get_or_create_report(run.run_id, "csv")
            with open(p, "rb") as f:
                st.download_button("Download CSV", f, file_name=p.name, mime="text/csv", key="dl_csv")
with exp_c2:
    if st.button("📥 Export JSON", use_container_width=True):
        with st.spinner("Generating JSON..."):
            p = report_service.get_or_create_report(run.run_id, "json")
            with open(p, "rb") as f:
                st.download_button("Download JSON", f, file_name=p.name, mime="application/json", key="dl_json")
with exp_c3:
    if st.button("📥 Export HTML", use_container_width=True):
        with st.spinner("Generating HTML..."):
            p = report_service.get_or_create_report(run.run_id, "html")
            with open(p, "rb") as f:
                st.download_button("Download HTML", f, file_name=p.name, mime="text/html", key="dl_html")
with exp_c4:
    st.caption("🔒 *Reports are exported directly from SQLite persistence without re-running queries.*")

st.divider()

# Tabbed detailed views
(
    tab_summary,
    tab_missing,
    tab_extra,
    tab_changed,
    tab_keys,
    tab_schema,
    tab_logs,
    tab_cfg,
) = st.tabs(
    [
        "📋 Summary",
        f"Missing in Target ({run.missing_count})",
        f"Extra in Target ({run.extra_count})",
        f"Field Mismatches ({run.changed_count})",
        f"Keys & Nulls ({run.duplicate_key_count + run.null_key_count})",
        "📐 Schema",
        "📜 Execution Logs",
        "⚙️ Config Snapshot",
    ]
)

with tab_summary:
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Source Rows", f"{run.rows_source:,}")
    kpi2.metric("Target Rows", f"{run.rows_target:,}")
    kpi3.metric("Partitions Completed", f"{run.completed_partitions} / {run.total_partitions}")
    kpi4.metric("Status", run.status.value)
    
    st.subheader("Checks Overview")
    checks = run_service.repo.get_checks(run.run_id)
    if checks:
        for chk in checks:
            badge = "✅" if chk.status.value == "Passed" else "❌"
            st.markdown(f"**{badge} {chk.check_name.upper()}**: {chk.summary}")
    else:
        st.write("No checks recorded.")

def render_discrepancy_table(disc_type: DiscrepancyType, page_key: str):
    total = run_service.repo.count_discrepancies(run.run_id, disc_type.value)
    if total == 0:
        st.success(f"No discrepancies found for category '{disc_type.value}'.")
        return

    st.write(f"Total Discrepancies: **{total:,}** (Showing first 100)")
    discs = run_service.repo.get_discrepancies(run.run_id, disc_type.value, limit=100)
    data = []
    for d in discs:
        data.append(
            {
                "Partition": d.partition_id,
                "Unique Key": d.key_values,
                "Column": d.column_name or "-",
                "Source Value": d.source_value or "-",
                "Target Value": d.target_value or "-",
                "Detected At": d.detected_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    st.dataframe(data, use_container_width=True, hide_index=True)

with tab_missing:
    render_discrepancy_table(DiscrepancyType.MISSING_IN_TARGET, "missing")

with tab_extra:
    render_discrepancy_table(DiscrepancyType.EXTRA_IN_TARGET, "extra")

with tab_changed:
    render_discrepancy_table(DiscrepancyType.CHANGED_RECORD, "changed")

with tab_keys:
    st.subheader("Duplicate Keys")
    render_discrepancy_table(DiscrepancyType.DUPLICATE_KEY, "dup_keys")
    st.markdown("---")
    st.subheader("NULL Keys")
    render_discrepancy_table(DiscrepancyType.NULL_KEY, "null_keys")

with tab_schema:
    schema_chk = [c for c in run_service.repo.get_checks(run.run_id) if c.check_name == "schema"]
    if schema_chk:
        chk = schema_chk[0]
        st.write(f"**Overall**: {chk.status.value} — {chk.summary}")
        if chk.details_json:
            try:
                diffs = json.loads(chk.details_json)
                st.dataframe(diffs, use_container_width=True, hide_index=True)
            except Exception:
                st.write(chk.details_json)
    else:
        st.info("No schema checks recorded for this run.")

with tab_logs:
    logs = run_service.repo.get_event_logs(run.run_id, limit=200)
    if logs:
        log_rows = [
            {
                "Time": l.created_at.strftime("%H:%M:%S"),
                "Level": l.level,
                "Component": l.component,
                "Message": l.message,
            }
            for l in logs
        ]
        st.dataframe(log_rows, use_container_width=True, hide_index=True)
    else:
        st.write("No execution logs recorded.")

with tab_cfg:
    if run.config_json:
        try:
            cfg = json.loads(run.config_json)
            st.json(cfg)
        except Exception:
            st.code(run.config_json)
    else:
        st.write("No configuration snapshot saved.")
