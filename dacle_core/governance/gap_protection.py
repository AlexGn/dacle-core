"""Live-tick gap protection.

Subscribes to a fresh price feed (NOT the 4H cipher cache, which is too slow).
Maintains a rolling window per market; when price moves >threshold within the
window, emit a Redis flag that the SovereignWrapper consumes to reject new entries.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

from dacle_core.governance.contracts import KEY_SOVEREIGN_GAP_PROTECTION

logger = logging.getLogger(__name__)


class GapProtector:
    """Detects sharp price moves within a rolling window per market.

    On gap: sets a Redis flag with TTL (so the flag self-clears) and emits a
    critical log line. SovereignWrapper.validate_intent() reads the flag.
    """

    def __init__(
        self,
        threshold_pct: float = 0.03,
        window_seconds: int = 300,
        redis: Optional[Any] = None,
        redis_flag_ttl_sec: int = 600,
        flag_key: str = KEY_SOVEREIGN_GAP_PROTECTION,
    ):
        if threshold_pct <= 0:
            raise ValueError("threshold_pct must be > 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.threshold_pct = float(threshold_pct)
        self.window_seconds = int(window_seconds)
        self.redis = redis
        self.redis_flag_ttl_sec = int(redis_flag_ttl_sec)
        self.flag_key = flag_key
        self._windows: Dict[str, Deque[Tuple[float, float]]] = {}
        self._lock = threading.Lock()
        self._local_active_until: float = 0.0

    def _prune(self, dq: Deque[Tuple[float, float]], now: float) -> None:
        cutoff = now - self.window_seconds
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def feed_tick(self, market_id: str, price: float, ts: Optional[float] = None) -> bool:
        """Append a tick. Returns True if a gap is detected.

        Idempotent: detection during this call also sets the Redis flag (best-effort, sync API).
        """
        if price <= 0:
            return False
        now = float(ts if ts is not None else time.time())
        with self._lock:
            dq = self._windows.setdefault(market_id, deque(maxlen=4096))
            dq.append((now, float(price)))
            self._prune(dq, now)
            if len(dq) < 2:
                return False
            prices = [p for _, p in dq]
            lo = min(prices)
            hi = max(prices)
            if lo <= 0:
                return False
            move = (hi - lo) / lo
            if move >= self.threshold_pct:
                self._local_active_until = now + self.redis_flag_ttl_sec
                logger.critical(
                    "GAP DETECTED market=%s move=%.4f threshold=%.4f window=%ds",
                    market_id, move, self.threshold_pct, self.window_seconds,
                )
                self._set_redis_flag(market_id, move)
                return True
            return False

    def _set_redis_flag(self, market_id: str, move: float) -> None:
        """Best-effort Redis flag set. Sync — called from feed_tick."""
        if self.redis is None:
            return
        try:
            payload = {"market_id": market_id, "move": move, "ts": time.time()}
            # Sync set — if redis is aioredis, this will be a coroutine we can't await here.
            # Use a fire-and-forget pattern for sync redis clients.
            if hasattr(self.redis, "set"):
                self.redis.set(self.flag_key, json.dumps(payload), ex=self.redis_flag_ttl_sec)
        except Exception as e:
            logger.error("Gap protector Redis flag set failed: %s", e)

    async def is_gap_active(self) -> bool:
        """Check Redis flag. Falls back to local in-memory check."""
        if self.redis is not None:
            try:
                val = await self.redis.get(self.flag_key)
                if val is not None:
                    return True
            except Exception as e:
                logger.error("Gap protector Redis check failed: %s", e)
        return self.is_gap_active_local()

    def is_gap_active_local(self) -> bool:
        """In-memory check only. Use when Redis is unavailable."""
        return time.time() < self._local_active_until