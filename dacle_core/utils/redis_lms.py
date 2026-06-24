#!/usr/bin/env python3
"""
Redis Live Market State (LMS) - High-speed price cache
Session 265: Phase 1, Day 5 (Agent 2 Integration)

Provides <1ms price lookups from Redis WebSocket cache.
Falls back to REST API if Redis unavailable or cache miss.

Architecture:
- Primary: Redis LMS (price:{SYMBOL}USDT) - <1ms latency
- Fallback: REST API (ccxt.fetch_ticker) - 200-500ms latency

Usage:
    from dacle_core.utils.redis_lms import get_current_price

    # Single price lookup
    btc_price = get_current_price("BTC")

    # Batch price lookup (returns dict)
    prices = get_current_prices(["BTC", "ETH", "POWER"])
    # {'BTC': 95432.50, 'ETH': 3421.30, 'POWER': 0.26}

Author: Claude Code (Session 265)
Date: 2025-12-28
"""

import json
import logging
from typing import Dict, List, Optional, Union

import redis

logger = logging.getLogger(__name__)

# Redis configuration
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0

# Global Redis client (lazy initialization)
_redis_client: Optional[redis.Redis] = None


def _get_redis_client() -> Optional[redis.Redis]:
    """
    Get or create Redis client (singleton pattern).

    Returns:
        Redis client or None if connection fails
    """
    global _redis_client

    if _redis_client is None:
        try:
            _redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                decode_responses=True,
                socket_connect_timeout=1,  # Fast timeout for local Redis
            )
            _redis_client.ping()  # Test connection
            logger.debug(f"✅ Connected to Redis LMS at {REDIS_HOST}:{REDIS_PORT}")
        except Exception as e:
            logger.warning(f"⚠️ Redis LMS unavailable: {e}")
            _redis_client = None

    return _redis_client


def get_current_price(
    symbol: str,
    fallback_exchange=None,
) -> Optional[float]:
    """
    Get current price from Redis LMS (primary) or REST API (fallback).

    Args:
        symbol: Token symbol (e.g., "BTC", "ETH", "POWER")
        fallback_exchange: ccxt.Exchange instance for REST fallback (optional)

    Returns:
        Current price (float) or None if not available

    Example:
        >>> price = get_current_price("BTC")
        95432.50  # From Redis LMS (<1ms)

        >>> price = get_current_price("NEWTOKEN", fallback_exchange=ccxt.binance())
        0.42  # From REST API (200-500ms, token not yet in LMS)
    """
    redis_client = _get_redis_client()

    # Primary: Redis LMS (fast path)
    if redis_client:
        try:
            lms_key = f"price:{symbol}USDT"
            lms_data = redis_client.get(lms_key)

            if lms_data:
                data = json.loads(lms_data)
                price = data.get("price")
                if price:
                    logger.debug(f"✅ Redis LMS hit: {symbol} = ${price:.4f}")
                    return float(price)
        except Exception as e:
            logger.debug(f"⚠️ Redis LMS read failed for {symbol}: {e}")

    # Fallback: REST API (slow path)
    if fallback_exchange:
        try:
            logger.debug(f"🔄 Redis LMS miss for {symbol}, falling back to REST API...")

            # Try multiple pairs (perp, spot, etc.)
            pairs = [
                f"{symbol}/USDT:USDT",  # Perpetual (priority for shorts)
                f"{symbol}/USDT",        # Spot
                f"{symbol}/USD",         # Alternative spot
            ]

            for pair in pairs:
                try:
                    ticker = fallback_exchange.fetch_ticker(pair)
                    price = ticker.get("last")
                    if price:
                        logger.debug(f"✅ REST API: {symbol} = ${price:.4f} ({pair})")
                        return float(price)
                except Exception:
                    continue

            logger.warning(f"⚠️ No price found for {symbol} (REST API fallback failed)")
        except Exception as e:
            logger.error(f"❌ REST API fetch failed for {symbol}: {e}")

    return None


