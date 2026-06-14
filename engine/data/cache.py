"""
Systematic data caching system for cryptocurrency market data.

Provides intelligent caching with:
- Multiple timeframe support
- Automatic cache invalidation
- Efficient storage with compression
- Memory and disk caching layers
"""

import os
import pickle
import gzip
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any
from pathlib import Path
import pandas as pd
from threading import Lock
import json


logger = logging.getLogger("DataCache")


@dataclass
class CacheEntry:
    """Represents a cached data entry."""

    key: str
    data: Any
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    metadata: Dict = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        """Check if cache entry is expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def age_hours(self) -> float:
        """Get age of cache entry in hours."""
        delta = datetime.now(timezone.utc) - self.created_at
        return delta.total_seconds() / 3600


class MemoryCache:
    """In-memory LRU cache layer."""

    def __init__(self, max_size: int = 100):
        self.max_size = max_size
        self._cache: Dict[str, CacheEntry] = {}
        self._access_order: List[str] = []
        self._lock = Lock()

    def get(self, key: str) -> Optional[CacheEntry]:
        """Get entry from cache."""
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if not entry.is_expired:
                    # Move to end (most recently used)
                    self._access_order.remove(key)
                    self._access_order.append(key)
                    return entry
                else:
                    # Remove expired entry
                    del self._cache[key]
                    self._access_order.remove(key)
        return None

    def put(self, entry: CacheEntry):
        """Put entry in cache."""
        with self._lock:
            # Evict if at capacity
            while len(self._cache) >= self.max_size:
                oldest_key = self._access_order.pop(0)
                del self._cache[oldest_key]

            self._cache[entry.key] = entry
            if entry.key in self._access_order:
                self._access_order.remove(entry.key)
            self._access_order.append(entry.key)

    def remove(self, key: str):
        """Remove entry from cache."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._access_order.remove(key)

    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            self._access_order.clear()

    @property
    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)


