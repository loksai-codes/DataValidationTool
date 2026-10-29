"""
Page 2: Connections
Configuration and live connection testing for AWS Athena / S3 and PostgreSQL.
Follows security best practices: secrets are masked and never logged in plaintext.
"""

import streamlit as st
from pydantic import SecretStr

from migration_validator.config import AWSAthenaConfig, PostgresConfig
from migration_validator.connectors.athena import AthenaConnector
from migration_validator.connectors.postgres import PostgresConnector
from migration_validator.ui_common import (
    apply_theme,
    init_session_state,
    render_sidebar_status,
)

st.set_page_config(page_title="Connections | Migration Validator", page_icon="🔌", layout="wide")
apply_theme()
init_session_state()
render_sidebar_status()

st.title("🔌 Connector Management & Health Verification")
st.caption("Verify and configure source (AWS Athena / S3) and destination (PostgreSQL) endpoints.")

tab_src, tab_dst = st.tabs(["Source: Amazon Athena & S3", "Destination: PostgreSQL"])

# TAB 1: ATHENA & S3
with tab_src:
    st.subheader("Amazon Athena & S3 Source Configuration")
    st.info("Uses standard AWS credential provider chain and local AWS CLI profiles (~/.aws/config).")

    cfg_aws: AWSAthenaConfig = st.session_state.config.aws

    col_a1, col_a2 = st.columns(2)
    with col_a1:
        aws_region = st.text_input("AWS Region", value=cfg_aws.region, help="AWS region hosting Athena and S3 bucket.")
        aws_profile = st.text_input("AWS CLI Profile", value=cfg_aws.profile or "", help="Named profile from ~/.aws/credentials.")
        athena_db = st.text_input("Athena Database", value=cfg_aws.athena_database, help="Glue / Athena catalog database.")

    with col_a2:
        athena_wg = st.text_input("Athena WorkGroup", value=cfg_aws.athena_workgroup, help="Athena query execution workgroup.")
        athena_output = st.text_input("Athena Query Result S3 Location", value=cfg_aws.s3_output_location, help="S3 bucket and prefix for query outputs.")
        s3_source = st.text_input("S3 Source Parquet Object", value=f"s3://{cfg_aws.s3_source_bucket}/{cfg_aws.s3_source_key}", help="Direct S3 Parquet dataset URI.")

    if st.button("⚡ Test Athena & S3 Connection", type="primary"):
        with st.spinner("Connecting to AWS Athena and verifying S3 bucket..."):
            # Update config in session
            st.session_state.config.aws.region = aws_region
            st.session_state.config.aws.profile = aws_profile if aws_profile else None
            st.session_state.config.aws.athena_database = athena_db
            st.session_state.config.aws.athena_workgroup = athena_wg
            st.session_state.config.aws.s3_output_location = athena_output

            connector = AthenaConnector(st.session_state.config.aws)
            res = connector.test_connection()
            connector.close()

            if res.is_successful:
                st.success(f"**{res.service_name}**: {res.message} (Latency: {res.latency_ms} ms)")
                if res.details.get("athena_iam_notice"):
                    st.warning(f"**IAM Notice**: {res.details['athena_iam_notice']}")
                    st.markdown("""
                    > **Diagnostic**: To enable full Athena query pushdown via Athena API, ensure the IAM user/role has:
                    > - `athena:StartQueryExecution`
                    > - `athena:GetQueryExecution`
                    > - `athena:GetQueryResults`
                    > - `glue:GetDatabase` & `glue:GetTable`
                    >
                    > The application automatically utilizes the high-performance **S3 Direct Engine** for full dataset validation.
                    """)
            else:
                st.error(f"**Connection Failed**: {res.message}")


# TAB 2: POSTGRESQL
with tab_dst:
    st.subheader("PostgreSQL Destination Configuration")
    st.caption("Configures target relational database and application metadata repository.")

    cfg_pg: PostgresConfig = st.session_state.config.postgres

    col_p1, col_p2 = st.columns(2)
    with col_p1:
        pg_host = st.text_input("PostgreSQL Host", value=cfg_pg.host)
        pg_port = st.number_input("Port", value=cfg_pg.port, min_value=1, max_value=65535)
        pg_database = st.text_input("Database Name", value=cfg_pg.database)

    with col_p2:
        pg_user = st.text_input("Username", value=cfg_pg.user)
        pg_pass = st.text_input(
            "Password",
            value=cfg_pg.raw_password,
            type="password",
            help="Database password (masked, never logged)"
        )

    if st.button("⚡ Test PostgreSQL Connection", type="primary"):
        with st.spinner("Connecting to PostgreSQL endpoint..."):
            st.session_state.config.postgres.host = pg_host
            st.session_state.config.postgres.port = int(pg_port)
            st.session_state.config.postgres.database = pg_database
            st.session_state.config.postgres.user = pg_user
            if pg_pass:
                st.session_state.config.postgres.password = SecretStr(pg_pass)

            connector = PostgresConnector(st.session_state.config.postgres)
            res = connector.test_connection()
            connector.close()

            if res.is_successful:
                st.success(f"**{res.service_name}**: {res.message} (Latency: {res.latency_ms} ms)")
                st.json(res.details)
            else:
                st.error(f"**Connection Failed**: {res.message}")