def get_current_prices(
    symbols: List[str],
    fallback_exchange=None,
) -> Dict[str, Optional[float]]:
    """
    Batch fetch current prices for multiple symbols.

    Args:
        symbols: List of token symbols (e.g., ["BTC", "ETH", "POWER"])
        fallback_exchange: ccxt.Exchange instance for REST fallback (optional)

    Returns:
        Dict mapping symbol → price (e.g., {'BTC': 95432.50, 'ETH': 3421.30})

    Example:
        >>> prices = get_current_prices(["BTC", "ETH", "POWER"])
        {'BTC': 95432.50, 'ETH': 3421.30, 'POWER': 0.26}
    """
    redis_client = _get_redis_client()
    prices = {}

    # Primary: Batch read from Redis LMS
    if redis_client:
        try:
            # Build list of Redis keys
            lms_keys = [f"price:{symbol}USDT" for symbol in symbols]

            # Batch GET (MGET) - single round trip
            lms_values = redis_client.mget(lms_keys)

            for symbol, lms_data in zip(symbols, lms_values):
                if lms_data:
                    try:
                        data = json.loads(lms_data)
                        price = data.get("price")
                        if price:
                            prices[symbol] = float(price)
                    except Exception as e:
                        logger.debug(f"⚠️ Failed to parse Redis LMS for {symbol}: {e}")

            logger.debug(f"✅ Redis LMS batch: {len(prices)}/{len(symbols)} hits")
        except Exception as e:
            logger.warning(f"⚠️ Redis LMS batch read failed: {e}")

    # Fallback: REST API for missing symbols
    missing_symbols = [s for s in symbols if s not in prices]
    if missing_symbols and fallback_exchange:
        logger.debug(f"🔄 Fetching {len(missing_symbols)} missing prices from REST API...")

        for symbol in missing_symbols:
            price = get_current_price(symbol, fallback_exchange=fallback_exchange)
            if price:
                prices[symbol] = price

    return prices


def get_ticker_data(
    symbol: str,
) -> Optional[Dict]:
    """
    Get full ticker data from Redis LMS.

    Args:
        symbol: Token symbol (e.g., "BTC", "ETH")

    Returns:
        Dict with full ticker data or None

    Example:
        >>> data = get_ticker_data("BTC")
        {
            "price": 95432.50,
            "timestamp": 1735401234567,
            "volume": 123456789.0,
            "change_24h": -2.34,
            "high_24h": 96500.0,
            "low_24h": 94800.0,
            "bid": 95432.00,
            "ask": 95433.00
        }
    """
    redis_client = _get_redis_client()

    if not redis_client:
        return None

    try:
        lms_key = f"price:{symbol}USDT"
        lms_data = redis_client.get(lms_key)

        if lms_data:
            return json.loads(lms_data)
    except Exception as e:
        logger.warning(f"⚠️ Failed to get ticker data for {symbol}: {e}")

    return None


def is_redis_lms_available() -> bool:
    """
    Check if Redis LMS is available.

    Returns:
        True if Redis is connected and responding, False otherwise
    """
    redis_client = _get_redis_client()
    if not redis_client:
        return False

    try:
        redis_client.ping()
        return True
    except Exception:
        return False


def get_lms_statistics() -> Dict:
    """
    Get Redis LMS statistics (cache size, keys, etc.).

    Returns:
        Dict with LMS statistics

    Example:
        >>> stats = get_lms_statistics()
        {
            "total_keys": 5,
            "symbols": ["BTC", "ETH", "POWER", "LAYER", "MONAD"],
            "redis_memory_mb": 0.12
        }
    """
    redis_client = _get_redis_client()

    if not redis_client:
        return {
            "available": False,
            "error": "Redis not connected"
        }

    try:
        # Get all price keys
        price_keys = redis_client.keys("price:*")

        # Extract symbols
        symbols = [key.replace("price:", "").replace("USDT", "") for key in price_keys]

        # Get Redis memory usage
        info = redis_client.info("memory")
        memory_mb = info.get("used_memory", 0) / (1024 * 1024)

        return {
            "available": True,
            "total_keys": len(price_keys),
            "symbols": sorted(symbols),
            "redis_memory_mb": round(memory_mb, 2),
        }
    except Exception as e:
        return {
            "available": False,
            "error": str(e)
        }


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Check if Redis LMS is available
    if is_redis_lms_available():
        print("✅ Redis LMS is available")

        # Get statistics
        stats = get_lms_statistics()
        print(f"📊 LMS Statistics: {json.dumps(stats, indent=2)}")

        # Get current prices
        btc_price = get_current_price("BTC")
        if btc_price:
            print(f"💰 BTC Price: ${btc_price:.2f}")

        # Batch fetch
        prices = get_current_prices(["BTC", "ETH"])
        print(f"💰 Prices: {prices}")

        # Full ticker data
        ticker = get_ticker_data("BTC")
        if ticker:
            print(f"📈 BTC Ticker: {json.dumps(ticker, indent=2)}")
    else:
        print("❌ Redis LMS is not available (WebSocket monitor not running?)")
