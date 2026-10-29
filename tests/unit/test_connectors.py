"""Unit tests for connectors and connector registry."""
from unittest.mock import MagicMock, patch
import pytest

from data_validator.connectors.aws_athena import AWSAthenaConnector
from data_validator.connectors.postgresql import PostgreSQLConnector
from data_validator.connectors.registry import (
    ConnectorStatus,
    get_connector_class,
    get_connector_status,
    is_connector_available,
)
from data_validator.connectors.synthetic import SyntheticConnector


def test_synthetic_connector():
    conn = SyntheticConnector("test_syn")
    test_res = conn.test_connection()
    assert test_res.success is True
    assert "Synthetic" in test_res.server_version

    conn.seed_table(
        "products",
        [
            {"sku": "A100", "price": 19.99, "stock": 50},
            {"sku": "B200", "price": 49.99, "stock": 10},
        ],
        primary_keys=["sku"],
    )

    tables = conn.list_tables()
    assert "products" in tables

    meta = conn.get_table_metadata("products")
    assert meta.table == "products"
    assert "sku" in meta.primary_keys
    assert len(meta.columns) == 3

    stats = conn.get_column_stats("products", "price")
    assert stats.min_value == 19.99
    assert stats.max_value == 49.99

    batches = list(conn.fetch_records_batch("products", batch_size=10))
    assert len(batches) == 1
    assert len(batches[0]) == 2


def test_connector_registry_status_matrix():
    assert get_connector_status("aws", "athena") == ConnectorStatus.IMPLEMENTED
    assert get_connector_status("aws", "postgresql") == ConnectorStatus.IMPLEMENTED
    assert get_connector_status("other", "synthetic") == ConnectorStatus.IMPLEMENTED

    # Planned engines
    assert get_connector_status("aws", "mysql") == ConnectorStatus.PLANNED
    assert get_connector_status("azure", "mssql") == ConnectorStatus.PLANNED
    assert get_connector_status("alibaba", "maxcompute") == ConnectorStatus.PLANNED

    assert is_connector_available("aws", "athena") is True
    assert is_connector_available("azure", "mssql") is False

    assert get_connector_class("aws", "athena") == AWSAthenaConnector
    assert get_connector_class("aws", "postgresql") == PostgreSQLConnector
    assert get_connector_class("other", "synthetic") == SyntheticConnector

    with pytest.raises(NotImplementedError):
        get_connector_class("azure", "mssql")


def test_postgresql_connector_mocked():
    conn = PostgreSQLConnector(
        "pg_mock",
        {
            "host": "localhost",
            "port": 5432,
            "database": "test_db",
            "username": "test_user",
        },
    )

    with patch("psycopg.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"version": "PostgreSQL 16.1"}

        res = conn.test_connection()
        assert res.success is True
        assert "PostgreSQL 16.1" in res.server_version


def test_athena_connector_mocked():
    conn = AWSAthenaConnector(
        "athena_mock",
        {
            "region_name": "ap-south-1",
            "workgroup": "primary",
            "s3_staging_dir": "s3://my-test-bucket/results/",
        },
    )

    with patch.object(conn, "_get_athena_client") as mock_client_fn:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client
        mock_client.get_work_group.return_value = {
            "WorkGroup": {"State": "ENABLED"}
        }

        res = conn.test_connection()
        assert res.success is True
        assert "ENABLED" in res.message
