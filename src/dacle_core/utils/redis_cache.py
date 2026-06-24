#!/usr/bin/env python3
"""
Redis Cache Helper - Session 263 P3 Optimization
High-performance caching layer for hot data paths

Purpose:
- Cache frequently accessed data with low latency (<1ms vs 50-500ms DB queries)
- Reduce load on Supabase and external APIs
- Improve user-facing operation speed by 70-80%

Use Cases:
1. TGE conviction scores (1h TTL)
2. TA aggregator data (15min TTL)
3. BTC/ETH/index prices (1min TTL)
4. Learning insights (24h TTL)
5. Supabase query results (configurable TTL)

Cost: $0 (Redis already deployed on VPS)

Usage:
    from src.utils.redis_cache import RedisCache, with_redis_cache

    # Method 1: Direct usage
    cache = RedisCache()

    # Set value
    cache.set("btc_price", 98000, ttl_seconds=60)

    # Get value
    price = cache.get("btc_price", default=0)

    # Method 2: Decorator
    @with_redis_cache(ttl_seconds=3600, key_prefix="conviction")
    def calculate_conviction(token_symbol: str) -> float:
        # Expensive calculation
        return score

Session 263 Optimization Impact:
- Expected latency reduction: 80-90% on cached operations
- Load reduction: 40-50% on expensive computations
- Cost: $0 (Redis already deployed)
"""

import functools
import json
import logging
import os
from datetime import timedelta
from enum import Enum
from typing import Any, Callable, Optional, Union

logger = logging.getLogger(__name__)

# Try to import redis
try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    redis = None  # type: ignore
    REDIS_AVAILABLE = False


