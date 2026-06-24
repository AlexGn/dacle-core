"""
Cross-Pillar PnL Aggregator.

Consolidates per-pillar realized PnL streams into a single Redis sorted set
for cross-pillar analysis. Feeds the Sharpe service and CapitalAllocator.

Redis key: dacle:governance:pnl_history
    Sorted set, score = unix timestamp, member = JSON with pillar + pnl

Daily PnL tracked in: dacle:governance:daily_pnl (Redis string, JSON dict)
    {"lighter": -3.50, "polymarket": 0.75, "swing": 2.10, "total": -0.65}
"""

import json
import time
from typing import Dict, Optional


KEY_PNL_HISTORY = "dacle:governance:pnl_history"
KEY_DAILY_PNL = "dacle:governance:daily_pnl"
MAX_HISTORY_DAYS = 90  # keep 90 days of PnL data


class PnLAggregator:
    """Cross-pillar PnL aggregation in Redis."""

    def __init__(self, redis=None):
        self._redis = redis

    # ── Write ──────────────────────────────────────────────────────

    async def record_pnl(self, pillar: str, pnl_usd: float, source: str = ""):
        """Record a realized PnL event for a pillar.

        Args:
            pillar: "lighter", "polymarket", or "swing"
            pnl_usd: Realized profit/loss in USD.
            source: Optional attribution (e.g. trade_id).
        """
        if self._redis is None:
            return

        now = time.time()
        entry = json.dumps({
            "ts": now,
            "pillar": pillar,
            "pnl_usd": round(pnl_usd, 4),
            "source": source,
        })

        pipe = self._redis.pipeline()
        pipe.zadd(KEY_PNL_HISTORY, {entry: now})
        pipe.zremrangebyscore(KEY_PNL_HISTORY, 0, now - (MAX_HISTORY_DAYS * 86400))
        await pipe.execute()

        # Update daily PnL
        await self._update_daily(pillar, pnl_usd)

    async def _update_daily(self, pillar: str, pnl: float):
        """Add pnl to the daily running total."""
        raw = await self._redis.get(KEY_DAILY_PNL)
        daily = json.loads(raw) if raw else {"lighter": 0.0, "polymarket": 0.0, "swing": 0.0}
        daily[pillar] = round(daily.get(pillar, 0.0) + pnl, 4)
        daily["total"] = sum(v for k, v in daily.items() if k != "total")
        await self._redis.set(KEY_DAILY_PNL, json.dumps(daily))

    # ── Read ───────────────────────────────────────────────────────

    async def get_daily_pnl(self) -> Dict[str, float]:
        """Return today's PnL per pillar.

        Returns: {"lighter": 0.0, "polymarket": 0.0, "swing": 0.0, "total": 0.0}
        """
        if self._redis is None:
            return {"lighter": 0.0, "polymarket": 0.0, "swing": 0.0, "total": 0.0}
        try:
            raw = await self._redis.get(KEY_DAILY_PNL)
            if raw:
                return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
        return {"lighter": 0.0, "polymarket": 0.0, "swing": 0.0, "total": 0.0}

    async def get_history(self, days: int = 30) -> list:
        """Return recent PnL entries. Most recent first.

        Returns list of {"ts": ..., "pillar": ..., "pnl_usd": ..., "source": ...}
        """
        if self._redis is None:
            return []
        try:
            cutoff = time.time() - (days * 86400)
            raw = await self._redis.zrangebyscore(KEY_PNL_HISTORY, cutoff, "+inf")
            entries = []
            for r in raw:
                try:
                    entries.append(json.loads(r))
                except json.JSONDecodeError:
                    pass
            return sorted(entries, key=lambda e: e.get("ts", 0), reverse=True)
        except Exception:
            return []

    async def get_pillar_pnl_series(self, pillar: str, days: int = 30) -> list:
        """Return daily PnL series for a single pillar. Most recent first.

        Returns list of floats (USD per day).
        """
        history = await self.get_history(days)
        # Group by day and sum per pillar
        day_pnl: Dict[str, float] = {}
        for entry in history:
            if entry.get("pillar") != pillar:
                continue
            day_key = time.strftime("%Y-%m-%d", time.gmtime(entry["ts"]))
            day_pnl[day_key] = day_pnl.get(day_key, 0.0) + entry.get("pnl_usd", 0.0)

        return [day_pnl[k] for k in sorted(day_pnl)]
