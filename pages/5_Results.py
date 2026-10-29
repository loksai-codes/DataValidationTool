"""
Page 5: Results
Comprehensive validation outcomes, status definitions, check-by-check breakdown,
and interactive visual analytics.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from migration_validator.ui_common import (
    apply_theme,
    get_metadata_repo,
    init_session_state,
    render_sidebar_status,
)
from migration_validator.validation.models import ValidationStatus

st.set_page_config(page_title="Validation Results | Migration Validator", page_icon="📈", layout="wide")
apply_theme()
init_session_state()
render_sidebar_status()

st.title("📈 Validation Results & Verification Suite")

summary = st.session_state.get("latest_summary")

# Load execution runs from repository
repo = get_metadata_repo()
runs = repo.get_runs(limit=30)

if runs:
    run_options = {r["run_id"]: f"{r['run_id']} [{r['status']}] - {str(r['started_at'])[:19]}" for r in runs}
    default_idx = 0
    if summary and summary.run_id in run_options:
        default_idx = list(run_options.keys()).index(summary.run_id)

    selected_run_id = st.selectbox(
        "Select Validation Run to Inspect",
        options=list(run_options.keys()),
        index=default_idx,
        format_func=lambda x: run_options[x]
    )

    run_record = repo.get_run_by_id(selected_run_id)
    checks_data = repo.get_checks_for_run(selected_run_id)
    disc_counts = repo.get_discrepancy_counts_by_type(selected_run_id)

    from migration_validator.validation.models import (
        CheckResult,
        CheckType,
        ValidationRunSummary,
    )
    checks = [
        CheckResult(
            check_id=c["check_id"],
            check_type=CheckType(c["check_type"]),
            check_name=c["check_name"],
            status=ValidationStatus.from_str(c["status"]),
            source_value=c["source_metric"],
            dest_value=c["dest_metric"],
            difference=c["difference"],
            tolerance=c["tolerance"],
            duration_ms=c.get("duration_ms", 0.0),
            error_details=c.get("error_message")
        )
        for c in checks_data
    ]

    summary = ValidationRunSummary(
        run_id=selected_run_id,
        overall_status=ValidationStatus.from_str(run_record["status"]),
        started_at=str(run_record["started_at"]),
        completed_at=str(run_record.get("completed_at")),
        duration_seconds=run_record.get("duration_seconds", 0.0),
        total_checks=run_record.get("total_checks", len(checks)),
        passed_checks=run_record.get("passed_checks", 0),
        failed_checks=run_record.get("failed_checks", 0),
        warning_checks=run_record.get("warning_checks", 0),
        inconclusive_checks=run_record.get("inconclusive_checks", 0),
        total_records_evaluated=run_record.get("total_records_evaluated", 0),
        total_discrepancies=run_record.get("total_discrepancies", 0),
        discrepancy_counts=disc_counts,
        checks=checks
    )
repo.close()

if not summary:
    st.info("No validation results found. Configure and run a validation from **Configure Validation** first.")
    st.stop()

# 1. Overall Status Card
st.subheader("Executive Execution Summary")

status_val = summary.overall_status.value
colors = {
    "PASSED": "#10B981",
    "FAILED": "#EF4444",
    "WARNING": "#F59E0B",
    "INCONCLUSIVE": "#9CA3AF",
    "CANCELLED": "#6B7280",
    "RUNNING": "#3B82F6"
}
status_col = colors.get(status_val, "#9CA3AF")

st.markdown(f"""
<div style="background: #111827; border-left: 6px solid {status_col}; border-top: 1px solid #1F2937; border-right: 1px solid #1F2937; border-bottom: 1px solid #1F2937; border-radius: 8px; padding: 20px; margin-bottom: 24px;">
    <div style="display: flex; justify-content: space-between; align-items: center;">
        <div>
            <span style="font-family: monospace; font-size: 0.85rem; color: #9CA3AF;">RUN IDENTIFIER: {summary.run_id}</span>
            <h2 style="margin: 4px 0 0 0; color: #F3F4F6;">OVERALL STATUS: {status_val}</h2>
        </div>
        <div>
            <span style="font-size: 1.5rem; font-weight: 800; color: {status_col};">{status_val}</span>
        </div>
    </div>
    <div style="margin-top: 14px; font-size: 0.85rem; color: #9CA3AF; border-top: 1px solid #1F2937; padding-top: 12px;">
        <strong>Status Criteria:</strong>
        <span style="color: #10B981;">PASSED</span> (All checks within tolerance) &bull;
        <span style="color: #EF4444;">FAILED</span> (At least 1 check breached tolerance) &bull;
        <span style="color: #F59E0B;">WARNING</span> (Advisory data quality or non-critical notice) &bull;
        <span style="color: #3B82F6;">RUNNING</span> (Execution in progress) &bull;
        <span style="color: #9CA3AF;">INCONCLUSIVE</span> (System unavailable or count indeterminate)
    </div>
