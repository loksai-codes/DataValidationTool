"""
Data Migration Validator - Mission Control Application
Streamlit entry point.
"""

import streamlit as st

st.set_page_config(
    page_title="Migration Validator | Mission Control",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

from migration_validator.ui_common import apply_theme, init_session_state, render_sidebar_status

apply_theme()
init_session_state()
render_sidebar_status()

# Welcome / Mission Control Home
st.title("⚡ Data Migration Validator")
st.markdown("""
<div style="background: #111827; border: 1px solid #1F2937; border-radius: 12px; padding: 24px; margin-bottom: 24px;">
    <h3 style="margin-top: 0; color: #F3F4F6;">Mission Control Architecture</h3>
    <p style="color: #9CA3AF; margin-bottom: 0;">
        Enterprise-grade data validation platform comparing cloud data lakes (Amazon Athena / S3 Parquet)
        against relational targets (PostgreSQL). Validates schemas, dynamic record counts, key uniqueness,
        NULL distributions, numeric aggregates, and record-level SHA-256 fingerprints at scale.
    </p>
</div>
""", unsafe_allow_html=True)

col1, col2, col3 = st.columns(3)
with col1:
    st.markdown("""
    #### 1. Connections & Exploration
    - **Step 1:** Configure Athena & PostgreSQL in [Connections](2_Connections).
    - **Step 2:** Inspect schemas & verify column mappings in [Dataset Explorer](3_Dataset_Explorer).
    """)

with col2:
    #### 2. Rules & Execution
    st.markdown("""
    #### 2. Rules & Execution
    - **Step 3:** Select rules, tolerances & sampling in [Configure Validation](4_Configure_Validation).
    - **Step 4:** Monitor progress & review outcomes in [Results](5_Results).
    """)

with col3:
    st.markdown("""
    #### 3. Audit & Reporting
    - **Step 5:** Drill down into column mismatches in [Discrepancies](6_Discrepancies).
    - **Step 6:** Export JSON, CSV & HTML audit artifacts in [Reports & History](7_Reports_History).
    """)

st.divider()

# Quick Overview of Recent Activity
from migration_validator.ui_common import get_metadata_repo

repo = get_metadata_repo()
try:
    stats = repo.get_overall_summary_stats()
    runs = repo.get_runs(limit=5)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Validation Runs", f"{stats.get('total_runs', 0):,}")
    m2.metric("Passed Runs", f"{stats.get('passed_runs', 0):,}")
    m3.metric("Failed Runs", f"{stats.get('failed_runs', 0):,}")
    m4.metric("Records Evaluated", f"{stats.get('total_records_validated', 0):,}")
    m5.metric("Total Discrepancies", f"{stats.get('total_discrepancies', 0):,}")

    if runs:
        st.subheader("Recent Validation Runs")
        import pandas as pd
        df = pd.DataFrame(runs)[[
            "run_id", "started_at", "status", "source_identifier",
            "dest_identifier", "passed_checks", "failed_checks", "duration_seconds"
        ]]
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No validation runs recorded yet. Navigate to **Connections** or **Configure Validation** to launch your first run.")
except Exception as e:
    st.warning(f"Metadata repository initialized: {e}")
finally:
    repo.close()
