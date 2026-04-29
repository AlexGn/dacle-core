"""
Rolling Sharpe Service.

Computes rolling Sharpe ratios (30d and 60d windows) from per-pillar PnL
data in the PnLAggregator. Feeds the CapitalAllocator for allocation decisions.

Sharpe = mean(daily_return) / std(daily_return) * sqrt(365)
    - Zero-volatility pillars get Sharpe = 0.0
    - Pillars with insufficient history (<5 days) return None

Redis cache: dacle:governance:sharpe:{window}d
    Expires after 6 hours (recomputed nightly or on demand).
"""

import json
import math
import time
from typing import Dict, Optional


KEY_SHARPE_PREFIX = "dacle:governance:sharpe"


class SharpeService:
    """Rolling Sharpe ratio calculator for per-pillar PnL streams."""

    def __init__(self, redis=None, pnl_aggregator=None):
        self._redis = redis
        self._pnl = pnl_aggregator

    async def compute_sharpe(self, pillar: str, window_days: int = 30) -> Optional[float]:
        """Compute rolling Sharpe for a pillar.

        Args:
            pillar: "lighter", "polymarket", or "swing"
            window_days: Lookback window (30 or 60 recommended).

        Returns:
            Sharpe ratio as float, or None if insufficient data.
        """
        if self._pnl is None:
            return None

        daily_returns = await self._pnl.get_pillar_pnl_series(pillar, window_days)

        if len(daily_returns) < 5:
            return None  # not enough data

        mu = sum(daily_returns) / len(daily_returns)
        if len(daily_returns) == 1:
            return 0.0

        variance = sum((r - mu) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        sigma = math.sqrt(variance)

        if sigma == 0.0:
            return 0.0 if mu == 0.0 else (float("inf") if mu > 0 else float("-inf"))

        sharpe = (mu / sigma) * math.sqrt(365)
        return round(sharpe, 4)

    async def get_cached_sharpe(self, pillar: str, window_days: int = 30) -> Optional[float]:
        """Get Sharpe from Redis cache, or compute and cache if missing."""
        if self._redis is None:
            return await self.compute_sharpe(pillar, window_days)

        key = f"{KEY_SHARPE_PREFIX}:{window_days}d"
        try:
            cached = await self._redis.hget(key, pillar)
            if cached:
                data = json.loads(cached)
                age = time.time() - data.get("ts", 0)
                if age < 21600:  # 6-hour cache
                    return data.get("sharpe")
        except Exception:
            pass

        sharpe = await self.compute_sharpe(pillar, window_days)
        if sharpe is not None:
            entry = json.dumps({"sharpe": sharpe, "ts": time.time()})
            await self._redis.hset(key, pillar, entry)
            await self._redis.expire(key, 21600)

        return sharpe

    async def get_all_sharpes(self, window_days: int = 30) -> Dict[str, Optional[float]]:
        """Return Sharpe ratios for all three pillars."""
        result = {}
        for pillar in ("lighter", "polymarket", "swing"):
            result[pillar] = await self.get_cached_sharpe(pillar, window_days)
        return result
