"""Application service for cached metadata discovery."""
from __future__ import annotations

import time
from typing import Any

from data_validator.connectors.base import TableMetadata
from data_validator.connectors.registry import create_connector, is_connector_available
from data_validator.security.secrets import SecretStore, default_secret_store


class MetadataService:
    """Service providing cached database and table metadata discovery."""

    def __init__(self, secret_store: SecretStore | None = None) -> None:
        self.secret_store = secret_store or default_secret_store
        self._cache: dict[str, tuple[float, Any]] = {}

    def _get_cache_key(self, *parts: Any) -> str:
        return ":".join(str(p) for p in parts)

    def clear_cache(self, connection_id: str | None = None) -> None:
        """Clear cached metadata entries."""
        if connection_id:
            keys_to_del = [k for k in self._cache if k.startswith(f"{connection_id}:")]
            for k in keys_to_del:
                del self._cache[k]
        else:
            self._cache.clear()

    def list_databases(
        self,
        provider: str,
        engine: str,
        connection_id: str,
        catalog: str | None = None,
        force_refresh: bool = False,
    ) -> list[str]:
        cache_key = self._get_cache_key("databases", connection_id, catalog)
        if not force_refresh and cache_key in self._cache:
            return self._cache[cache_key][1]

        if not is_connector_available(provider, engine):
            return []

        cfg = self.secret_store.get_connection(connection_id)
        conn = create_connector(provider, engine, connection_id, cfg)
        try:
            dbs = conn.list_databases(catalog=catalog)
            self._cache[cache_key] = (time.time(), dbs)
            return dbs
        finally:
            conn.close()

    def list_schemas(
        self,
        provider: str,
        engine: str,
        connection_id: str,
        database: str | None = None,
        catalog: str | None = None,
        force_refresh: bool = False,
    ) -> list[str]:
        cache_key = self._get_cache_key("schemas", connection_id, database, catalog)
        if not force_refresh and cache_key in self._cache:
            return self._cache[cache_key][1]

        if not is_connector_available(provider, engine):
            return []

        cfg = self.secret_store.get_connection(connection_id)
        conn = create_connector(provider, engine, connection_id, cfg)
        try:
            schemas = conn.list_schemas(database=database, catalog=catalog)
            self._cache[cache_key] = (time.time(), schemas)
            return schemas
        finally:
            conn.close()

    def list_tables(
        self,
        provider: str,
        engine: str,
        connection_id: str,
        database: str | None = None,
        schema: str | None = None,
        catalog: str | None = None,
        force_refresh: bool = False,
    ) -> list[str]:
        cache_key = self._get_cache_key(
            "tables", connection_id, database, schema, catalog
        )
        if not force_refresh and cache_key in self._cache:
            return self._cache[cache_key][1]

        if not is_connector_available(provider, engine):
            return []

        cfg = self.secret_store.get_connection(connection_id)
        conn = create_connector(provider, engine, connection_id, cfg)
        try:
            tables = conn.list_tables(schema=schema, database=database, catalog=catalog)
            self._cache[cache_key] = (time.time(), tables)
            return tables
        finally:
            conn.close()

    def get_table_metadata(
        self,
        provider: str,
        engine: str,
        connection_id: str,
        table: str,
        database: str | None = None,
        schema: str | None = None,
        catalog: str | None = None,
        force_refresh: bool = False,
    ) -> TableMetadata | None:
        cache_key = self._get_cache_key(
            "table_meta", connection_id, database, schema, table
        )
        if not force_refresh and cache_key in self._cache:
            return self._cache[cache_key][1]

        if not is_connector_available(provider, engine):
            return None

        cfg = self.secret_store.get_connection(connection_id)
        conn = create_connector(provider, engine, connection_id, cfg)
        try:
            meta = conn.get_table_metadata(
                table=table, schema=schema, database=database, catalog=catalog
            )
            self._cache[cache_key] = (time.time(), meta)
            return meta
        finally:
            conn.close()
