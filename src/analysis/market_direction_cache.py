"""
Thin TTL-cached wrapper around calculate_direction_bias().

Ensures that rapid consecutive requests (e.g. pre-trade-check + full-analysis
hitting within seconds) share a single live computation, while still refreshing
every 60 seconds so #macro-updates and #trades stay in sync.
"""

import logging
import time
from typing import Optional

from src.analysis.market_direction_scorer import calculate_direction_bias

logger = logging.getLogger(__name__)

_cache: dict = {"result": None, "expires": 0.0}
_TTL_SECONDS = 60


async def get_live_market_direction() -> Optional[dict]:
    """Return live market direction with 60s TTL cache.

    Returns dict with keys: bias, score, confidence_pct — or None on failure.
    """
    now = time.monotonic()
    if _cache["result"] is not None and now < _cache["expires"]:
        return _cache["result"]

    try:
        result = await calculate_direction_bias()
        entry = {
            "bias": result.bias.value,
            "score": round(result.score, 3),
            "confidence_pct": result.confidence_pct,
        }
        _cache["result"] = entry
        _cache["expires"] = now + _TTL_SECONDS
        return entry
    except Exception as e:
        logger.warning(f"[market-direction-cache] Live calc failed: {e}")
        return None


def clear_cache() -> None:
    """Reset the TTL cache (useful for testing)."""
    _cache["result"] = None
    _cache["expires"] = 0.0
