import logging
from typing import Optional, Tuple
from uuid import uuid4

import redis

logger = logging.getLogger(__name__)


class ContextTokenStore:
    """Distributed one-time nonce store backed by Redis."""

    def __init__(self, redis_url: str, prefix: str = "exec_ctx_nonce:", timeout_ms: int = 50):
        self.redis_url = str(redis_url or "").strip()
        self.prefix = str(prefix or "exec_ctx_nonce:")
        timeout_ms = int(timeout_ms)
        timeout_ms = max(10, min(timeout_ms, 100))
        self.timeout_sec = timeout_ms / 1000.0
        self._redis: Optional[redis.Redis] = None

    def _client(self) -> Optional[redis.Redis]:
        if not self.redis_url:
            return None
        if self._redis is None:
            self._redis = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=self.timeout_sec,
                socket_connect_timeout=self.timeout_sec,
            )
        return self._redis

    def ensure_ready(self) -> Tuple[bool, str]:
        client = self._client()
        if client is None:
            return False, "UNAVAILABLE"
        probe_key = f"{self.prefix}probe:{uuid4().hex}"
        try:
            client.ping()
            client.set(probe_key, "ok", ex=5)
            probe_val = client.get(probe_key)
            client.delete(probe_key)
            if probe_val != "ok":
                return False, "UNAVAILABLE"
            return True, "OK"
        except redis.TimeoutError:
            return False, "TIMEOUT"
        except Exception as e:
            logger.warning("Context token store probe failed: %s", e)
            return False, "UNAVAILABLE"

    def consume_once(self, nonce: str, ttl_sec: int) -> Tuple[bool, str]:
        client = self._client()
        if client is None:
            return False, "UNAVAILABLE"
        raw_nonce = str(nonce or "").strip()
        if not raw_nonce:
            return False, "INVALID_NONCE"
        ttl = max(1, min(int(ttl_sec), 300))
        key = f"{self.prefix}{raw_nonce}"
        try:
            created = client.set(key, "1", nx=True, ex=ttl)
            if created:
                return True, "OK"
            return False, "REPLAYED"
        except redis.TimeoutError:
            return False, "TIMEOUT"
        except Exception as e:
            logger.warning("Context token consume_once failed: %s", e)
            return False, "UNAVAILABLE"
