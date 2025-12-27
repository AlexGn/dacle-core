"""
Exchange Data Fetchers Module

Consolidates exchange-related data fetching:
- MEXC Calendar: Upcoming listings and TGE schedules
- Price Fetching: Real-time and historical prices
- Twitter Announcements: Exchange listing alerts

This module provides access to exchange data for trading decisions.

Usage:
    from src.data.fetchers.exchange import (
        fetch_mexc_calendar,
        fetch_token_price,
        fetch_mexc_twitter,
        MEXCCalendarFetcher,
    )

    # Fetch MEXC calendar for upcoming listings
    listings = fetch_mexc_calendar(days_ahead=7)

    # Fetch current token price
    price = fetch_token_price("BTC")
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure scripts.helpers is importable
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(_project_root))

logger = logging.getLogger(__name__)


# =============================================================================
# MEXC Calendar Fetcher
# =============================================================================

def fetch_mexc_calendar(
    days_ahead: int = 30,
    include_past: bool = False
) -> List[Dict[str, Any]]:
    """
    Fetch upcoming token listings from MEXC calendar.

    Args:
        days_ahead: Number of days to look ahead
        include_past: Include past listings

    Returns:
        List of upcoming listing dicts with:
        - symbol: Token symbol
        - name: Token name
        - listing_date: Expected listing date
        - trading_pairs: Available trading pairs
    """
    from scripts.helpers.mexc_calendar_enhanced import fetch_mexc_calendar as _fetch
    return _fetch(days_ahead=days_ahead, include_past=include_past)


class MEXCCalendarFetcher:
    """
    Class-based interface for MEXC calendar data.

    Provides caching and advanced filtering options.

    Usage:
        fetcher = MEXCCalendarFetcher()
        listings = fetcher.get_upcoming(days=7)
        listing = fetcher.find_by_symbol("MONAD")
    """

    def __init__(self, cache_duration_minutes: int = 30):
        """
        Initialize MEXC Calendar Fetcher.

        Args:
            cache_duration_minutes: Cache duration
        """
        self.cache_duration = cache_duration_minutes
        self._cache: Optional[List[Dict]] = None
        self._cache_time: Optional[float] = None

    def get_upcoming(
        self,
        days: int = 30,
        force_refresh: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get upcoming listings with caching.

        Args:
            days: Days to look ahead
            force_refresh: Bypass cache

        Returns:
            List of upcoming listing dicts
        """
        import time

        if not force_refresh and self._cache and self._cache_time:
            elapsed = (time.time() - self._cache_time) / 60
            if elapsed < self.cache_duration:
                logger.debug("Using cached MEXC calendar data")
                return self._cache

        self._cache = fetch_mexc_calendar(days_ahead=days)
        self._cache_time = time.time()
        return self._cache

    def find_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Find a specific token in upcoming listings.

        Args:
            symbol: Token symbol to find

        Returns:
            Listing dict or None
        """
        listings = self.get_upcoming()
        symbol_upper = symbol.upper()

        for listing in listings:
            if listing.get('symbol', '').upper() == symbol_upper:
                return listing

        return None


# =============================================================================
# Token Price Fetcher
# =============================================================================

def fetch_token_price(
    symbol: str,
    source: str = "auto",
    include_24h_change: bool = True
) -> Optional[Dict[str, Any]]:
    """
    Fetch current token price from multiple sources.

    Args:
        symbol: Token symbol (e.g., "BTC", "ETH")
        source: Price source ('auto', 'coingecko', 'binance', 'mexc')
        include_24h_change: Include 24h price change

    Returns:
        Dict with:
        - price: Current price in USD
        - change_24h: 24h percentage change
        - volume_24h: 24h trading volume
        - source: Data source used
    """
    from scripts.helpers.web_price_fetcher import fetch_token_price as _fetch
    return _fetch(
        symbol=symbol,
        source=source,
        include_24h_change=include_24h_change
    )


def fetch_token_price_interactive(
    symbol: str,
    verbose: bool = False
) -> Optional[float]:
    """
    Fetch token price with interactive feedback.

    Designed for CLI usage with progress indicators.

    Args:
        symbol: Token symbol
        verbose: Show detailed progress

    Returns:
        Current price as float or None
    """
    from scripts.helpers.fetch_token_price_interactive import fetch_price as _fetch
    return _fetch(symbol, verbose=verbose)


# =============================================================================
# MEXC Twitter Fetcher
# =============================================================================

def fetch_mexc_twitter(
    limit: int = 50,
    filter_listings: bool = True
) -> List[Dict[str, Any]]:
    """
    Fetch MEXC listing announcements from Twitter.

    Args:
        limit: Maximum tweets to fetch
        filter_listings: Only return listing announcements

    Returns:
        List of announcement dicts with:
        - tweet_id: Twitter ID
        - text: Tweet text
        - symbol: Extracted token symbol (if any)
        - listing_date: Extracted date (if any)
        - created_at: Tweet timestamp
    """
    from scripts.helpers.mexc_twitter_fetcher import fetch_mexc_twitter as _fetch
    return _fetch(limit=limit, filter_listings=filter_listings)


# =============================================================================
# ChainGPT Pad Fetcher
# =============================================================================

def fetch_chaingpt_pad(
    include_ended: bool = False
) -> List[Dict[str, Any]]:
    """
    Fetch upcoming IDOs from ChainGPT Pad.

    Args:
        include_ended: Include ended IDOs

    Returns:
        List of IDO dicts with token details
    """
    from scripts.helpers.chaingpt_pad_fetcher import fetch_chaingpt_pad as _fetch
    return _fetch(include_ended=include_ended)


# =============================================================================
# Social Hype Fetcher
# =============================================================================

def fetch_social_hype(
    symbol: str,
    token_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetch social media hype metrics for a token.

    Aggregates data from Twitter, Reddit, Telegram.

    Args:
        symbol: Token symbol
        token_name: Token name for better search

    Returns:
        Dict with:
        - twitter_mentions: Mention count
        - reddit_posts: Post count
        - telegram_members: Group size (if known)
        - hype_score: Normalized 0-100 score
    """
    from scripts.helpers.social_hype_fetcher import fetch_social_hype as _fetch
    return _fetch(symbol, token_name)


# =============================================================================
# Module exports
# =============================================================================

__all__ = [
    'fetch_mexc_calendar',
    'fetch_token_price',
    'fetch_token_price_interactive',
    'fetch_mexc_twitter',
    'fetch_chaingpt_pad',
    'fetch_social_hype',
    'MEXCCalendarFetcher',
]
