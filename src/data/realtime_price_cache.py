#!/usr/bin/env python3
"""
Real-Time Price Cache Manager - Session 291 P0 #4

Intelligent price caching with automatic freshness detection and multi-source fallback.
Integrates WebSocket streaming (Binance) with REST API fallback (CoinGecko, Binance API).

Architecture:
- Primary: Binance WebSocket (real-time, <100ms latency)
- Fallback 1: Binance REST API (1-2s latency, no auth required)
- Fallback 2: CoinGecko API (2-5s latency, cached pricing)
- Redis cache: Optional persistence across restarts
- Memory cache: Always-on local storage

Features:
- Automatic staleness detection (configurable TTL per token category)
- Price verification (multiple sources for critical decisions)
- Conviction score trigger detection (>2x price movement)
- Thread-safe operations
- Graceful degradation

Usage:
    from src.data.realtime_price_cache import RealtimePriceCache

    cache = RealtimePriceCache()

    # Get price (auto-refreshes if stale)
    price = cache.get_price("POWER")

    # Check if conviction should be recalculated (L033)
    should_recalc = cache.needs_conviction_update("POWER", original_price=0.36)

    # Batch update for multiple tokens
    cache.update_prices(["BTC", "ETH", "POWER"])

Author: Claude Code (Session 291 - Real-Time Price Updates)
Date: 2026-01-06
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional, Set
import threading
import requests

logger = logging.getLogger(__name__)

# Staleness thresholds by token category
STALENESS_TTL = {
    "BTC": 60,  # 1 minute (macro index, needs freshness)
    "MEME": 30,  # 30 seconds (high volatility)
    "AI": 30,  # 30 seconds (high volatility)
    "GAMING": 60,  # 1 minute (moderate volatility)
    "L1": 120,  # 2 minutes (lower volatility)
    "L2": 120,  # 2 minutes (lower volatility)
    "DEFI": 120,  # 2 minutes (lower volatility)
    "DEFAULT": 60,  # 1 minute (conservative default)
}

# Conviction update threshold (L033: Dynamic FDV/MC Recalculation)
CONVICTION_UPDATE_MULTIPLIER = 2.0  # >2x price movement triggers re-score


class PriceSource(Enum):
    """Source of price data."""
    WEBSOCKET = "websocket"  # Real-time Binance WebSocket
    BINANCE_REST = "binance_rest"  # Binance REST API
    COINGECKO = "coingecko"  # CoinGecko API
    CACHE = "cache"  # Cached value (may be stale)


@dataclass
class PriceData:
    """Cached price data with metadata."""
    symbol: str  # Token symbol (e.g., "POWER")
    price: float  # Current price in USD
    price_24h_ago: Optional[float]  # Price 24h ago (for %change calculation)
    change_24h_pct: float  # 24h price change %
    volume_24h: Optional[float]  # 24h volume in USD
    timestamp: datetime  # When price was fetched
    source: PriceSource  # Source of this price
    verified: bool  # True if verified by multiple sources
    category: str  # Token category for staleness TTL


class RealtimePriceCache:
    """
    Real-time price cache with intelligent freshness management.

    Key Features:
    - Automatic staleness detection (category-based TTLs)
    - Multi-source fallback (WebSocket → REST → CoinGecko)
    - Price verification for critical decisions
    - Conviction update triggers (L033)
    - Thread-safe operations
    """

    def __init__(self, use_redis: bool = True):
        """
        Initialize the price cache.

        Args:
            use_redis: If True, use Redis for persistence (optional)
        """
        self._prices: Dict[str, PriceData] = {}
        self._price_lock = threading.Lock()
        self._ws_client = None  # Lazy-initialized WebSocket client
        self._use_redis = use_redis
        self._redis_client = None

        if use_redis:
            self._init_redis()

    def _init_redis(self):
        """Initialize Redis client (optional persistence)."""
        try:
            from src.utils.redis_cache import RedisCache
            self._redis_client = RedisCache(namespace="prices")
            logger.debug("Redis price cache initialized")
        except (ImportError, Exception) as e:
            logger.warning(f"Redis unavailable, using memory-only: {e}")
            self._redis_client = None

    def get_price(
        self,
        symbol: str,
        category: str = "DEFAULT",
        force_refresh: bool = False,
        verify: bool = False,
    ) -> Optional[PriceData]:
        """
        Get price with automatic freshness management.

        Args:
            symbol: Token symbol (e.g., "POWER")
            category: Token category for staleness TTL
            force_refresh: Force fetch even if cached
            verify: Verify price with multiple sources (for critical decisions)

        Returns:
            PriceData if available, None if all sources failed
        """
        symbol = symbol.upper()

        # Check cache first
        if not force_refresh:
            with self._price_lock:
                cached = self._prices.get(symbol)
                if cached and not self._is_stale(cached, category):
                    return cached

        # Price is stale or missing - fetch new data
        price_data = self._fetch_price(symbol, category)

        if price_data:
            # Verify with additional source if requested
            if verify and price_data.source != PriceSource.WEBSOCKET:
                verified_data = self._verify_price(price_data)
                if verified_data:
                    price_data = verified_data

            # Update cache
            with self._price_lock:
                self._prices[symbol] = price_data

            # Persist to Redis if available
            if self._redis_client:
                try:
                    self._redis_client.set(
                        f"price:{symbol}",
                        {
                            "price": price_data.price,
                            "change_24h_pct": price_data.change_24h_pct,
                            "timestamp": price_data.timestamp.isoformat(),
                            "source": price_data.source.value,
                        },
                        ttl=STALENESS_TTL.get(category, STALENESS_TTL["DEFAULT"]) * 2,
                    )
                except Exception as e:
                    logger.debug(f"Redis set failed: {e}")

        return price_data

    def _is_stale(self, price_data: PriceData, category: str) -> bool:
        """Check if cached price is stale."""
        ttl_seconds = STALENESS_TTL.get(category, STALENESS_TTL["DEFAULT"])
        age_seconds = (datetime.now(timezone.utc) - price_data.timestamp).total_seconds()
        return age_seconds > ttl_seconds

    def _fetch_price(self, symbol: str, category: str) -> Optional[PriceData]:
        """
        Fetch price with multi-source fallback.

        Priority:
        1. WebSocket (if available)
        2. Binance REST API
        3. CoinGecko API
        """
        # Try WebSocket first (fastest, <100ms)
        price_data = self._fetch_from_websocket(symbol)
        if price_data:
            return price_data

        # Fallback to Binance REST (1-2s, no auth required)
        price_data = self._fetch_from_binance_rest(symbol)
        if price_data:
            return price_data

        # Fallback to CoinGecko (2-5s, may be cached)
        price_data = self._fetch_from_coingecko(symbol, category)
        return price_data

    def _fetch_from_websocket(self, symbol: str) -> Optional[PriceData]:
        """Fetch price from WebSocket cache (if client is running)."""
        try:
            from src.data.websocket_price_stream import get_realtime_price

            price = get_realtime_price(symbol, timeout=2.0)
            if price:
                return PriceData(
                    symbol=symbol,
                    price=price,
                    price_24h_ago=None,  # WebSocket doesn't provide historical
                    change_24h_pct=0.0,  # Will be calculated later
                    volume_24h=None,
                    timestamp=datetime.now(timezone.utc),
                    source=PriceSource.WEBSOCKET,
                    verified=False,
                    category="DEFAULT",
                )
        except Exception as e:
            logger.debug(f"WebSocket fetch failed for {symbol}: {e}")

        return None

    def _fetch_from_binance_rest(self, symbol: str) -> Optional[PriceData]:
        """Fetch price from Binance REST API."""
        try:
            trading_pair = f"{symbol}USDT"
            url = "https://api.binance.com/api/v3/ticker/24hr"
            response = requests.get(url, params={"symbol": trading_pair}, timeout=5)
            response.raise_for_status()

            data = response.json()
            current_price = float(data["lastPrice"])
            price_change_pct = float(data["priceChangePercent"])
            volume_24h = float(data["quoteVolume"])

            # Calculate 24h ago price
            price_24h_ago = current_price / (1 + price_change_pct / 100)

            return PriceData(
                symbol=symbol,
                price=current_price,
                price_24h_ago=price_24h_ago,
                change_24h_pct=price_change_pct,
                volume_24h=volume_24h,
                timestamp=datetime.now(timezone.utc),
                source=PriceSource.BINANCE_REST,
                verified=False,
                category="DEFAULT",
            )

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                logger.debug(f"Binance does not list {symbol}USDT")
            else:
                logger.warning(f"Binance REST API error for {symbol}: {e}")
        except Exception as e:
            logger.debug(f"Binance REST fetch failed for {symbol}: {e}")

        return None

    def _fetch_from_coingecko(self, symbol: str, category: str) -> Optional[PriceData]:
        """Fetch price from CoinGecko API."""
        try:
            # CoinGecko uses IDs, not symbols - need mapping
            # For now, fallback to simple search
            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {
                "ids": symbol.lower(),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            if symbol.lower() in data:
                token_data = data[symbol.lower()]
                current_price = token_data["usd"]
                change_24h_pct = token_data.get("usd_24h_change", 0.0)
                volume_24h = token_data.get("usd_24h_vol")

                # Calculate 24h ago price
                price_24h_ago = current_price / (1 + change_24h_pct / 100) if change_24h_pct else None

                return PriceData(
                    symbol=symbol,
                    price=current_price,
                    price_24h_ago=price_24h_ago,
                    change_24h_pct=change_24h_pct,
                    volume_24h=volume_24h,
                    timestamp=datetime.now(timezone.utc),
                    source=PriceSource.COINGECKO,
                    verified=False,
                    category=category,
                )

        except Exception as e:
            logger.debug(f"CoinGecko fetch failed for {symbol}: {e}")

        return None

    def _verify_price(self, price_data: PriceData) -> Optional[PriceData]:
        """
        Verify price with additional source.

        Returns original if verification fails, updated if different source confirms.
        """
        # If already from WebSocket, it's real-time and trustworthy
        if price_data.source == PriceSource.WEBSOCKET:
            price_data.verified = True
            return price_data

        # Try to verify with Binance if original was CoinGecko
        if price_data.source == PriceSource.COINGECKO:
            binance_data = self._fetch_from_binance_rest(price_data.symbol)
            if binance_data:
                # Check if prices are within 2% (acceptable divergence)
                price_diff_pct = abs(binance_data.price - price_data.price) / price_data.price * 100
                if price_diff_pct < 2.0:
                    binance_data.verified = True
                    return binance_data
                else:
                    logger.warning(
                        f"Price divergence for {price_data.symbol}: "
                        f"CG=${price_data.price:.4f} vs Binance=${binance_data.price:.4f} "
                        f"({price_diff_pct:.1f}% diff)"
                    )

        # Verification failed, return original
        return price_data

    def needs_conviction_update(
        self,
        symbol: str,
        original_price: float,
        original_tge_date: Optional[datetime] = None,
    ) -> bool:
        """
        Check if conviction should be recalculated due to price movement.

        Implements Learning 033 (Dynamic FDV/MC Recalculation):
        - Price >2x from TGE = recalculate conviction (SKIP → EXECUTE possible)

        Args:
            symbol: Token symbol
            original_price: Price at time of original conviction calculation
            original_tge_date: TGE date (for time-based checks)

        Returns:
            True if conviction should be recalculated
        """
        current_data = self.get_price(symbol)
        if not current_data:
            return False

        # Check if price moved >2x (up or down)
        price_ratio = current_data.price / original_price

        if price_ratio >= CONVICTION_UPDATE_MULTIPLIER:
            logger.info(
                f"🔄 {symbol} price UP {price_ratio:.1f}x since TGE "
                f"(${original_price:.4f} → ${current_data.price:.4f}) - Conviction update needed"
            )
            return True

        if price_ratio <= (1 / CONVICTION_UPDATE_MULTIPLIER):
            logger.info(
                f"🔄 {symbol} price DOWN {1/price_ratio:.1f}x since TGE "
                f"(${original_price:.4f} → ${current_data.price:.4f}) - Conviction update needed"
            )
            return True

        return False

    def update_prices(self, symbols: List[str], category: str = "DEFAULT"):
        """
        Batch update prices for multiple tokens.

        Args:
            symbols: List of token symbols
            category: Token category for staleness TTL
        """
        for symbol in symbols:
            try:
                self.get_price(symbol, category=category, force_refresh=True)
            except Exception as e:
                logger.error(f"Failed to update price for {symbol}: {e}")

    def get_all_prices(self) -> Dict[str, PriceData]:
        """Get all cached prices."""
        with self._price_lock:
            return dict(self._prices)

    def clear_stale_prices(self):
        """Remove stale prices from cache."""
        with self._price_lock:
            stale_symbols = [
                symbol
                for symbol, data in self._prices.items()
                if self._is_stale(data, data.category)
            ]

            for symbol in stale_symbols:
                del self._prices[symbol]

            if stale_symbols:
                logger.info(f"Cleared {len(stale_symbols)} stale prices: {stale_symbols}")

    def get_stats(self) -> Dict:
        """Get cache statistics."""
        with self._price_lock:
            total = len(self._prices)
            by_source = {}
            for data in self._prices.values():
                source = data.source.value
                by_source[source] = by_source.get(source, 0) + 1

            verified_count = sum(1 for data in self._prices.values() if data.verified)

            return {
                "total_cached": total,
                "by_source": by_source,
                "verified_count": verified_count,
                "verified_pct": (verified_count / total * 100) if total > 0 else 0,
            }


# Global singleton instance (optional - for convenience)
_global_cache: Optional[RealtimePriceCache] = None


def get_price_cache() -> RealtimePriceCache:
    """Get the global price cache instance (singleton pattern)."""
    global _global_cache
    if _global_cache is None:
        _global_cache = RealtimePriceCache()
    return _global_cache


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    if len(sys.argv) < 2:
        print("Usage: python realtime_price_cache.py <SYMBOL> [--verify]")
        sys.exit(1)

    symbol = sys.argv[1].upper()
    verify = "--verify" in sys.argv

    cache = RealtimePriceCache()

    print(f"Fetching price for {symbol}...")
    price_data = cache.get_price(symbol, verify=verify)

    if price_data:
        print(f"\n{'='*60}")
        print(f"PRICE DATA: {symbol}")
        print(f"{'='*60}")
        print(f"Price: ${price_data.price:.4f}")
        print(f"24h Change: {price_data.change_24h_pct:+.2f}%")
        print(f"24h Volume: ${price_data.volume_24h:,.0f}" if price_data.volume_24h else "24h Volume: N/A")
        print(f"Source: {price_data.source.value}")
        print(f"Verified: {'✓' if price_data.verified else '✗'}")
        print(f"Timestamp: {price_data.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"{'='*60}\n")

        # Test conviction update check
        if len(sys.argv) > 2 and sys.argv[2] != "--verify":
            original_price = float(sys.argv[2])
            needs_update = cache.needs_conviction_update(symbol, original_price)
            print(f"Original price: ${original_price:.4f}")
            print(f"Needs conviction update: {'YES' if needs_update else 'NO'}")
    else:
        print(f"❌ Could not fetch price for {symbol}")
        print("Tried: WebSocket → Binance REST → CoinGecko (all failed)")
