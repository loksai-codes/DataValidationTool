"""Application service for managing named connections."""
from __future__ import annotations

from typing import Any

from data_validator.connectors.base import ConnectionTestResult
from data_validator.connectors.registry import create_connector, is_connector_available
from data_validator.security.secrets import SecretStore, default_secret_store


class ConnectionService:
    """Service for managing, resolving, and testing database connections."""

    def __init__(self, secret_store: SecretStore | None = None) -> None:
        self.secret_store = secret_store or default_secret_store

    def list_connections(self) -> dict[str, dict[str, Any]]:
        """List all named connections with secrets redacted."""
        return self.secret_store.list_connections()

    def get_connection(self, connection_id: str) -> dict[str, Any]:
        """Get full connection credentials for connection_id."""
        return self.secret_store.get_connection(connection_id)

    def save_connection(self, connection_id: str, config: dict[str, Any]) -> None:
        """Save or update a named connection."""
        self.secret_store.save_connection(connection_id, config)

    def delete_connection(self, connection_id: str) -> None:
        """Delete a named connection."""
        self.secret_store.delete_connection(connection_id)

    def test_connection(
        self, provider: str, engine: str, connection_id: str
    ) -> ConnectionTestResult:
        """Test connection reachability and authentication."""
        if not is_connector_available(provider, engine):
            return ConnectionTestResult(
                success=False,
                message=f"Connector for {provider}/{engine} is Planned/Unavailable.",
            )

        try:
            cfg = self.secret_store.get_connection(connection_id)
            connector = create_connector(provider, engine, connection_id, cfg)
            try:
                return connector.test_connection()
            finally:
                connector.close()
        except KeyError:
            return ConnectionTestResult(
                success=False,
                message=f"Connection ID '{connection_id}' not configured in secrets.",
            )
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message=f"Connection test error: {e}",
                error_details=str(e),
            )