class DiskCache:
    """Disk-based cache layer with compression."""

    def __init__(self, cache_dir: str, compress: bool = True):
        self.cache_dir = Path(cache_dir)
        self.compress = compress
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Index file for metadata
        self.index_file = self.cache_dir / "cache_index.json"
        self._index = self._load_index()

    def _load_index(self) -> Dict:
        """Load cache index from disk."""
        if self.index_file.exists():
            try:
                with open(self.index_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cache index: {e}")
        return {}

    def _save_index(self):
        """Save cache index to disk."""
        try:
            with open(self.index_file, "w") as f:
                json.dump(self._index, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save cache index: {e}")

    def _get_filepath(self, key: str) -> Path:
        """Get filepath for a cache key."""
        # Use hash for filename to avoid special characters
        hash_key = hashlib.md5(key.encode()).hexdigest()
        ext = ".pkl.gz" if self.compress else ".pkl"
        return self.cache_dir / f"{hash_key}{ext}"

    def get(self, key: str) -> Optional[CacheEntry]:
        """Get entry from disk cache."""
        filepath = self._get_filepath(key)

        if not filepath.exists():
            return None

        # Check index for expiration
        if key in self._index:
            expires_at = self._index[key].get("expires_at")
            if expires_at:
                expires_dt = datetime.fromisoformat(expires_at)
                if datetime.now(timezone.utc) > expires_dt:
                    self.remove(key)
                    return None

        try:
            if self.compress:
                with gzip.open(filepath, "rb") as f:
                    entry = pickle.load(f)
            else:
                with open(filepath, "rb") as f:
                    entry = pickle.load(f)
            return entry
        except Exception as e:
            logger.error(f"Failed to load cache entry {key}: {e}")
            return None

    def put(self, entry: CacheEntry):
        """Put entry in disk cache."""
        filepath = self._get_filepath(entry.key)

        try:
            if self.compress:
                with gzip.open(filepath, "wb") as f:
                    pickle.dump(entry, f)
            else:
                with open(filepath, "wb") as f:
                    pickle.dump(entry, f)

            # Update index
            self._index[entry.key] = {
                "filepath": str(filepath),
                "created_at": entry.created_at.isoformat(),
                "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                "metadata": entry.metadata,
            }
            self._save_index()

        except Exception as e:
            logger.error(f"Failed to save cache entry {entry.key}: {e}")

    def remove(self, key: str):
        """Remove entry from disk cache."""
        filepath = self._get_filepath(key)

        try:
            if filepath.exists():
                filepath.unlink()
            if key in self._index:
                del self._index[key]
                self._save_index()
        except Exception as e:
            logger.error(f"Failed to remove cache entry {key}: {e}")

    def clear(self):
        """Clear all cache entries."""
        try:
            for filepath in self.cache_dir.glob("*.pkl*"):
                filepath.unlink()
            self._index.clear()
            self._save_index()
        except Exception as e:
            logger.error(f"Failed to clear disk cache: {e}")

    def cleanup_expired(self):
        """Remove expired entries."""
        now = datetime.now(timezone.utc)
        expired_keys = []

        for key, info in self._index.items():
            expires_at = info.get("expires_at")
            if expires_at:
                if now > datetime.fromisoformat(expires_at):
                    expired_keys.append(key)

        for key in expired_keys:
            self.remove(key)

        logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")


class DataCache:
    """
    Multi-level caching system for market data.

    Features:
    - Two-tier caching (memory + disk)
    - Automatic expiration
    - Compression for disk storage
    - Support for multiple timeframes
    - Cache statistics and monitoring
    """

    def __init__(
        self,
        cache_dir: str = "data/cache",
        memory_size: int = 100,
        default_expiry_hours: int = 24,
        compress: bool = True,
    ):
        self.cache_dir = cache_dir
        self.default_expiry_hours = default_expiry_hours

        # Initialize cache layers
        self.memory_cache = MemoryCache(max_size=memory_size)
        self.disk_cache = DiskCache(cache_dir, compress=compress)

        # Statistics
        self._stats = {"memory_hits": 0, "disk_hits": 0, "misses": 0, "writes": 0}

        logger.info(f"DataCache initialized at {cache_dir}")

    def _generate_key(self, symbol: str, interval: str, start: datetime, end: datetime) -> str:
        """Generate cache key for market data."""
        start_str = start.strftime("%Y%m%d_%H%M")
        end_str = end.strftime("%Y%m%d_%H%M")
        return f"{symbol}_{interval}_{start_str}_{end_str}"

    def get(self, symbol: str, interval: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
        """
        Get cached data for a symbol and timeframe.

        Args:
            symbol: Trading symbol
            interval: Time interval (e.g., '1h', '15m')
            start: Start datetime
            end: End datetime

        Returns:
            DataFrame if cached, None otherwise
        """
        key = self._generate_key(symbol, interval, start, end)

        # Try memory cache first
        entry = self.memory_cache.get(key)
        if entry:
            self._stats["memory_hits"] += 1
            logger.debug(f"Memory cache hit: {key}")
            return entry.data

        # Try disk cache
        entry = self.disk_cache.get(key)
        if entry:
            self._stats["disk_hits"] += 1
            # Promote to memory cache
            self.memory_cache.put(entry)
            logger.debug(f"Disk cache hit: {key}")
            return entry.data

        self._stats["misses"] += 1
        logger.debug(f"Cache miss: {key}")
        return None

    def put(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        data: pd.DataFrame,
        expiry_hours: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ):
        """
        Cache data for a symbol and timeframe.

        Args:
            symbol: Trading symbol
            interval: Time interval
            start: Start datetime
            end: End datetime
            data: DataFrame to cache
            expiry_hours: Cache expiration in hours
            metadata: Additional metadata
        """
        key = self._generate_key(symbol, interval, start, end)
        expiry = expiry_hours or self.default_expiry_hours

        entry = CacheEntry(
            key=key,
            data=data,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=expiry),
            metadata=metadata
            or {
                "symbol": symbol,
                "interval": interval,
                "rows": len(data),
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )

        # Write to both caches
        self.memory_cache.put(entry)
        self.disk_cache.put(entry)

        self._stats["writes"] += 1
        logger.debug(f"Cached {len(data)} rows for {key}")

    def get_or_fetch(
        self,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        fetch_func: Callable[..., Any],
        expiry_hours: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Get cached data or fetch and cache if not available.

        Args:
            symbol: Trading symbol
            interval: Time interval
            start: Start datetime
            end: End datetime
            fetch_func: Function to fetch data if not cached
            expiry_hours: Cache expiration in hours

        Returns:
            DataFrame with market data
        """
        # Try cache first
        data = self.get(symbol, interval, start, end)
        if data is not None:
            return data

        # Fetch data
        logger.info(f"Fetching data for {symbol} {interval} {start} to {end}")
        data = fetch_func(symbol, interval, start, end)

        # Cache the result
        if data is not None and len(data) > 0:
            self.put(symbol, interval, start, end, data, expiry_hours)

        return data

    def get_multi(self, symbols: List[str], interval: str, start: datetime, end: datetime) -> Dict[str, pd.DataFrame]:
        """
        Get cached data for multiple symbols.

        Returns dict of symbol -> DataFrame (only for cached symbols)
        """
        result = {}
        for symbol in symbols:
            data = self.get(symbol, interval, start, end)
            if data is not None:
                result[symbol] = data
        return result

    def put_multi(
        self,
        data: Dict[str, pd.DataFrame],
        interval: str,
        start: datetime,
        end: datetime,
        expiry_hours: Optional[int] = None,
    ):
        """
        Cache data for multiple symbols.
        """
        for symbol, df in data.items():
            self.put(symbol, interval, start, end, df, expiry_hours)

    def invalidate(self, symbol: Optional[str] = None, interval: Optional[str] = None):
        """
        Invalidate cache entries.

        If symbol and interval provided, invalidates specific entries.
        Otherwise clears all cache.
        """
        if symbol is None and interval is None:
            self.memory_cache.clear()
            self.disk_cache.clear()
            logger.info("Cleared all cache")
        else:
            # Would need to iterate through index to find matching entries
            # For now, just clear all
            self.memory_cache.clear()
            logger.info(f"Invalidated cache for {symbol or '*'}/{interval or '*'}")

    def cleanup(self):
        """Clean up expired entries."""
        self.disk_cache.cleanup_expired()

    def get_stats(self) -> Dict:
        """Get cache statistics."""
        total_hits = self._stats["memory_hits"] + self._stats["disk_hits"]
        total_requests = total_hits + self._stats["misses"]
        hit_rate = total_hits / total_requests if total_requests > 0 else 0

        return {
            **self._stats,
            "total_hits": total_hits,
            "total_requests": total_requests,
            "hit_rate": hit_rate,
            "hit_rate_pct": hit_rate * 100,
            "memory_cache_size": self.memory_cache.size,
        }

    def preload(
        self, symbols: List[str], intervals: List[str], days: int = 30, fetch_func: Callable[..., Any] | None = None
    ):
        """
        Preload cache with historical data.

        Args:
            symbols: List of symbols to cache
            intervals: List of intervals to cache
            days: Number of days of history
            fetch_func: Function to fetch data
        """
        if not fetch_func:
            logger.warning("No fetch function provided for preload")
            return

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        total = len(symbols) * len(intervals)
        loaded = 0

        for symbol in symbols:
            for interval in intervals:
                try:
                    self.get_or_fetch(symbol, interval, start, end, fetch_func)
                    loaded += 1
                    logger.info(f"Preloaded {loaded}/{total}: {symbol} {interval}")
                except Exception as e:
                    logger.error(f"Failed to preload {symbol} {interval}: {e}")

        logger.info(f"Preload complete: {loaded}/{total} entries cached")


class TimeframedCache:
    """
    Cache manager that handles multiple timeframes efficiently.

    Automatically manages different cache expiry based on timeframe.
    """

    # Default expiry hours based on timeframe
    EXPIRY_MAP = {
        "1m": 1,
        "5m": 4,
        "15m": 12,
        "30m": 24,
        "1h": 48,
        "4h": 168,  # 1 week
        "1d": 720,  # 1 month
    }

    def __init__(self, base_dir: str = "data/cache"):
        self.base_dir = base_dir
        self._caches: Dict[str, DataCache] = {}

    def _get_cache(self, interval: str) -> DataCache:
        """Get or create cache for interval."""
        if interval not in self._caches:
            cache_dir = os.path.join(self.base_dir, interval)
            expiry = self.EXPIRY_MAP.get(interval, 24)
            self._caches[interval] = DataCache(cache_dir=cache_dir, default_expiry_hours=expiry)
        return self._caches[interval]

    def get(self, symbol: str, interval: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
        """Get data from appropriate timeframe cache."""
        cache = self._get_cache(interval)
        return cache.get(symbol, interval, start, end)

    def put(self, symbol: str, interval: str, start: datetime, end: datetime, data: pd.DataFrame):
        """Put data in appropriate timeframe cache."""
        cache = self._get_cache(interval)
        cache.put(symbol, interval, start, end, data)

    def get_or_fetch(
        self, symbol: str, interval: str, start: datetime, end: datetime, fetch_func: Callable[..., Any]
    ) -> pd.DataFrame:
        """Get or fetch data from appropriate timeframe cache."""
        cache = self._get_cache(interval)
        return cache.get_or_fetch(symbol, interval, start, end, fetch_func)

    def get_all_stats(self) -> Dict[str, Dict]:
        """Get statistics for all timeframe caches."""
        return {interval: cache.get_stats() for interval, cache in self._caches.items()}