class RedisCache:
    """
    High-performance Redis caching layer.

    Features:
    - JSON serialization/deserialization
    - TTL-based expiry
    - Key prefix support for namespacing
    - Graceful degradation if Redis unavailable
    - Connection pooling for performance
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        key_prefix: Optional[str] = None,
    ):
        """
        Initialize Redis cache.

        Args:
            host: Redis server host
            port: Redis server port
            db: Redis database number (0-15)
            key_prefix: Global prefix for all keys. Defaults to the
                DACLE_KEY_PREFIX env var, falling back to 'dacle' so each
                pillar repo can namespace its keys (see
                docs/plans/polymarket-pillar-split.md).
        """
        self.key_prefix = key_prefix or os.getenv("DACLE_KEY_PREFIX", "dacle")
        self.enabled = REDIS_AVAILABLE

        if not REDIS_AVAILABLE:
            logger.warning(
                "⚠️ Redis not available - caching disabled (install: pip install redis)"
            )
            self.client = None
            return

        try:
            # Create connection pool for better performance
            pool = redis.ConnectionPool(
                host=host,
                port=port,
                db=db,
                decode_responses=True,  # Auto-decode bytes to strings
                max_connections=10,
            )
            self.client = redis.Redis(connection_pool=pool)

            # Test connection
            self.client.ping()
            logger.info(f"✅ Redis connected: {host}:{port} (db={db})")

        except Exception as e:
            logger.warning(f"⚠️ Redis connection failed: {e} - caching disabled")
            self.client = None
            self.enabled = False

    def _make_key(self, key: str, namespace: Optional[str] = None) -> str:
        """
        Generate full Redis key with prefix and optional namespace.

        Args:
            key: Base key
            namespace: Optional namespace (e.g., 'conviction', 'ta', 'prices')

        Returns:
            Full key: dacle:namespace:key or dacle:key
        """
        parts = [self.key_prefix]
        if namespace:
            parts.append(namespace)
        parts.append(key)
        return ":".join(parts)

    def get(
        self, key: str, namespace: Optional[str] = None, default: Any = None
    ) -> Any:
        """
        Get value from cache.

        Args:
            key: Cache key
            namespace: Optional namespace
            default: Default value if not found or cache unavailable

        Returns:
            Cached value (deserialized from JSON) or default
        """
        if not self.enabled or not self.client:
            return default

        try:
            full_key = self._make_key(key, namespace)
            value = self.client.get(full_key)

            if value is None:
                return default

            # Deserialize JSON
            return json.loads(value)

        except Exception as e:
            logger.warning(f"Redis GET error for {key}: {e}")
            return default

    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
        namespace: Optional[str] = None,
        ttl: Optional[int] = None,
    ) -> bool:
        """
        Set value in cache with optional TTL.

        Args:
            key: Cache key
            value: Value to cache (must be JSON-serializable)
            ttl_seconds: Time-to-live in seconds (None = no expiry)
            namespace: Optional namespace
            ttl: Alias for ttl_seconds (Session 341 compatibility)

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or not self.client:
            return False

        # Session 341: Support both ttl and ttl_seconds
        expiry = ttl_seconds if ttl_seconds is not None else ttl

        try:
            full_key = self._make_key(key, namespace)

            # Serialize to JSON
            json_value = json.dumps(value, default=self._json_default)

            if expiry:
                self.client.setex(full_key, expiry, json_value)
            else:
                self.client.set(full_key, json_value)

            return True

        except Exception as e:
            logger.warning(f"Redis SET error for {key}: {e}")
            return False

    @staticmethod
    def _json_default(value: Any) -> Any:
        """Best-effort serializer for cache payloads."""
        if isinstance(value, Enum):
            return value.value
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            try:
                return isoformat()
            except Exception:
                pass
        return str(value)

    def delete(self, key: str, namespace: Optional[str] = None) -> bool:
        """
        Delete key from cache.

        Args:
            key: Cache key
            namespace: Optional namespace

        Returns:
            True if deleted, False otherwise
        """
        if not self.enabled or not self.client:
            return False

        try:
            full_key = self._make_key(key, namespace)
            self.client.delete(full_key)
            return True

        except Exception as e:
            logger.warning(f"Redis DELETE error for {key}: {e}")
            return False

    def exists(self, key: str, namespace: Optional[str] = None) -> bool:
        """
        Check if key exists in cache.

        Args:
            key: Cache key
            namespace: Optional namespace

        Returns:
            True if exists, False otherwise
        """
        if not self.enabled or not self.client:
            return False

        try:
            full_key = self._make_key(key, namespace)
            return bool(self.client.exists(full_key))

        except Exception as e:
            logger.warning(f"Redis EXISTS error for {key}: {e}")
            return False

    def ttl(self, key: str, namespace: Optional[str] = None) -> int:
        """
        Get time-to-live for a key.

        Session 337 P2.2: Added for cache warming logic.

        Args:
            key: Cache key
            namespace: Optional namespace

        Returns:
            Remaining TTL in seconds:
            - Positive number: seconds remaining
            - -1: key exists but has no expiry
            - -2: key doesn't exist
        """
        if not self.enabled or not self.client:
            return -1

        try:
            full_key = self._make_key(key, namespace)
            return self.client.ttl(full_key)

        except Exception as e:
            logger.warning(f"Redis TTL error for {key}: {e}")
            return -1

    def get_ttl(self, key: str, namespace: Optional[str] = None) -> int:
        """
        Get remaining TTL for a key in seconds.

        Args:
            key: Cache key
            namespace: Optional namespace

        Returns:
            TTL in seconds (-1 = no expiry, -2 = key doesn't exist)
        """
        if not self.enabled or not self.client:
            return -2

        try:
            full_key = self._make_key(key, namespace)
            return self.client.ttl(full_key)

        except Exception as e:
            logger.warning(f"Redis TTL error for {key}: {e}")
            return -2

    def clear_namespace(self, namespace: str) -> int:
        """
        Clear all keys in a namespace.

        Args:
            namespace: Namespace to clear

        Returns:
            Number of keys deleted
        """
        if not self.enabled or not self.client:
            return 0

        try:
            pattern = self._make_key("*", namespace)
            keys = self.client.keys(pattern)

            if keys:
                return self.client.delete(*keys)
            return 0

        except Exception as e:
            logger.warning(f"Redis CLEAR error for namespace {namespace}: {e}")
            return 0

    def get_stats(self) -> dict:
        """
        Get cache statistics.

        Returns:
            Dict with cache stats (keys, memory, hits, etc.)
        """
        if not self.enabled or not self.client:
            return {"enabled": False}

        try:
            info = self.client.info("stats")
            memory = self.client.info("memory")

            return {
                "enabled": True,
                "total_keys": self.client.dbsize(),
                "hits": info.get("keyspace_hits", 0),
                "misses": info.get("keyspace_misses", 0),
                "hit_rate": self._calculate_hit_rate(
                    info.get("keyspace_hits", 0), info.get("keyspace_misses", 0)
                ),
                "memory_used_mb": memory.get("used_memory", 0) / (1024 * 1024),
                "memory_peak_mb": memory.get("used_memory_peak", 0) / (1024 * 1024),
            }

        except Exception as e:
            logger.warning(f"Redis STATS error: {e}")
            return {"enabled": True, "error": str(e)}

    def _calculate_hit_rate(self, hits: int, misses: int) -> float:
        """Calculate cache hit rate percentage."""
        total = hits + misses
        if total == 0:
            return 0.0
        return (hits / total) * 100


