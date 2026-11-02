"""
Persistent Metadata Caching Layer with SQLite backend.
Eliminates redundant system catalog and information schema queries across Streamlit reruns.
Provides TTL expiration, manual invalidation, and metrics without caching credentials.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, Optional

from migration_validator.connectors.base import ColumnMetadata, TableSchema
from migration_validator.logging_config import get_logger

logger = get_logger("cache.metadata")


def generate_connection_fingerprint(
    service_type: str,
    host: Optional[str] = None,
    port: Optional[int] = None,
    database: Optional[str] = None,
    user: Optional[str] = None,
    region: Optional[str] = None,
    config_version: str = "v1",
) -> str:
    """
    Generate a deterministic, secure fingerprint identifying a connection configuration.
    Deliberately excludes passwords and secrets to ensure they are never recorded.
    """
    elements = [
        service_type.upper(),
        str(host or "").strip().lower(),
        str(port or ""),
        str(database or "").strip().lower(),
        str(user or "").strip().lower(),
        str(region or "").strip().lower(),
        config_version,
    ]
    raw_str = "|".join(elements)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:24]


class BaseMetadataCache(ABC):
    """Abstract interface for metadata persistence providers (SQLite, S3 snapshots, Redis)."""

    @abstractmethod
    def get(self, fingerprint: str, category: str, key: str) -> Optional[Any]:
        """Retrieve a cached entry if present and not expired."""
        pass

    @abstractmethod
    def set(
        self,
        fingerprint: str,
        category: str,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Store an entry in the persistent cache with optional TTL."""
        pass

    @abstractmethod
    def invalidate(
        self,
        fingerprint: Optional[str] = None,
        category: Optional[str] = None,
        key: Optional[str] = None,
    ) -> int:
        """Invalidate entries matching criteria. Returns count of invalidated entries."""
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """Return cache health and hit/miss statistics."""
        pass

    @abstractmethod
    def clear_expired(self) -> int:
        """Purge stale cache entries."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close cache resources cleanly."""
        pass


