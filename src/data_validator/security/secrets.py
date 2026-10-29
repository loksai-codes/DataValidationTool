"""Secure credential management and resolution for named connections.

Non-negotiable: Credentials are never serialized into job configs,
SQLite repository runs, or logs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from data_validator.security.redaction import redact_secrets_dict

load_dotenv()


class SecretStore:
    """Manages secure resolution of connection credentials."""

    def __init__(self, connections_file: str | Path | None = None) -> None:
        self.connections_file = (
            Path(connections_file)
            if connections_file
            else Path("configs/connections.json")
        )
        self._in_memory_overrides: dict[str, dict[str, Any]] = {}

    def set_memory_override(self, connection_id: str, config: dict[str, Any]) -> None:
        """Set credentials in memory for the current process/session."""
        self._in_memory_overrides[connection_id] = config

    def save_connection(self, connection_id: str, config: dict[str, Any]) -> None:
        """Persist a named connection configuration to local connections file."""
        self.connections_file.parent.mkdir(parents=True, exist_ok=True)
        existing = self.list_all_raw()
        existing[connection_id] = config
        with open(self.connections_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)

    def delete_connection(self, connection_id: str) -> None:
        """Remove a named connection."""
        if connection_id in self._in_memory_overrides:
            del self._in_memory_overrides[connection_id]
        existing = self.list_all_raw()
        if connection_id in existing:
            del existing[connection_id]
            with open(self.connections_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)

    def list_all_raw(self) -> dict[str, dict[str, Any]]:
        """Load all raw connection definitions (includes credentials)."""
        if self.connections_file.exists():
            try:
                with open(self.connections_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def list_connections(self) -> dict[str, dict[str, Any]]:
        """List all connections with secrets redacted."""
        raw = self.list_all_raw()
        raw.update(self._in_memory_overrides)
        return {cid: redact_secrets_dict(cfg) for cid, cfg in raw.items()}

    def get_connection(self, connection_id: str) -> dict[str, Any]:
        """Resolve full connection parameters for a connection_id.

        Priority order:
        1. In-memory session overrides
        2. Streamlit secrets (if running in Streamlit)
        3. Local connections file (`configs/connections.json`)
        4. Environment variables
        """
        if connection_id in self._in_memory_overrides:
            return dict(self._in_memory_overrides[connection_id])

        # Try streamlit secrets if available
        try:
            import streamlit as st
            if hasattr(st, "secrets") and "connections" in st.secrets:
                if connection_id in st.secrets["connections"]:
                    return dict(st.secrets["connections"][connection_id])
        except Exception:
            pass

        # Try connections file
        file_conns = self.list_all_raw()
        if connection_id in file_conns:
            return dict(file_conns[connection_id])

        # Try environment variable conventions
        # e.g. CONN_<CONNECTION_ID>_HOST, etc.
        prefix = f"CONN_{connection_id.upper()}_"
        env_config: dict[str, Any] = {}
        for k, v in os.environ.items():
            if k.startswith(prefix):
                param_key = k[len(prefix):].lower()
                env_config[param_key] = v

        if env_config:
            return env_config

        # Check default fallback mappings for standard example connections
        if connection_id in ("postgres_dev", "postgres_target"):
            return {
                "provider": "aws",
                "engine": "postgresql",
                "host": os.getenv("POSTGRES_HOST", "localhost"),
                "port": int(os.getenv("POSTGRES_PORT", "5432")),
                "database": os.getenv("POSTGRES_DATABASE", "migration_validator"),
                "username": os.getenv("POSTGRES_USERNAME", "postgres"),
                "password": os.getenv("POSTGRES_PASSWORD", ""),
            }
        elif connection_id in ("aws_analytics", "athena_source"):
            return {
                "provider": "aws",
                "engine": "athena",
                "region_name": os.getenv("AWS_REGION", "ap-south-1"),
                "profile_name": os.getenv("AWS_PROFILE", None),
                "workgroup": os.getenv("ATHENA_WORKGROUP", "primary"),
                "s3_staging_dir": os.getenv(
                    "ATHENA_S3_STAGING_DIR",
                    "s3://migration-validator-query-results/staging/",
                ),
                "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", None),
                "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", None),
            }

        raise KeyError(
            f"Named connection '{connection_id}' not found in secrets or configuration."
        )


# Global default instance
default_secret_store = SecretStore()
