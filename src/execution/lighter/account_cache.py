
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class LighterAccountCache:
    """Institutional-grade cache for Lighter account metadata.
    
    Implements strict invalidation rules to prevent stale data risks
    while reducing REST API pressure.
    """
    
    def __init__(self, redis_client, ttl_sec: int = 3600):
        self.redis = redis_client
        self.ttl_sec = ttl_sec
        self.prefix = "cache:lighter:account:"

    async def get_account_info(self, address: str, sentinel_window_sec: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Fetch account info from cache if valid and within sentinel window."""
        key = f"{self.prefix}{address}"
        try:
            raw = await self.redis.get(key)
            if not raw:
                return None
            
            data = json.loads(raw)
            
            # Sentinel Poll Enforcement: Force refresh if older than window
            if sentinel_window_sec:
                last_refresh_str = data.get("last_refresh")
                if last_refresh_str:
                    last_refresh = datetime.fromisoformat(last_refresh_str)
                    if (datetime.now(timezone.utc) - last_refresh).total_seconds() > sentinel_window_sec:
                        logger.debug(f"Cache sentinel trigger: forcing refresh for {address}")
                        return None
            
            return data
        except Exception as e:
            logger.warning(f"Account cache read failed: {e}")
            return None

    async def set_account_info(self, address: str, data: Dict[str, Any]):
        """Persist account info to cache with TTL."""
        key = f"{self.prefix}{address}"
        try:
            # Inject refresh timestamp for sentinel logic
            data["last_refresh"] = datetime.now(timezone.utc).isoformat()
            await self.redis.set(key, json.dumps(data), ex=self.ttl_sec)
        except Exception as e:
            logger.warning(f"Account cache write failed: {e}")

    async def invalidate(self, address: str, reason: str = "unknown"):
        """Explicitly invalidate a specific address cache."""
        key = f"{self.prefix}{address}"
        logger.info(f"Invalidating account cache for {address} (Reason: {reason})")
        try:
            await self.redis.delete(key)
        except Exception as e:
            logger.error(f"Failed to invalidate cache: {e}")

    async def handle_transport_error(self, address: str, status_code: int):
        """React to API errors by clearing cache if they imply auth/rate issues."""
        if status_code in (401, 403, 429):
            logger.warning(f"Transport error {status_code} detected for {address}. Clearing cache.")
            await self.invalidate(address, reason=f"transport_error_{status_code}")
            
    async def clear_all(self):
        """Emergency clear of all Lighter caches."""
        pattern = f"{self.prefix}*"
        deleted = 0
        try:
            scan_iter = getattr(self.redis, "scan_iter", None)
            if callable(scan_iter):
                keys = []
                async for key in scan_iter(match=pattern):
                    keys.append(key)
                if keys:
                    deleted = int(await self.redis.delete(*keys) or 0)
                logger.warning(f"Emergency cache clear executed via scan_iter: deleted={deleted}")
                return deleted

            scan = getattr(self.redis, "scan", None)
            if not callable(scan):
                logger.warning("Account cache clear_all skipped: redis client has no scan/scan_iter")
                return 0

            cursor = 0
            while True:
                cursor, keys = await scan(cursor=cursor, match=pattern, count=100)
                if keys:
                    deleted += int(await self.redis.delete(*keys) or 0)
                if cursor in (0, "0", b"0"):
                    break

            logger.warning(f"Emergency cache clear executed via scan: deleted={deleted}")
            return deleted
        except Exception as e:
            logger.error(f"Failed to clear all account cache entries: {e}")
            return 0
