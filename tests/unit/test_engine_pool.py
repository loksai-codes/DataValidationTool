"""
Unit tests for SQLAlchemy EnginePool and connection lifecycle management.
"""

from migration_validator.connectors.engine_pool import get_engine_pool


def test_engine_pool_singleton():
    pool1 = get_engine_pool()
    pool2 = get_engine_pool()
    assert pool1 is pool2


def test_engine_reuse_and_stats():
    pool = get_engine_pool()
    url = "sqlite:///:memory:"

    # First acquisition creates the engine
    engine1 = pool.get_engine(url)
    stats1 = pool.get_stats()
    assert stats1["active_engines"] >= 1

    # Second acquisition returns the same engine
    engine2 = pool.get_engine(url)
    assert engine1 is engine2

    stats2 = pool.get_stats()
    key = pool.generate_engine_key(url)
    assert stats2["details"][key]["reused_count"] >= 1


def test_engine_dispose():
    pool = get_engine_pool()
    url = "sqlite:///:memory:?cache=shared&uri=true"

    pool.get_engine(url)
    key = pool.generate_engine_key(url)
    assert key in pool.get_stats()["details"]

    pool.dispose_engine(url)
    assert key not in pool.get_stats()["details"]
