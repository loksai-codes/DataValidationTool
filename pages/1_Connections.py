"""Streamlit Page: Connection Management."""
import streamlit as st

st.set_page_config(page_title="Manage Connections — Validator", page_icon="🔌", layout="wide")

from data_validator.app_services.connections import ConnectionService
from data_validator.connectors.registry import is_connector_available

conn_service = ConnectionService()

st.title("🔌 Named Connection Management")
st.caption(
    "Configure named database connections and credentials. Secrets are stored securely in local secret storage or environment variables and are never embedded into job configs or SQLite validation runs."
)

tab_list, tab_create = st.tabs(["📋 Configured Connections", "➕ Add / Edit Connection"])

with tab_list:
    conns = conn_service.list_connections()
    if conns:
        st.subheader("Existing Connections")
        for cid, cfg in sorted(conns.items()):
            with st.expander(f"Connection: **{cid}** ({cfg.get('provider', '').upper()} / {cfg.get('engine', '').capitalize()})", expanded=False):
                col_info, col_actions = st.columns([3, 1])
                with col_info:
                    st.json(cfg)
                with col_actions:
                    prov = cfg.get("provider", "other")
                    eng = cfg.get("engine", "postgresql")
                    if st.button(f"Test '{cid}'", key=f"test_{cid}", use_container_width=True):
                        with st.spinner("Testing connection..."):
                            res = conn_service.test_connection(prov, eng, cid)
                            if res.success:
                                st.success(f"Connected! {res.message} ({res.latency_ms:.1f}ms)")
                            else:
                                st.error(f"Failed: {res.message}")
                    if st.button(f"Delete '{cid}'", key=f"del_{cid}", use_container_width=True, type="secondary"):
                        conn_service.delete_connection(cid)
                        st.toast(f"Connection '{cid}' deleted.")
                        st.rerun()
    else:
        st.info("No custom connections configured yet. Use the tab above to create one, or rely on environment variables.")

with tab_create:
    st.subheader("Create or Update Named Connection")
    
    col_p, col_e, col_id = st.columns(3)
    with col_p:
        provider = st.selectbox("Provider", ["AWS", "Other / Self-Hosted", "Azure", "GCP", "Alibaba"], index=0)
    
    prov_key = "aws" if "AWS" in provider else "azure" if "Azure" in provider else "gcp" if "GCP" in provider else "alibaba" if "Alibaba" in provider else "other"
    
    engine_options = []
    if prov_key == "aws":
        engine_options = ["Athena", "PostgreSQL", "MySQL (Planned)"]
    elif prov_key == "other":
        engine_options = ["PostgreSQL", "Synthetic (In-Memory/Demo)", "MySQL (Planned)", "MSSQL (Planned)"]
    elif prov_key == "azure":
        engine_options = ["MSSQL (Planned)", "PostgreSQL"]
    elif prov_key == "gcp":
        engine_options = ["PostgreSQL"]
    else:
        engine_options = ["MaxCompute (Planned)"]

    with col_e:
        engine_choice = st.selectbox("Engine", engine_options)
        
    engine_key = "athena" if "Athena" in engine_choice else "postgresql" if "PostgreSQL" in engine_choice else "synthetic" if "Synthetic" in engine_choice else "mysql" if "MySQL" in engine_choice else "mssql" if "MSSQL" in engine_choice else "maxcompute"

    is_supported = is_connector_available(prov_key, engine_key)
    if not is_supported:
        st.warning(f"⚠️ Connector for {provider} / {engine_choice} is Planned. You can configure it, but live validation requires Implemented connectors (Athena, PostgreSQL, Synthetic).")

    with col_id:
        connection_id = st.text_input("Connection ID", placeholder="e.g. aws_analytics or postgres_prod")

    st.markdown("---")
    conn_params: dict = {"provider": prov_key, "engine": engine_key}

    if engine_key == "athena":
        st.info("💡 **Athena S3 Configuration**: Configure workgroup and query results output directory here so that table selection does not require picking an S3 bucket.")
        c1, c2 = st.columns(2)
        with c1:
            conn_params["region_name"] = st.text_input("AWS Region", value="ap-south-1")
            conn_params["workgroup"] = st.text_input("Athena Workgroup", value="primary")
            conn_params["profile_name"] = st.text_input("AWS Profile Name (optional)", value="")
        with c2:
            conn_params["s3_staging_dir"] = st.text_input(
                "S3 Query Result Output Location",
                value="s3://migration-validator-query-results/staging/",
                help="S3 directory where Athena stores query output files."
            )
            conn_params["aws_access_key_id"] = st.text_input("AWS Access Key ID (optional)", type="password")
            conn_params["aws_secret_access_key"] = st.text_input("AWS Secret Access Key (optional)", type="password")

    elif engine_key == "postgresql":
        c1, c2 = st.columns(2)
        with c1:
            conn_params["host"] = st.text_input("Host", value="localhost")
            conn_params["port"] = st.number_input("Port", value=5432, step=1)
            conn_params["database"] = st.text_input("Database Name", value="migration_validator")
        with c2:
            conn_params["username"] = st.text_input("Username", value="postgres")
            conn_params["password"] = st.text_input("Password", type="password")
            conn_params["sslmode"] = st.selectbox("SSL Mode", ["prefer", "require", "disable"], index=0)

    elif engine_key == "synthetic":
        st.info("Synthetic In-Memory or SQLite connector for local validation demos and failure recovery tests.")
        conn_params["db_path"] = st.text_input("SQLite Database Path (or :memory:)", value="data/synthetic_demo.db")

    st.markdown("---")
    btn_col1, btn_col2 = st.columns([1, 4])
    with btn_col1:
        if st.button("Save Connection", type="primary", use_container_width=True):
            if not connection_id.strip():
                st.error("Please specify a valid Connection ID.")
            else:
                conn_service.save_connection(connection_id.strip(), conn_params)
                st.success(f"Connection '{connection_id.strip()}' saved successfully!")
                st.rerun()

    with btn_col2:
        if st.button("Test Parameters", use_container_width=False):
            if not is_supported:
                st.error("This connector is marked Planned and cannot be tested yet.")
            else:
                from data_validator.connectors.registry import create_connector
                try:
                    with st.spinner("Testing parameters..."):
                        c_inst = create_connector(prov_key, engine_key, connection_id or "temp_test", conn_params)
                        res = c_inst.test_connection()
                        c_inst.close()
                    if res.success:
                        st.success(f"Success! {res.message} (Latency: {res.latency_ms:.1f}ms)")
                    else:
                        st.error(f"Failed: {res.message}")
                except Exception as e:
                    st.error(f"Error testing connection: {e}")
