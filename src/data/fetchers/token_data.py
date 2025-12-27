"""
Token Data Fetchers Module

Consolidates token data fetching from multiple sources:
- CryptoRank (web scraping when API fails)
- ICODrops (vesting, whitepaper, farming)
- Dropstab (tokenomics, unlocks)
- CoinGecko (primary source)
- CoinMarketCap (fallback source)

This module provides a unified interface for fetching token data.
The actual implementation is delegated to the original helper files
during the migration period.

Session 260 Update: Health monitoring integrated for all fetchers.

Usage:
    from src.data.fetchers.token_data import (
        fetch_cryptorank_web,
        fetch_icodrops_data,
        fetch_from_primary_sources,
    )

    # Fetch from CryptoRank website
    data = fetch_cryptorank_web("RAYLS")

    # Fetch from ICODrops
    data = fetch_icodrops_data("SEEK", "Talisman")

    # Fetch from primary sources (auto-cascade)
    data = fetch_from_primary_sources("BTC", token_name="Bitcoin")
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure scripts.helpers is importable
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logger = logging.getLogger(__name__)

# Session 260: Health monitoring integration
from src.monitoring.data_source_health import get_monitor
_health_monitor = get_monitor()


# =============================================================================
# CryptoRank Web Fetcher
# =============================================================================

def fetch_cryptorank_web(
    token: str,
    url: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch token data directly from CryptoRank website HTML.

    This is the fallback when CryptoRank API doesn't have the token indexed
    (common for pre-TGE tokens).

    Args:
        token: Token symbol (e.g., "RAYLS")
        url: Optional explicit URL (if None, tries common patterns)

    Returns:
        Dict with TGE data, or None if not found
    """
    with _health_monitor.track_call("cryptorank_web"):
        from scripts.helpers.cryptorank_web_fetcher import fetch_cryptorank_web as _fetch
        return _fetch(token, url)


# =============================================================================
# ICODrops Fetcher
# =============================================================================

def fetch_icodrops_data(
    symbol: str,
    name: str = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch token data from ICODrops.com

    Args:
        symbol: Token symbol (e.g., "SEEK", "MONAD")
        name: Token name (e.g., "Talisman") - helps with URL matching

    Returns:
        Dict with vesting, whitepaper, farming, and tokenomics data
    """
    with _health_monitor.track_call("icodrops_scraper"):
        from scripts.helpers.icodrops_fetcher import fetch_icodrops_data as _fetch
        return _fetch(symbol, name)


# =============================================================================
# Dropstab Fetcher
# =============================================================================

def fetch_dropstab_data(
    symbol: str,
    name: str = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch token data from Dropstab (unlock schedules, tokenomics).

    Args:
        symbol: Token symbol
        name: Token name (helps with matching)

    Returns:
        Dict with unlock schedule, tokenomics data
    """
    with _health_monitor.track_call("dropstab_scraper"):
        from scripts.helpers.dropstab_fetcher import fetch_dropstab_data as _fetch
        return _fetch(symbol, name)


# =============================================================================
# Primary Source Fetcher (Multi-source cascade)
# =============================================================================

def fetch_from_primary_sources(
    token: str,
    token_name: Optional[str] = None,
    tge_date: Optional[str] = None,
    category: Optional[str] = None,
    min_confidence: int = 60,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Fetch token data from primary sources with automatic cascade.

    Priority order:
    1. CryptoRank API
    2. CoinGecko API
    3. CoinMarketCap API (if name mismatch on CoinGecko)

    Args:
        token: Token symbol (e.g., "BTC", "ETH")
        token_name: Expected token name for disambiguation
        tge_date: TGE date for context
        category: Token category for validation
        min_confidence: Minimum confidence threshold (0-100)
        force_refresh: Bypass cache and fetch fresh data

    Returns:
        Dict with consolidated token data from best available source
    """
    with _health_monitor.track_call("primary_source"):
        from scripts.helpers.primary_source_fetcher import fetch_from_primary_sources as _fetch
        return _fetch(
            token=token,
            token_name=token_name,
            tge_date=tge_date,
            category=category,
            min_confidence=min_confidence,
            force_refresh=force_refresh,
        )


def fetch_coingecko(
    token: str,
    token_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch token data from CoinGecko API.

    Args:
        token: Token symbol
        token_name: Expected token name for disambiguation

    Returns:
        Dict with CoinGecko data or None
    """
    with _health_monitor.track_call("coingecko_api"):
        from scripts.helpers.primary_source_fetcher import fetch_coingecko as _fetch
        return _fetch(token, token_name)


def fetch_coinmarketcap(
    token: str,
    token_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch token data from CoinMarketCap API.

    Args:
        token: Token symbol
        token_name: Expected token name for disambiguation

    Returns:
        Dict with CMC data or None
    """
    with _health_monitor.track_call("coinmarketcap_api"):
        from scripts.helpers.primary_source_fetcher import fetch_coinmarketcap as _fetch
        return _fetch(token, token_name)


# =============================================================================
# Class-based interface (for advanced usage)
# =============================================================================

class PrimarySourceFetcher:
    """
    Class-based interface for primary source data fetching.

    Provides stateful fetching with caching and configuration.

    Usage:
        fetcher = PrimarySourceFetcher()
        data = fetcher.fetch("BTC", token_name="Bitcoin")
        data = fetcher.fetch("ETH", token_name="Ethereum")
    """

    def __init__(self, min_confidence: int = 60):
        """
        Initialize fetcher with configuration.

        Args:
            min_confidence: Minimum confidence threshold for data
        """
        self.min_confidence = min_confidence
        self._cache: Dict[str, Dict] = {}

    def fetch(
        self,
        token: str,
        token_name: Optional[str] = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Fetch token data with caching.

        Args:
            token: Token symbol
            token_name: Expected token name
            force_refresh: Bypass cache

        Returns:
            Token data dict
        """
        cache_key = f"{token}:{token_name or ''}"

        if not force_refresh and cache_key in self._cache:
            logger.debug(f"Cache hit for {cache_key}")
            return self._cache[cache_key]

        data = fetch_from_primary_sources(
            token=token,
            token_name=token_name,
            min_confidence=self.min_confidence,
            force_refresh=force_refresh,
        )

        self._cache[cache_key] = data
        return data

    def clear_cache(self):
        """Clear the internal cache."""
        self._cache.clear()
        logger.debug("PrimarySourceFetcher cache cleared")


# =============================================================================
# Module exports
# =============================================================================

__all__ = [
    'fetch_cryptorank_web',
    'fetch_icodrops_data',
    'fetch_dropstab_data',
    'fetch_from_primary_sources',
    'fetch_coingecko',
    'fetch_coinmarketcap',
    'PrimarySourceFetcher',
]
