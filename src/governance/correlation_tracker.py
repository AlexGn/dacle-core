"""
Cross-Pillar Correlation Tracker.

Tracks return-stream correlations between pillars to prevent concentration
risk. Updated nightly from PnLAggregator data.

Feeds CapitalAllocator:
    - High correlation (>0.8) → reduce allocation to correlated pillars
    - Near-zero correlation → independent strategies, full allocation

Redis cache: dacle:governance:correlation
    JSON dict: {"lighter:polymarket": 0.42, "lighter:swing": 0.15, ...}
"""

import json
import math
import time
from typing import Dict, Optional, Tuple


KEY_CORRELATION = "dacle:governance:correlation"


class CorrelationTracker:
    """Cross-pillar return correlation tracker."""

    def __init__(self, redis=None, pnl_aggregator=None):
        self._redis = redis
        self._pnl = pnl_aggregator

    async def compute_pairwise(self, window_days: int = 30) -> Dict[str, float]:
        """Compute pairwise Pearson correlations between all pillar pairs.

        Returns:
            Dict with keys like "lighter:polymarket" → correlation coefficient.
        """
        if self._pnl is None:
            return {}

        pillars = ("lighter", "polymarket", "swing")
        series: Dict[str, list] = {}

        for p in pillars:
            series[p] = await self._pnl.get_pillar_pnl_series(p, window_days)

        result = {}
        for i, p1 in enumerate(pillars):
            for p2 in pillars[i + 1:]:
                corr = _pearson(series.get(p1, []), series.get(p2, []))
                key = f"{p1}:{p2}"
                result[key] = round(corr, 4) if corr is not None else 0.0

        return result

    async def get_correlation(self) -> Dict[str, float]:
        """Get cached correlations, or compute and cache."""
        if self._redis is None:
            return await self.compute_pairwise()

        try:
            raw = await self._redis.get(KEY_CORRELATION)
            if raw:
                data = json.loads(raw)
                age = time.time() - data.get("ts", 0)
                if age < 86400:  # 24h cache
                    return {k: v for k, v in data.items() if k != "ts"}
        except Exception:
            pass

        correlations = await self.compute_pairwise()
        if correlations:
            entry = {"ts": time.time(), **correlations}
            await self._redis.set(KEY_CORRELATION, json.dumps(entry), ex=86400)
        return correlations

    async def get_max_correlation(self) -> float:
        """Return the highest pairwise correlation across all pillars."""
        corrs = await self.get_correlation()
        values = [abs(v) for v in corrs.values()]
        return max(values) if values else 0.0


def _pearson(x: list, y: list) -> Optional[float]:
    """Pearson correlation coefficient between two equal-length series."""
    n = min(len(x), len(y))
    if n < 3:
        return None

    x = x[:n]
    y = y[:n]

    mx = sum(x) / n
    my = sum(y) / n

    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))

    if dx == 0 or dy == 0:
        return 0.0

    return num / (dx * dy)
