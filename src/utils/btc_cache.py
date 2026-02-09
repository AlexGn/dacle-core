"""
Global BTC Macro Cache

Session 342: Shared BTC macro context cache to eliminate redundant API calls.

Problem: BTC macro context (price, trend, RSI, regime) was being fetched separately by:
- TA Upload (fetch_btc_macro_context)
- Playbook Generator (BTC context integration)
- Alert Generator (BTC safety checks)
- Position Sizing (regime-based modifiers)

Each fetch = 100-200ms Binance API call = 400-800ms total latency per playbook.

Solution: Single shared cache with 5-minute TTL
- First request fetches from Binance and caches
- Subsequent requests get cached data (<10ms)
- TTL ensures data stays reasonably fresh for trading decisions

Usage:
    from src.utils.btc_cache import get_btc_macro_context, invalidate_btc_cache

    # Get cached or fresh BTC data
    btc_context = await get_btc_macro_context()

    # Force refresh (after major price move)
    btc_context = await get_btc_macro_context(force_refresh=True)
"""

import logging
import asyncio
from datetime import datetime
from typing import Optional

import httpx

from src.utils.redis_cache import get_redis_cache

logger = logging.getLogger(__name__)

# Cache configuration
BTC_CACHE_KEY = "global:btc_macro"
BTC_CACHE_TTL = 300  # 5 minutes - balances freshness vs API efficiency
BTC_CACHE_NAMESPACE = "btc"

# Binance API endpoints
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"

# In-memory fallback for when Redis is unavailable
_memory_cache: dict = {}
_memory_cache_timestamp: Optional[datetime] = None