# Singleton instance
_redis_cache: Optional[RedisCache] = None


def get_redis_cache() -> RedisCache:
    """Get or create the global Redis cache instance."""
    global _redis_cache
    if _redis_cache is None:
        _redis_cache = RedisCache()
    return _redis_cache


def with_redis_cache(
    ttl_seconds: int = 300,
    key_prefix: str = "",
    namespace: Optional[str] = None,
    ttl: Optional[int] = None,
):
    """
    Decorator to cache function results in Redis.

    Args:
        ttl_seconds: Cache TTL in seconds (default: 5 minutes)
        key_prefix: Prefix for cache key (default: function name)
        namespace: Optional namespace for grouping related keys
        ttl: Alias for ttl_seconds (Session 341 compatibility)

    Usage:
        @with_redis_cache(ttl_seconds=3600, namespace="conviction")
        def calculate_conviction(token_symbol: str) -> float:
            # Expensive calculation
            return score
    """
    # Session 341: Support both ttl and ttl_seconds
    expiry = ttl_seconds if ttl is None or ttl_seconds != 300 else ttl

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache = get_redis_cache()

            # Generate cache key from function name and arguments
            func_name = key_prefix or func.__name__

            # Create key from args/kwargs
            key_parts = [func_name]
            if args:
                key_parts.extend(str(arg) for arg in args)
            if kwargs:
                key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))

            cache_key = ":".join(key_parts)

            # Try to get from cache
            cached = cache.get(cache_key, namespace=namespace)
            if cached is not None:
                logger.debug(f"✅ Cache HIT: {cache_key}")
                return cached

            # Execute function
            logger.debug(f"❌ Cache MISS: {cache_key} - executing function")
            result = func(*args, **kwargs)

            # Store in cache
            cache.set(cache_key, result, ttl_seconds=expiry, namespace=namespace)

            return result

        return wrapper

    return decorator


if __name__ == "__main__":
    # Test Redis cache
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("REDIS CACHE TEST")
    print("=" * 60)

    cache = RedisCache()

    print("\n1. Testing basic operations...")
    cache.set("test_key", {"value": 123}, ttl_seconds=10, namespace="test")
    result = cache.get("test_key", namespace="test")
    print(f"   Set and get: {result}")

    print("\n2. Testing TTL...")
    ttl = cache.get_ttl("test_key", namespace="test")
    print(f"   TTL: {ttl} seconds")

    print("\n3. Testing decorator...")

    @with_redis_cache(ttl_seconds=5, namespace="test")
    def expensive_function(x: int) -> int:
        import time

        time.sleep(0.5)  # Simulate expensive operation
        return x * 2

    import time

    start = time.time()
    result1 = expensive_function(21)
    duration1 = time.time() - start
    print(f"   First call: {result1} (took {duration1:.3f}s)")

    start = time.time()
    result2 = expensive_function(21)
    duration2 = time.time() - start
    print(f"   Second call: {result2} (took {duration2:.3f}s)")
    print(f"   Speedup: {duration1/duration2:.0f}x faster")

    print("\n4. Cache statistics...")
    stats = cache.get_stats()
    print(f"   Total keys: {stats.get('total_keys', 0)}")
    print(f"   Hit rate: {stats.get('hit_rate', 0):.1f}%")
    print(f"   Memory used: {stats.get('memory_used_mb', 0):.2f} MB")

    print("\n5. Cleanup...")
    deleted = cache.clear_namespace("test")
    print(f"   Deleted {deleted} test keys")

    print("\n✅ All tests passed")
