"""
Abstract base connector interface and common data structures.
Enables pluggable data source and destination implementations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


@dataclass
class ConnectionTestResult:
    """Result of testing a database or service connection."""
    is_successful: bool
    service_name: str
    message: str
    latency_ms: float
    details: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None


@dataclass
class ColumnMetadata:
    """Metadata describing a single table column."""
    name: str
    data_type: str
    is_nullable: bool = True
    is_primary_key: bool = False
    ordinal_position: int = 0
    character_maximum_length: Optional[int] = None
    numeric_precision: Optional[int] = None
    numeric_scale: Optional[int] = None


@dataclass
class TableSchema:
    """Complete schema definition for a table."""
    database_name: str
    schema_name: str
    table_name: str
    columns: Dict[str, ColumnMetadata]
    comment: Optional[str] = None

    @property
    def full_name(self) -> str:
        if self.schema_name:
            return f"{self.schema_name}.{self.table_name}"
        return f"{self.database_name}.{self.table_name}"


@dataclass
class KeySummary:
    """Summary of business key coverage and integrity."""
    total_records: int
    distinct_keys: int
    null_keys: int
    duplicate_keys: int
    min_key: Optional[Any] = None
    max_key: Optional[Any] = None


class BaseConnector(ABC):
    """
    Abstract interface for database and data lake connectors.
    All query methods must be read-only and safe from SQL injection.
    """

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Verify connectivity and return latency and status."""
        pass

    @abstractmethod
    def get_databases_or_schemas(self) -> List[str]:
        """List available databases (Athena) or schemas (PostgreSQL)."""
        pass

    @abstractmethod
    def get_tables(self, schema_or_db: Optional[str] = None) -> List[str]:
        """List tables within the given schema or database."""
        pass

    @abstractmethod
    def get_table_schema(self, table_name: str, schema_or_db: Optional[str] = None) -> TableSchema:
        """Retrieve column names, data types, and nullability."""
        pass

    @abstractmethod
    def get_record_count(self, table_name: str, schema_or_db: Optional[str] = None) -> int:
        """Return the total number of records dynamically."""
        pass

    @abstractmethod
    def get_null_counts(
        self,
        table_name: str,
        columns: List[str],
        schema_or_db: Optional[str] = None
    ) -> Dict[str, int]:
        """Return count of NULL values for each column (distinguished from empty strings)."""
        pass

    @abstractmethod
    def get_aggregates(
        self,
        table_name: str,
        numeric_columns: List[str],
        schema_or_db: Optional[str] = None
    ) -> Dict[str, Dict[str, float]]:
        """
        Return numeric aggregates (sum, avg, min, max) for given columns.
        Format: {col_name: {'sum': ..., 'avg': ..., 'min': ..., 'max': ...}}
        """
        pass

    @abstractmethod
    def get_distinct_counts(
        self,
        table_name: str,
        columns: List[str],
        schema_or_db: Optional[str] = None
    ) -> Dict[str, int]:
        """Return COUNT(DISTINCT col) for each specified column."""
        pass

    @abstractmethod
    def get_key_summary(
        self,
        table_name: str,
        key_columns: List[str],
        schema_or_db: Optional[str] = None
    ) -> KeySummary:
        """Evaluate key nullability, uniqueness, and range."""
        pass

    @abstractmethod
    def fetch_records_batch(
        self,
        table_name: str,
        columns: List[str],
        key_columns: List[str],
        batch_size: int,
        last_key: Optional[Any] = None,
        schema_or_db: Optional[str] = None
    ) -> Tuple[pd.DataFrame, Optional[Any]]:
        """
        Stream records in deterministic order using keyset pagination.
        Returns: (batch_dataframe, next_cursor_value)
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Cleanly close all connections and pools."""
        pass