async def fetch_btc_from_binance() -> dict:
    """
    Fetch BTC macro context directly from Binance API.

    Returns dict with: price, change_24h, trend, rsi, range_7d, regime, sma_distance
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Fetch 4H klines for trend/RSI calculation
            klines_resp = await client.get(
                BINANCE_KLINES_URL,
                params={"symbol": "BTCUSDT", "interval": "4h", "limit": 50}
            )
            klines = klines_resp.json()

            # Fetch 24h ticker for current price and change
            ticker_resp = await client.get(
                BINANCE_TICKER_URL,
                params={"symbol": "BTCUSDT"}
            )
            ticker = ticker_resp.json()

        closes = [float(k[4]) for k in klines]
        current_price = float(ticker["lastPrice"])
        change_24h = float(ticker["priceChangePercent"])

        # 7-day range (42 x 4H candles)
        high_7d = max(closes[-42:]) if len(closes) >= 42 else max(closes)
        low_7d = min(closes[-42:]) if len(closes) >= 42 else min(closes)
        range_7d = ((high_7d - low_7d) / low_7d) * 100

        # Simple trend: current vs 20-period SMA
        sma_20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else sum(closes) / len(closes)
        trend = "UPTREND" if current_price > sma_20 else "DOWNTREND"
        sma_distance = ((current_price - sma_20) / sma_20) * 100

        # RSI(14) calculation
        changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [c if c > 0 else 0 for c in changes[-14:]]
        losses = [-c if c < 0 else 0 for c in changes[-14:]]
        avg_gain = sum(gains) / 14 if gains else 0
        avg_loss = sum(losses) / 14 if losses else 0.001
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        # Regime classification (L081, Session 389b: added NEUTRAL)
        if rsi < 30:
            regime = "OVERSOLD"
        elif rsi > 70:
            regime = "OVERBOUGHT"
        elif range_7d < 5:
            regime = "SIDEWAYS"
        elif abs(sma_distance) < 1.5 and 40 <= rsi <= 60:
            regime = "NEUTRAL"
        elif trend == "UPTREND":
            regime = "BULLISH"
        else:
            regime = "BEARISH"

        return {
            "price": current_price,
            "change_24h": round(change_24h, 2),
            "trend": trend,
            "rsi": round(rsi, 1),
            "range_7d": round(range_7d, 1),
            "regime": regime,
            "sma_distance": round(sma_distance, 2),
            "high_7d": high_7d,
            "low_7d": low_7d,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "source": "binance",
            "cached": False
        }

    except Exception as e:
        logger.warning(f"Failed to fetch BTC from Binance: {e}")
        return {
            "price": None,
            "error": str(e),
            "regime": "UNKNOWN",
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "source": "error",
            "cached": False
        }


async def get_btc_macro_context(force_refresh: bool = False) -> dict:
    """
    Get BTC macro context from cache or fetch fresh.

    Args:
        force_refresh: If True, bypass cache and fetch fresh data

    Returns:
        dict with BTC price, trend, RSI, regime, etc.
    """
    global _memory_cache, _memory_cache_timestamp

    cache = get_redis_cache()

    # Try Redis cache first (unless force refresh)
    if cache and cache.client and not force_refresh:
        try:
            cached_data = cache.get(BTC_CACHE_KEY, namespace=BTC_CACHE_NAMESPACE)
            if cached_data:
                cached_data["cached"] = True
                cached_data["cache_source"] = "redis"
                logger.debug("BTC context from Redis cache")
                return cached_data
        except Exception as e:
            logger.warning(f"Redis cache read failed: {e}")

    # Try memory cache (fallback when Redis unavailable)
    if not force_refresh and _memory_cache and _memory_cache_timestamp:
        age_seconds = (datetime.utcnow() - _memory_cache_timestamp).total_seconds()
        if age_seconds < BTC_CACHE_TTL:
            result = _memory_cache.copy()
            result["cached"] = True
            result["cache_source"] = "memory"
            result["cache_age_seconds"] = round(age_seconds, 1)
            logger.debug(f"BTC context from memory cache ({age_seconds:.1f}s old)")
            return result

    # Fetch fresh data from Binance
    logger.info("Fetching fresh BTC context from Binance")
    btc_data = await fetch_btc_from_binance()

    # Only cache successful fetches
    if btc_data.get("price") is not None:
        # Update Redis cache
        if cache and cache.client:
            try:
                cache.set(BTC_CACHE_KEY, btc_data, ttl=BTC_CACHE_TTL, namespace=BTC_CACHE_NAMESPACE)
                logger.debug("BTC context cached to Redis")
            except Exception as e:
                logger.warning(f"Redis cache write failed: {e}")

        # Update memory cache (fallback)
        _memory_cache = btc_data.copy()
        _memory_cache_timestamp = datetime.utcnow()

    return btc_data


def invalidate_btc_cache() -> bool:
    """
    Invalidate BTC cache (e.g., after detecting major price move).

    Returns:
        True if cache was invalidated successfully
    """
    global _memory_cache, _memory_cache_timestamp

    # Clear memory cache
    _memory_cache = {}
    _memory_cache_timestamp = None

    # Clear Redis cache
    cache = get_redis_cache()
    if cache and cache.client:
        try:
            cache.delete(BTC_CACHE_KEY, namespace=BTC_CACHE_NAMESPACE)
            logger.info("BTC cache invalidated")
            return True
        except Exception as e:
            logger.warning(f"Failed to invalidate Redis BTC cache: {e}")
            return False

    return True


def get_btc_cache_status() -> dict:
    """
    Get current BTC cache status for debugging.

    Returns:
        dict with cache status, age, and data preview
    """
    global _memory_cache, _memory_cache_timestamp

    result = {
        "memory_cache_present": bool(_memory_cache),
        "memory_cache_age_seconds": None,
        "redis_cache_present": False,
        "redis_cache_ttl": None
    }

    if _memory_cache_timestamp:
        result["memory_cache_age_seconds"] = round(
            (datetime.utcnow() - _memory_cache_timestamp).total_seconds(), 1
        )

    cache = get_redis_cache()
    if cache and cache.client:
        try:
            # Check if key exists
            ttl = cache.client.ttl(f"{BTC_CACHE_NAMESPACE}:{BTC_CACHE_KEY}")
            result["redis_cache_present"] = ttl > 0
            result["redis_cache_ttl"] = ttl if ttl > 0 else None
        except Exception:
            pass

    return result


# Convenience function for calculating position modifier (L081)
def calculate_btc_position_modifier(btc_context: dict, direction: str = "SHORT") -> float:
    """
    Calculate position size modifier based on BTC context and trade direction.

    Based on L081: BTC Structure-Based Trade Gating

    Args:
        btc_context: BTC macro context dict
        direction: "SHORT" or "LONG"

    Returns:
        Position modifier (0.5x - 1.25x)
    """
    if not btc_context or btc_context.get("price") is None:
        return 1.0  # Default: no modification on error

    regime = btc_context.get("regime", "UNKNOWN")
    trend = btc_context.get("trend", "UNKNOWN")
    rsi = btc_context.get("rsi", 50)

    modifier = 1.0

    if direction.upper() == "SHORT":
        # SHORTs prefer bearish conditions
        if regime == "NEUTRAL":
            modifier = 1.0  # No adjustment in neutral
        elif regime == "BEARISH" or trend == "DOWNTREND":
            modifier = 1.1  # Aligned with direction
        elif regime == "BULLISH" or trend == "UPTREND":
            modifier = 0.75  # Counter-trend
        elif regime == "OVERBOUGHT":
            modifier = 1.25  # Exhaustion imminent
        elif regime == "OVERSOLD":
            modifier = 0.5  # Bounce risk

    elif direction.upper() == "LONG":
        # LONGs prefer bullish conditions
        if regime == "NEUTRAL":
            modifier = 1.0  # No adjustment in neutral
        elif regime == "BULLISH" or trend == "UPTREND":
            modifier = 1.1  # Aligned with direction
        elif regime == "BEARISH" or trend == "DOWNTREND":
            modifier = 0.75  # Counter-trend
        elif regime == "OVERSOLD":
            modifier = 1.25  # Bounce expected
        elif regime == "OVERBOUGHT":
            modifier = 0.5  # Rejection risk

    # Additional RSI adjustment
    if direction.upper() == "SHORT" and rsi > 70:
        modifier = min(modifier * 1.15, 1.25)  # Cap at 1.25x
    elif direction.upper() == "LONG" and rsi < 30:
        modifier = min(modifier * 1.15, 1.25)

    return round(modifier, 2)
