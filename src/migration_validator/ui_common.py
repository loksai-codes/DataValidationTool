"""
Shared Streamlit UI helpers, session state management, and mission-control styling.
"""

import streamlit as st

from migration_validator.config import AppConfig, load_config
from migration_validator.connectors.athena import AthenaConnector
from migration_validator.connectors.postgres import PostgresConnector
from migration_validator.repositories.metadata_repository import MetadataRepository

CUSTOM_CSS = """
<style>
    /* Mission Control Dark Theme Enhancements */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, sans-serif;
    }

    code, pre, .stCodeBlock {
        font-family: 'JetBrains Mono', monospace !important;
    }

    /* Metric Cards */
    div[data-testid="stMetric"] {
        background-color: #111827;
        border: 1px solid #1F2937;
        border-radius: 10px;
        padding: 16px 20px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }
    div[data-testid="stMetricLabel"] {
        color: #9CA3AF !important;
        font-size: 0.8rem !important;
        font-weight: 600 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
    }
    div[data-testid="stMetricValue"] {
        color: #F9FAFB !important;
        font-weight: 700 !important;
    }

    /* Badges */
    .badge-passed {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 0.75rem;
        letter-spacing: 0.05em;
        color: #10B981;
        background: rgba(16, 185, 129, 0.15);
        border: 1px solid #064E3B;
    }
    .badge-failed {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 0.75rem;
        letter-spacing: 0.05em;
        color: #EF4444;
        background: rgba(239, 68, 68, 0.15);
        border: 1px solid #7F1D1D;
    }
    .badge-warning {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 0.75rem;
        letter-spacing: 0.05em;
        color: #F59E0B;
        background: rgba(245, 158, 11, 0.15);
        border: 1px solid #78350F;
    }
    .badge-inconclusive {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 0.75rem;
        letter-spacing: 0.05em;
        color: #9CA3AF;
        background: rgba(156, 163, 175, 0.15);
        border: 1px solid #374151;
    }

    /* Dataframe polish */
    div[data-testid="stDataFrame"] {
        border: 1px solid #1F2937;
        border-radius: 8px;
        overflow: hidden;
    }

    /* Sidebar Mission Control Header */
    .sidebar-brand {
        padding: 12px 0 20px 0;
        border-bottom: 1px solid #1F2937;
        margin-bottom: 20px;
    }
    .sidebar-title {
        font-size: 1.15rem;
        font-weight: 700;
        color: #F3F4F6;
        letter-spacing: -0.02em;
        margin: 0;
    }
    .sidebar-subtitle {
        font-size: 0.75rem;
        color: #6B7280;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-top: 4px;
    }
</style>
"""


def apply_theme():
    """Apply CSS styles to the current page."""
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def init_session_state():
    """Ensure all required session state variables are initialized."""
    if "config" not in st.session_state:
        st.session_state.config = load_config()

    if "latest_summary" not in st.session_state:
        st.session_state.latest_summary = None

    if "is_running" not in st.session_state:
        st.session_state.is_running = False

    if "cancel_requested" not in st.session_state:
        st.session_state.cancel_requested = False


def get_metadata_repo() -> MetadataRepository:
    """Return an active metadata repository."""
    init_session_state()
    return MetadataRepository(st.session_state.config.postgres)


def get_connectors():
    """Return active Athena and PostgreSQL connectors."""
    init_session_state()
    cfg: AppConfig = st.session_state.config
    athena = AthenaConnector(cfg.aws)
    postgres = PostgresConnector(cfg.postgres)
    return athena, postgres


def render_sidebar_status():
    """Render system status widget in sidebar."""
    st.sidebar.markdown("""
    <div class="sidebar-brand">
        <div class="sidebar-title">&#9889; MIGRATION VALIDATOR</div>
        <div class="sidebar-subtitle">Enterprise Data Quality Engine</div>
    </div>
    """, unsafe_allow_html=True)

    st.sidebar.caption("ACTIVE TARGETS")
    st.sidebar.text(f"Source: Athena / S3\nRegion: {st.session_state.config.aws.region}")
    st.sidebar.text(f"Dest: PostgreSQL 18\nHost: {st.session_state.config.postgres.host}")


def render_status_badge(status_str: str) -> str:
    """Return HTML for a status badge."""
    s = status_str.upper()
    if s == "PASSED":
        return '<span class="badge-passed">PASSED</span>'
    elif s == "FAILED":
        return '<span class="badge-failed">FAILED</span>'
    elif s == "WARNING":
        return '<span class="badge-warning">WARNING</span>'
    elif s == "INCONCLUSIVE":
        return '<span class="badge-inconclusive">INCONCLUSIVE</span>'
    else:
        return f'<span class="badge-inconclusive">{s}</span>'