class SQLiteMetadataCache(BaseMetadataCache):
    """
    Local persistent SQLite metadata cache.
    Thread-safe, durable across application restarts, with WAL-mode concurrency.
    """

    def __init__(
        self,
        cache_db_path: Optional[str] = None,
        default_ttl_seconds: int = 3600,
    ):
        if cache_db_path is None:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".cache"))
            os.makedirs(base_dir, exist_ok=True)
            cache_db_path = os.path.join(base_dir, "metadata_cache.db")
        else:
            parent = os.path.dirname(os.path.abspath(cache_db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)

        self.db_path = cache_db_path
        self.default_ttl = default_ttl_seconds
        self._lock = threading.Lock()

        # Operational metrics
        self._stats = {
            "hits": 0,
            "misses": 0,
            "writes": 0,
            "invalidations": 0,
            "evictions": 0,
        }

        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS metadata_entries (
                        fingerprint TEXT NOT NULL,
                        category TEXT NOT NULL,
                        cache_key TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        last_accessed_at REAL NOT NULL,
                        hit_count INTEGER DEFAULT 0,
                        PRIMARY KEY (fingerprint, category, cache_key)
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_metadata_expiry
                    ON metadata_entries (expires_at);
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_metadata_category
                    ON metadata_entries (fingerprint, category);
                """)
                conn.commit()
            finally:
                conn.close()

    def get(self, fingerprint: str, category: str, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.execute(
                    """
                    SELECT payload_json, expires_at, hit_count
                    FROM metadata_entries
                    WHERE fingerprint = ? AND category = ? AND cache_key = ?
                    """,
                    (fingerprint, category, key),
                )
                row = cur.fetchone()
                if row is None:
                    self._stats["misses"] += 1
                    return None

                if row["expires_at"] < now:
                    # Expired
                    conn.execute(
                        "DELETE FROM metadata_entries WHERE fingerprint = ? AND category = ? AND cache_key = ?",
                        (fingerprint, category, key),
                    )
                    conn.commit()
                    self._stats["misses"] += 1
                    self._stats["evictions"] += 1
                    return None

                # Hit
                new_hits = row["hit_count"] + 1
                conn.execute(
                    """
                    UPDATE metadata_entries
                    SET last_accessed_at = ?, hit_count = ?
                    WHERE fingerprint = ? AND category = ? AND cache_key = ?
                    """,
                    (now, new_hits, fingerprint, category, key),
                )
                conn.commit()
                self._stats["hits"] += 1
                return json.loads(row["payload_json"])
            finally:
                conn.close()

    def set(
        self,
        fingerprint: str,
        category: str,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        now = time.time()
        expires_at = now + ttl
        payload = json.dumps(value, default=str)

        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            try:
                conn.execute(
                    """
                    INSERT INTO metadata_entries (
                        fingerprint, category, cache_key, payload_json,
                        created_at, expires_at, last_accessed_at, hit_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    ON CONFLICT(fingerprint, category, cache_key) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at,
                        last_accessed_at = excluded.last_accessed_at;
                    """,
                    (fingerprint, category, key, payload, now, expires_at, now),
                )
                conn.commit()
                self._stats["writes"] += 1
            finally:
                conn.close()

    def invalidate(
        self,
        fingerprint: Optional[str] = None,
        category: Optional[str] = None,
        key: Optional[str] = None,
    ) -> int:
        clauses = []
        params = []
        if fingerprint:
            clauses.append("fingerprint = ?")
            params.append(fingerprint)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if key:
            clauses.append("cache_key = ?")
            params.append(key)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"DELETE FROM metadata_entries {where};"

        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            try:
                cur = conn.execute(sql, tuple(params))
                count = cur.rowcount
                conn.commit()
                self._stats["invalidations"] += count
                return count
            finally:
                conn.close()

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.execute("SELECT COUNT(*) AS total_entries FROM metadata_entries;")
                total = cur.fetchone()["total_entries"]

                cur = conn.execute("SELECT MIN(created_at) AS oldest, MAX(created_at) AS newest FROM metadata_entries;")
                time_row = cur.fetchone()
            finally:
                conn.close()

            total_lookups = self._stats["hits"] + self._stats["misses"]
            hit_ratio = (self._stats["hits"] / total_lookups) if total_lookups > 0 else 0.0

            oldest_str = datetime.fromtimestamp(time_row["oldest"]).isoformat() if time_row["oldest"] else None
            newest_str = datetime.fromtimestamp(time_row["newest"]).isoformat() if time_row["newest"] else None

            return {
                "backend": "SQLiteMetadataCache",
                "db_path": self.db_path,
                "cached_entries": total,
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "writes": self._stats["writes"],
                "invalidations": self._stats["invalidations"],
                "evictions": self._stats["evictions"],
                "hit_ratio": round(hit_ratio, 4),
                "oldest_entry": oldest_str,
                "newest_entry": newest_str,
            }

    def clear_expired(self) -> int:
        now = time.time()
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            try:
                cur = conn.execute("DELETE FROM metadata_entries WHERE expires_at < ?;", (now,))
                count = cur.rowcount
                conn.commit()
                self._stats["evictions"] += count
                return count
            finally:
                conn.close()

    def close(self) -> None:
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path, timeout=30.0)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                conn.close()
            except Exception:
                pass


# Global singleton cache instance
_global_cache: Optional[BaseMetadataCache] = None
_cache_init_lock = threading.Lock()


def get_metadata_cache(cache_path: Optional[str] = None) -> BaseMetadataCache:
    """Retrieve the application-wide metadata cache singleton."""
    global _global_cache
    if _global_cache is None:
        with _cache_init_lock:
            if _global_cache is None:
                _global_cache = SQLiteMetadataCache(cache_path)
    return _global_cache


# Serialization helpers for TableSchema
def serialize_table_schema(schema: TableSchema) -> Dict[str, Any]:
    """Convert TableSchema dataclass to JSON-serializable dictionary."""
    return {
        "database_name": schema.database_name,
        "schema_name": schema.schema_name,
        "table_name": schema.table_name,
        "comment": schema.comment,
        "columns": {col_name: asdict(col_meta) for col_name, col_meta in schema.columns.items()},
    }


def deserialize_table_schema(data: Dict[str, Any]) -> TableSchema:
    """Reconstruct TableSchema dataclass from dictionary."""
    columns = {
        col_name: ColumnMetadata(**col_dict)
        for col_name, col_dict in data.get("columns", {}).items()
    }
    return TableSchema(
        database_name=data.get("database_name", ""),
        schema_name=data.get("schema_name", ""),
        table_name=data.get("table_name", ""),
        columns=columns,
        comment=data.get("comment"),
    )
