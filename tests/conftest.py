"""
Pytest configuration and environment fixtures.
Checks connectivity for integration tests and provides reproducible mocks.
"""

import socket

import pytest

try:  # legacy UI engine dependencies are optional (pip install ".[ui]")
    import sqlalchemy  # noqa: F401

    import migration_validator.config  # noqa: F401
    _LEGACY_AVAILABLE = True
except ImportError:
    _LEGACY_AVAILABLE = False

# Without the legacy dependencies only the datavalidator suite is collected.
collect_ignore_glob = [] if _LEGACY_AVAILABLE else ["unit/*", "integration/*"]


def is_postgres_available() -> bool:
    """Check if PostgreSQL server is accepting TCP connections."""
    try:
        from migration_validator.config import load_config

        config = load_config()
        host = config.postgres.host
        port = config.postgres.port
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except Exception:
        return False


_PG_AVAILABLE = is_postgres_available()


def pytest_runtest_setup(item):
    """Automatically skip live integration tests if local PostgreSQL is unreachable."""
    if "integration" in item.keywords:
        if not _PG_AVAILABLE:
            pytest.skip(
                "Live PostgreSQL service is not reachable on localhost:5432. "
                "Start PostgreSQL or run 'docker compose up -d' to execute live integration tests."
            )