</div>
""", unsafe_allow_html=True)

# 2. Metric Cards
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Records Evaluated", f"{summary.total_records_evaluated:,}")
m2.metric("Passed Checks", f"{summary.passed_checks} / {summary.total_checks}")
m3.metric("Failed Checks", f"{summary.failed_checks}", delta_color="inverse")
m4.metric("Discrepancies", f"{summary.total_discrepancies:,}", delta_color="inverse")
m5.metric("Duration", f"{summary.duration_seconds:.2f}s")

st.divider()

# 3. Visual Charts
st.subheader("Visual Analysis")
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    # Donut chart of check results
    check_status_counts = {
        "Passed": summary.passed_checks,
        "Failed": summary.failed_checks,
        "Warning": summary.warning_checks,
        "Inconclusive": summary.inconclusive_checks
    }
    status_df = pd.DataFrame([
        {"Status": k, "Count": v} for k, v in check_status_counts.items() if v > 0
    ])

    if not status_df.empty:
        fig_pie = px.pie(
            status_df,
            names="Status",
            values="Count",
            title="Checks Distribution",
            hole=0.55,
            color="Status",
            color_discrete_map={
                "Passed": "#10B981",
                "Failed": "#EF4444",
                "Warning": "#F59E0B",
                "Inconclusive": "#9CA3AF"
            }
        )
        fig_pie.update_layout(
            paper_bgcolor="#111827",
            plot_bgcolor="#111827",
            font_color="#F3F4F6",
            margin=dict(t=40, b=20, l=20, r=20)
        )
        st.plotly_chart(fig_pie, use_container_width=True)

with chart_col2:
    # Duration / Execution Latency by Check
    dur_data = [
        {"Check": c.check_name[:25] + "...", "Duration (ms)": c.duration_ms, "Status": c.status.value}
        for c in summary.checks
    ]
    dur_df = pd.DataFrame(dur_data)
    if not dur_df.empty:
        fig_bar = px.bar(
            dur_df,
            x="Duration (ms)",
            y="Check",
            orientation="h",
            title="Execution Latency by Check (ms)",
            color="Status",
            color_discrete_map={
                "PASSED": "#10B981",
                "FAILED": "#EF4444",
                "WARNING": "#F59E0B",
                "INCONCLUSIVE": "#9CA3AF"
            }
        )
        fig_bar.update_layout(
            paper_bgcolor="#111827",
            plot_bgcolor="#111827",
            font_color="#F3F4F6",
            margin=dict(t=40, b=20, l=20, r=20),
            yaxis=dict(autorange="reversed")
        )
        st.plotly_chart(fig_bar, use_container_width=True)

st.divider()

# 4. Check-by-Check Details Table
st.subheader("Validation Checks Breakdown")

checks_rows = []
for c in summary.checks:
    checks_rows.append({
        "Status": c.status.value,
        "Check Name": c.check_name,
        "Source Metric": str(c.source_value or "-"),
        "Destination Metric": str(c.dest_value or "-"),
        "Difference": str(c.difference or "-"),
        "Tolerance": str(c.tolerance or "-"),
        "Duration (ms)": f"{c.duration_ms:.1f}",
        "Error / Notes": str(c.error_details or "Passed")
    })

checks_df = pd.DataFrame(checks_rows)
st.dataframe(checks_df, use_container_width=True, hide_index=True)
