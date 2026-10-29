"""Connector registry and support matrix."""
from __future__ import annotations

from enum import Enum
from typing import Type

from data_validator.config.models import EngineType, ProviderType
from data_validator.connectors.base import BaseConnector
from data_validator.connectors.synthetic import SyntheticConnector


class ConnectorStatus(str, Enum):
    IMPLEMENTED = "Implemented"
    TESTED = "Tested"
    PLANNED = "Planned"
    UNAVAILABLE = "Unavailable"


# Status metadata for all provider/engine combinations
CONNECTOR_SUPPORT_MATRIX: dict[tuple[str, str], ConnectorStatus] = {
    ("aws", "athena"): ConnectorStatus.IMPLEMENTED,
    ("aws", "postgresql"): ConnectorStatus.IMPLEMENTED,
    ("other", "postgresql"): ConnectorStatus.IMPLEMENTED,
    ("other", "synthetic"): ConnectorStatus.IMPLEMENTED,
    ("aws", "mysql"): ConnectorStatus.PLANNED,
    ("other", "mysql"): ConnectorStatus.PLANNED,
    ("azure", "mssql"): ConnectorStatus.PLANNED,
    ("other", "mssql"): ConnectorStatus.PLANNED,
    ("alibaba", "maxcompute"): ConnectorStatus.PLANNED,
    ("gcp", "postgresql"): ConnectorStatus.IMPLEMENTED,
}


def get_connector_status(provider: str, engine: str) -> ConnectorStatus:
    p = provider.lower()
    e = engine.lower()
    return CONNECTOR_SUPPORT_MATRIX.get((p, e), ConnectorStatus.PLANNED)


def is_connector_available(provider: str, engine: str) -> bool:
    return get_connector_status(provider, engine) in (
        ConnectorStatus.IMPLEMENTED,
        ConnectorStatus.TESTED,
    )


def list_available_engines_for_provider(provider: str) -> list[str]:
    p = provider.lower()
    engines = []
    for (prov, eng), status in CONNECTOR_SUPPORT_MATRIX.items():
        if prov == p:
            engines.append(eng)
    return sorted(list(set(engines)))


def get_connector_class(provider: str, engine: str) -> Type[BaseConnector]:
    """Resolve the connector class for a provider and engine."""
    e = engine.lower()

    if e == "synthetic":
        return SyntheticConnector

    if e == "postgresql":
        from data_validator.connectors.postgresql import PostgreSQLConnector
        return PostgreSQLConnector

    if e == "athena":
        from data_validator.connectors.aws_athena import AWSAthenaConnector
        return AWSAthenaConnector

    raise NotImplementedError(
        f"Connector for '{provider}' / '{engine}' is currently Planned/Unavailable. "
        "Only Athena, PostgreSQL, and Synthetic connectors are implemented."
    )


def create_connector(
    provider: str, engine: str, connection_id: str, config: dict
) -> BaseConnector:
    """Instantiate a connector with resolved configuration."""
    cls = get_connector_class(provider, engine)
    return cls(connection_id=connection_id, config=config)
