"""Abstract connector base interface and common data structures."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generator


@dataclass
class ConnectionTestResult:
    success: bool
    message: str
    latency_ms: float = 0.0
    server_version: str | None = None
    error_details: str | None = None


@dataclass
class ColumnMetadata:
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    ordinal_position: int = 0
    precision: int | None = None
    scale: int | None = None


@dataclass
class TableMetadata:
    catalog: str | None
    database: str
    schema: str | None
    table: str
    columns: list[ColumnMetadata] = field(default_factory=list)
    primary_keys: list[str] = field(default_factory=list)
    estimated_row_count: int | None = None
    fetched_at: str | None = None


@dataclass
class ConnectorCapabilities:
    supports_schemas: bool = True
    supports_catalogs: bool = False
    supports_read_only_transactions: bool = True
    supports_server_side_cursors: bool = True
    supports_cancellation: bool = True
    supports_estimates: bool = True
    notes: str = ""


@dataclass
class ColumnStats:
    min_value: Any = None
    max_value: Any = None
    null_count: int = 0
    distinct_count: int | None = None


class BaseConnector(ABC):
    """Abstract base connector interface for all supported databases and query engines."""

    def __init__(self, connection_id: str, config: dict[str, Any]) -> None:
        self.connection_id = connection_id
        self.config = config

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Test connection reachability, authentication, and permissions."""
        pass

    @abstractmethod
    def list_catalogs(self) -> list[str]:
        """List available catalogs (e.g. Athena AwsDataCatalog)."""
        pass

    @abstractmethod
    def list_databases(self, catalog: str | None = None) -> list[str]:
        """List databases within a catalog."""
        pass

    @abstractmethod
    def list_schemas(
        self, database: str | None = None, catalog: str | None = None
    ) -> list[str]:
        """List schemas within a database."""
        pass

    @abstractmethod
    def list_tables(
        self,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
    ) -> list[str]:
        """List tables within a schema/database."""
        pass

    @abstractmethod
    def get_table_metadata(
        self,
        table: str,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
    ) -> TableMetadata:
        """Retrieve table structure, column types, nullability, and primary keys."""
        pass

    @abstractmethod
    def get_filtered_row_count(
        self,
        table: str,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
        filter_expr: str | None = None,
    ) -> int:
        """Get exact count of rows matching filter expression."""
        pass

    @abstractmethod
    def get_column_stats(
        self,
        table: str,
        column: str,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
        filter_expr: str | None = None,
    ) -> ColumnStats:
        """Retrieve min, max, and null statistics for a partition column."""
        pass

    @abstractmethod
    def fetch_records_batch(
        self,
        table: str,
        schema: str | None = None,
        database: str | None = None,
        catalog: str | None = None,
        columns: list[str] | None = None,
        filter_expr: str | None = None,
        batch_size: int = 5000,
    ) -> Generator[list[dict[str, Any]], None, None]:
        """Stream batches of rows matching the query specification."""
        pass

    @abstractmethod
    def get_capabilities(self) -> ConnectorCapabilities:
        """Declare connector capabilities."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close connections and release resources."""
        pass
