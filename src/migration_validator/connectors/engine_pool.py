"""
Thread-safe SQLAlchemy Engine Pool and Connection Lifecycle Manager.
Reuses database engines across requests and Streamlit reruns,
enforcing connection limits, timeouts, and health pre-pings.
"""

import hashlib
import threading
from typing import Any, Dict, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

from migration_validator.logging_config import get_logger

logger = get_logger("connectors.engine_pool")


class EnginePool:
    """
    Singleton connection manager that creates, pools, and reuses SQLAlchemy engines.
    Prevents connection churn and exhaustion across Streamlit reruns and concurrent jobs.
    """

    _instance: Optional["EnginePool"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._engines: Dict[str, Engine] = {}
        self._engine_locks: Dict[str, threading.Lock] = {}
        self._stats: Dict[str, Dict[str, Any]] = {}
        self._pool_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "EnginePool":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @staticmethod
    def generate_engine_key(
        connection_url: str,
        pool_size: int = 5,
        max_overflow: int = 10,
        recycle_seconds: int = 300,
    ) -> str:
        """
        Generate a deterministic cache key for an engine configuration.
        Masks password to ensure secrets are never stored in key strings.
        """
        # Create a stable hash based on connection parameters
        raw = f"{connection_url}::pool={pool_size}::overflow={max_overflow}::recycle={recycle_seconds}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def get_engine(
        self,
        connection_url: str,
        pool_size: int = 5,
        max_overflow: int = 10,
        connect_timeout: int = 10,
        recycle_seconds: int = 300,
        pool_pre_ping: bool = True,
    ) -> Engine:
        """
        Retrieve an existing engine or create a new pooled SQLAlchemy Engine.
        Thread-safe and idempotent.
        """
        key = self.generate_engine_key(connection_url, pool_size, max_overflow, recycle_seconds)

        with self._pool_lock:
            if key not in self._engine_locks:
                self._engine_locks[key] = threading.Lock()
            engine_lock = self._engine_locks[key]

        with engine_lock:
            if key in self._engines:
                self._stats[key]["reused_count"] += 1
                logger.debug("Reusing pooled SQLAlchemy engine for key=%s (reuses=%d)", key, self._stats[key]["reused_count"])
                return self._engines[key]

            logger.info("Initializing new pooled SQLAlchemy engine for key=%s (pool_size=%d, max_overflow=%d)", key, pool_size, max_overflow)

            connect_args: Dict[str, Any] = {}
            if "postgresql" in connection_url or "postgres" in connection_url:
                connect_args["connect_timeout"] = connect_timeout

            engine = create_engine(
                connection_url,
                poolclass=QueuePool,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_recycle=recycle_seconds,
                pool_pre_ping=pool_pre_ping,
                connect_args=connect_args,
            )

            with self._pool_lock:
                self._engines[key] = engine
                self._stats[key] = {
                    "created_at": threading.get_ident(),
                    "reused_count": 0,
                    "pool_size": pool_size,
                    "max_overflow": max_overflow,
                }

            return engine

    def get_stats(self) -> Dict[str, Any]:
        """Return engine pool statistics."""
        with self._pool_lock:
            return {
                "active_engines": len(self._engines),
                "details": {
                    k: {
                        "reused_count": v["reused_count"],
                        "pool_size": v["pool_size"],
                        "max_overflow": v["max_overflow"],
                    }
                    for k, v in self._stats.items()
                },
            }

    def dispose_engine(self, connection_url: str) -> None:
        """Dispose and remove a specific engine from the pool."""
        key = self.generate_engine_key(connection_url)
        with self._pool_lock:
            engine = self._engines.pop(key, None)
            self._stats.pop(key, None)
            self._engine_locks.pop(key, None)

        if engine is not None:
            logger.info("Disposing pooled SQLAlchemy engine for key=%s", key)
            engine.dispose()

    def dispose_all(self) -> None:
        """Dispose all active engines across the application."""
        with self._pool_lock:
            engines = list(self._engines.values())
            self._engines.clear()
            self._stats.clear()
            self._engine_locks.clear()

        for engine in engines:
            try:
                engine.dispose()
            except Exception as e:
                logger.warning("Error disposing engine: %s", e)
        logger.info("All pooled SQLAlchemy engines disposed.")


def get_engine_pool() -> EnginePool:
    """Helper to access the singleton EnginePool."""
    return EnginePool.get_instance()
