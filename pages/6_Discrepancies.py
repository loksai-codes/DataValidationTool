"""
Page 6: Discrepancy Explorer
Filter and paginate record-level discrepancies, inspect column diffs,
and export CSV reports.
"""

import pandas as pd
import streamlit as st

from migration_validator.reports.csv_report import generate_csv_discrepancy_report
from migration_validator.ui_common import (
    apply_theme,
    get_metadata_repo,
    init_session_state,
    render_sidebar_status,
)

st.set_page_config(page_title="Discrepancies | Migration Validator", page_icon="🚨", layout="wide")
apply_theme()
init_session_state()
render_sidebar_status()

st.title("🚨 Discrepancy Explorer & Audit Drilldown")
st.caption("Inspect and filter record mismatches, missing rows, and column-level divergences.")

repo = get_metadata_repo()

try:
    runs = repo.get_runs(limit=20)
    if not runs:
        st.info("No validation runs found. Run a validation first.")
        st.stop()

    run_options = {r["run_id"]: f"{r['run_id']} ({r['status']}) - {str(r['started_at'])[:19]}" for r in runs}
    selected_run_id = st.selectbox("Select Validation Run", options=list(run_options.keys()), format_func=lambda x: run_options[x])

    # Discrepancy summary counts
    counts_map = repo.get_discrepancy_counts_by_type(selected_run_id)
    total_discs = sum(counts_map.values())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Discrepancies", f"{total_discs:,}")
    c2.metric("Missing in Destination", f"{counts_map.get('MISSING_IN_DEST', 0):,}")
    c3.metric("Extra in Destination", f"{counts_map.get('EXTRA_IN_DEST', 0):,}")
    c4.metric("Value Mismatches", f"{counts_map.get('VALUE_MISMATCH', 0):,}")

    st.divider()

    # Filter & Pagination Controls
    f_col1, f_col2, f_col3 = st.columns([2, 1, 1])
    with f_col1:
        type_options = ["ALL"] + sorted(list(counts_map.keys()))
        selected_type = st.selectbox("Filter by Discrepancy Type", options=type_options)

    with f_col2:
        page_size = st.selectbox("Page Size", options=[25, 50, 100, 250], index=1)

    with f_col3:
        page_num = st.number_input("Page Number", min_value=1, value=1, step=1)

    offset = (page_num - 1) * page_size

    # Fetch paginated discrepancies
    discrepancies = repo.get_discrepancies_for_run(
        run_id=selected_run_id,
        discrepancy_type=selected_type,
        limit=page_size,
        offset=offset
    )

    if discrepancies:
        disc_df = pd.DataFrame(discrepancies)[[
            "id", "discrepancy_type", "record_key", "column_name",
            "source_val", "dest_val"
        ]]
        st.subheader(f"Displaying Records {offset + 1} &ndash; {offset + len(discrepancies)} (Page {page_num})")
        st.dataframe(disc_df, use_container_width=True, hide_index=True)

        # Download CSV Button
        csv_data = generate_csv_discrepancy_report(discrepancies, run_id=selected_run_id)
        st.download_button(
            label="📥 Download Current Discrepancies (CSV)",
            data=csv_data,
            file_name=f"discrepancies_{selected_run_id}.csv",
            mime="text/csv",
            use_container_width=False
        )
    else:
        if total_discs == 0:
            st.success("🎉 Zero discrepancies found! Source and destination systems are in exact parity.")
        else:
            st.info(f"No discrepancies found matching type filter '{selected_type}' on page {page_num}.")

except Exception as e:
    st.error(f"Error accessing discrepancies: {e}")
finally:
    repo.close()
