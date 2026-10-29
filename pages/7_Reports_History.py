"""
Page 7: Reports and History
Export and download validation artifacts (JSON, CSV, HTML),
browse historical runs, and view executive summaries.
"""

import pandas as pd
import streamlit as st

from migration_validator.reports.csv_report import generate_csv_discrepancy_report
from migration_validator.reports.html_report import generate_html_report
from migration_validator.reports.json_report import generate_json_report
from migration_validator.ui_common import (
    apply_theme,
    get_metadata_repo,
    init_session_state,
    render_sidebar_status,
)
from migration_validator.validation.models import (
    CheckResult,
    CheckType,
    ValidationRunSummary,
    ValidationStatus,
)

st.set_page_config(page_title="Reports & History | Migration Validator", page_icon="📑", layout="wide")
apply_theme()
init_session_state()
render_sidebar_status()

st.title("📑 Audit Reports & Execution History")
st.caption("Generate, preview, and download compliance reports for external audits.")

repo = get_metadata_repo()

try:
    runs = repo.get_runs(limit=50)
    if not runs:
        st.info("No execution history available yet. Execute a validation run first.")
        st.stop()

    run_options = {r["run_id"]: f"{r['run_id']} [{r['status']}] - {str(r['started_at'])[:19]}" for r in runs}
    selected_run_id = st.selectbox("Select Target Run to Inspect & Export", options=list(run_options.keys()), format_func=lambda x: run_options[x])

    run_record = repo.get_run_by_id(selected_run_id)
    checks_data = repo.get_checks_for_run(selected_run_id)
    disc_data = repo.get_discrepancies_for_run(selected_run_id, limit=500)
    disc_counts = repo.get_discrepancy_counts_by_type(selected_run_id)

    # Reconstruct summary model for report generation
    checks = [
        CheckResult(
            check_id=c["check_id"],
            check_type=CheckType(c["check_type"]),
            check_name=c["check_name"],
            status=ValidationStatus(c["status"]),
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
        overall_status=ValidationStatus(run_record["status"]),
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

    src_id = run_record["source_identifier"]
    dst_id = run_record["dest_identifier"]

    # 1. Download Buttons Row
    st.subheader("Export Formats")
    d1, d2, d3 = st.columns(3)

    # JSON Report
    json_str = generate_json_report(summary, src_id, dst_id, disc_data[:50])
    with d1:
        st.download_button(
            label="📦 Download JSON Report",
            data=json_str,
            file_name=f"validation_report_{selected_run_id}.json",
            mime="application/json",
            use_container_width=True
        )

    # CSV Discrepancies
    csv_str = generate_csv_discrepancy_report(disc_data, selected_run_id)
    with d2:
        st.download_button(
            label="📊 Download CSV Discrepancies",
            data=csv_str,
            file_name=f"discrepancies_{selected_run_id}.csv",
            mime="text/csv",
            use_container_width=True
        )

    # HTML Executive Report
    html_str = generate_html_report(summary, src_id, dst_id, disc_data)
    with d3:
        st.download_button(
            label="🌐 Download HTML Report",
            data=html_str,
            file_name=f"validation_summary_{selected_run_id}.html",
            mime="text/html",
            use_container_width=True
        )

    st.divider()

    # 2. Embedded HTML Preview
    st.subheader("HTML Executive Summary Preview")
    st.components.v1.html(html_str, height=650, scrolling=True)

    st.divider()

    # 3. Complete Historical Runs Table
    st.subheader("All Historical Execution Runs")
    history_df = pd.DataFrame(runs)[[
        "run_id", "started_at", "status", "source_identifier",
        "dest_identifier", "total_checks", "passed_checks",
        "failed_checks", "total_records_evaluated", "total_discrepancies",
        "duration_seconds"
    ]]
    st.dataframe(history_df, use_container_width=True, hide_index=True)

except Exception as e:
    st.error(f"Error loading report history: {e}")
finally:
    repo.close()
