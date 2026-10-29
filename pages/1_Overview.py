"""
Page 1: Overview
Mission control dashboard displaying system KPIs, live connection health,
recent execution runs, and latest run outcomes.
"""

import pandas as pd
import streamlit as st

from migration_validator.ui_common import (
    apply_theme,
    get_metadata_repo,
    init_session_state,
    render_sidebar_status,
)

st.set_page_config(page_title="Overview | Migration Validator", page_icon="📊", layout="wide")
apply_theme()
init_session_state()
render_sidebar_status()

st.title("📊 Mission Control Overview")
st.caption("Real-time telemetry, connector health, and migration validation history.")

# 1. Connection Health Banner
st.subheader("Connector Health Status")
col_conn1, col_conn2 = st.columns(2)

with col_conn1:
    with st.container():
        st.markdown("""
        <div style="background: #111827; border: 1px solid #1F2937; border-radius: 8px; padding: 16px;">
            <div style="font-weight: 600; font-size: 0.9rem; color: #9CA3AF;">SOURCE CONNECTOR</div>
            <div style="font-size: 1.1rem; font-weight: 700; color: #F3F4F6; margin-top: 4px;">Amazon Athena & S3 Data Lake</div>
            <div style="font-size: 0.8rem; color: #6B7280; font-family: monospace; margin-top: 2px;">
                Bucket: loksai-migration-validator-960607644797 &bull; Region: ap-south-1
            </div>
        </div>
        """, unsafe_allow_html=True)

with col_conn2:
    with st.container():
        st.markdown("""
        <div style="background: #111827; border: 1px solid #1F2937; border-radius: 8px; padding: 16px;">
            <div style="font-weight: 600; font-size: 0.9rem; color: #9CA3AF;">DESTINATION CONNECTOR</div>
            <div style="font-size: 1.1rem; font-weight: 700; color: #F3F4F6; margin-top: 4px;">PostgreSQL 18.6</div>
            <div style="font-size: 0.8rem; color: #6B7280; font-family: monospace; margin-top: 2px;">
                Database: migration_validator &bull; Host: localhost:5432
            </div>
        </div>
        """, unsafe_allow_html=True)

st.divider()

# 2. Key Performance Indicators
repo = get_metadata_repo()
try:
    stats = repo.get_overall_summary_stats()
    runs = repo.get_runs(limit=10)

    total_runs = stats.get("total_runs", 0)
    passed_runs = stats.get("passed_runs", 0)
    failed_runs = stats.get("failed_runs", 0)
    warning_runs = stats.get("warning_runs", 0)
    inconclusive_runs = stats.get("inconclusive_runs", 0)
    records_validated = stats.get("total_records_validated", 0)
    discrepancies = stats.get("total_discrepancies", 0)

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Total Runs", f"{total_runs:,}")
    k2.metric("Passed Runs", f"{passed_runs:,}", delta=f"{passed_runs}/{total_runs}" if total_runs else None)
    k3.metric("Failed Runs", f"{failed_runs:,}", delta=f"{failed_runs} failed" if failed_runs else None, delta_color="inverse")
    k4.metric("Need Investigation", f"{(warning_runs + inconclusive_runs):,}")
    k5.metric("Records Validated", f"{records_validated:,}")
    k6.metric("Discrepancies", f"{discrepancies:,}", delta=f"{discrepancies} diffs" if discrepancies else None, delta_color="inverse")

    st.write("")

    # 3. Latest Run Summary or Empty State
    if runs:
        latest = runs[0]
        st.subheader("Latest Validation Execution")

        status_color = "#10B981" if latest["status"] == "PASSED" else ("#EF4444" if latest["status"] == "FAILED" else "#F59E0B")

        st.markdown(f"""
        <div style="background: #111827; border-left: 4px solid {status_color}; border-top: 1px solid #1F2937; border-right: 1px solid #1F2937; border-bottom: 1px solid #1F2937; border-radius: 8px; padding: 20px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <span style="font-family: monospace; font-size: 0.85rem; color: #9CA3AF;">{latest['run_id']}</span>
                    <h3 style="margin: 4px 0 0 0; color: #F3F4F6;">{latest['source_identifier']} &rarr; {latest['dest_identifier']}</h3>
                </div>
                <div>
                    <span style="font-size: 1.1rem; font-weight: 700; color: {status_color};">{latest['status']}</span>
                </div>
            </div>
            <div style="display: flex; gap: 24px; margin-top: 16px; font-size: 0.85rem; color: #9CA3AF;">
                <div>Started: <strong style="color: #E5E7EB;">{str(latest['started_at'])[:19]}</strong></div>
                <div>Duration: <strong style="color: #E5E7EB;">{latest.get('duration_seconds', 0):.2f}s</strong></div>
                <div>Passed Checks: <strong style="color: #10B981;">{latest['passed_checks']}/{latest['total_checks']}</strong></div>
                <div>Failed Checks: <strong style="color: #EF4444;">{latest['failed_checks']}</strong></div>
                <div>Records Evaluated: <strong style="color: #E5E7EB;">{latest['total_records_evaluated']:,}</strong></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # 4. Recent Execution History Table
        st.subheader("Recent Execution History")
        run_df = pd.DataFrame(runs)[[
            "run_id", "started_at", "status", "source_identifier",
            "dest_identifier", "total_checks", "passed_checks",
            "failed_checks", "total_discrepancies", "duration_seconds"
        ]]
        st.dataframe(run_df, use_container_width=True)

    else:
        # Empty state when no runs have been executed yet
        st.info("""
        **No validation executions found.**

        The repository is connected and ready. Before initial validation:
        1. Review connection credentials in **Connections**.
        2. Inspect source & destination schema in **Dataset Explorer**.
        3. Trigger your first validation in **Configure Validation**.
        """)

except Exception as e:
    st.error(f"Error accessing metadata repository: {e}")
finally:
    repo.close()
