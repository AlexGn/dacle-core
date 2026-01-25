#!/usr/bin/env python3
"""
Macro Indicators Fetcher - Phase 3 Task 1

Fetches DXY (US Dollar Index) daily change % and VIX (Volatility Index) level
using Yahoo Finance API (free, no API key required).

Purpose:
- Provide macro context for BQS (Breakout Quality Score) calculations
- DXY strength impacts risk-on/risk-off sentiment for crypto
- VIX level indicates market fear/volatility expectations

API Source: Yahoo Finance (free, unlimited)
- DXY symbol: DX-Y.NYB
- VIX symbol: ^VIX

Data Format:
    MacroContext(
        dxy_change_pct=-1.20,   # Daily change % (negative = weaker dollar = good for crypto)
        vix_level=16.09,        # Current VIX (higher = more fear)
        timestamp=datetime,
        source="YAHOO" | "CACHED" | "UNAVAILABLE"
    )

Usage:
    from src.data.macro_indicators import fetch_macro_context, MacroContext

    context = fetch_macro_context()
    if context.dxy_change_pct is not None:
        print(f"DXY: {context.dxy_change_pct:+.2f}%")
    if context.vix_level is not None:
        print(f"VIX: {context.vix_level:.1f}")

Session 348: Phase 3 Task 1 - BQS Integration
Updated: Switched from FMP (deprecated) to Yahoo Finance (free)
Author: Claude Code
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Cache configuration
CACHE_TTL_SECONDS = 15 * 60  # 15 minutes

# Module-level cache
_cache: Optional['MacroContext'] = None
_cache_timestamp: Optional[float] = None

# Yahoo Finance API headers
YAHOO_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}


@dataclass
class MacroContext:
    """
    Macro market context data.

    Attributes:
        dxy_change_pct: DXY daily change percentage (e.g., 0.35 or -0.20)
        vix_level: Current VIX level (e.g., 18.5)
        timestamp: When the data was fetched
        source: Data source ("YAHOO", "CACHED", or "UNAVAILABLE")
    """
    dxy_change_pct: Optional[float]
    vix_level: Optional[float]
    timestamp: datetime
    source: str  # "YAHOO" | "CACHED" | "UNAVAILABLE"


def _make_yahoo_request(symbol: str, timeout: int = 10) -> Optional[dict]:
    """
    Make a request to Yahoo Finance API for chart data.

    Args:
        symbol: The symbol to fetch (e.g., "DX-Y.NYB" or "^VIX")
        timeout: Request timeout in seconds

    Returns:
        Dict with meta data or None on failure
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"

    try:
        response = requests.get(url, headers=YAHOO_HEADERS, timeout=timeout)

        if response.status_code != 200:
            logger.warning(f"Yahoo Finance API returned status {response.status_code} for {symbol}")
            return None

        data = response.json()
        result = data.get('chart', {}).get('result', [])

        if not result:
            logger.debug(f"Yahoo Finance API returned empty data for {symbol}")
            return None

        return result[0].get('meta', {})

    except requests.exceptions.Timeout:
        logger.warning(f"Yahoo Finance API timeout for {symbol}")
        return None
    except requests.exceptions.RequestException as e:
        logger.warning(f"Yahoo Finance API request error for {symbol}: {e}")
        return None
    except ValueError as e:
        logger.warning(f"Yahoo Finance API JSON decode error for {symbol}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Yahoo Finance API unexpected error for {symbol}: {e}")
        return None


def fetch_dxy_change_pct() -> Optional[float]:
    """
    Fetch DXY (US Dollar Index) daily change percentage.

    Returns:
        Daily change percentage (e.g., -1.20 for -1.20%) or None on failure
    """
    try:
        data = _make_yahoo_request("DX-Y.NYB")

        if data is None:
            return None

        # Get current price and previous close
        current_price = data.get('regularMarketPrice')
        previous_close = data.get('chartPreviousClose') or data.get('previousClose')

        if current_price is None or previous_close is None:
            logger.debug("DXY data missing price or previous close")
            return None

        # Calculate change percentage
        change_pct = ((current_price - previous_close) / previous_close) * 100
        return round(change_pct, 2)

    except Exception as e:
        logger.warning(f"Error calculating DXY change: {e}")
        return None


def fetch_vix_level() -> Optional[float]:
    """
    Fetch current VIX (Volatility Index) level.

    Returns:
        Current VIX level (e.g., 16.09) or None on failure
    """
    try:
        data = _make_yahoo_request("^VIX")

        if data is None:
            return None

        vix_level = data.get('regularMarketPrice')

        if vix_level is None:
            logger.debug("VIX data missing price")
            return None

        return round(float(vix_level), 2)

    except Exception as e:
        logger.warning(f"Error fetching VIX: {e}")
        return None


def fetch_macro_context() -> MacroContext:
    """
    Fetch complete macro context (DXY change + VIX level).

    Uses caching to avoid redundant API calls within 15 minutes.

    Returns:
        MacroContext with DXY change %, VIX level, timestamp, and source
    """
    global _cache, _cache_timestamp

    # Check cache
    current_time = time.time()
    if _cache is not None and _cache_timestamp is not None:
        age = current_time - _cache_timestamp
        if age < CACHE_TTL_SECONDS:
            logger.debug(f"Using cached macro context (age: {age:.0f}s)")
            return MacroContext(
                dxy_change_pct=_cache.dxy_change_pct,
                vix_level=_cache.vix_level,
                timestamp=_cache.timestamp,
                source="CACHED"
            )

    # Fetch fresh data
    dxy = fetch_dxy_change_pct()
    vix = fetch_vix_level()

    # Determine source
    if dxy is not None or vix is not None:
        source = "YAHOO"
    else:
        source = "UNAVAILABLE"

    context = MacroContext(
        dxy_change_pct=dxy,
        vix_level=vix,
        timestamp=datetime.now(),
        source=source
    )

    # Update cache
    _cache = context
    _cache_timestamp = current_time

    return context


def get_macro_risk_assessment() -> Tuple[str, str]:
    """
    Get a simple risk assessment based on macro conditions.

    Convenience function for BQS integration.

    Returns:
        Tuple of (risk_level, description)
        - risk_level: "LOW", "MEDIUM", "HIGH", "UNKNOWN"
        - description: Human-readable explanation
    """
    ctx = fetch_macro_context()

    if ctx.source == "UNAVAILABLE":
        return ("UNKNOWN", "Macro data unavailable")

    risk_factors = []

    # DXY assessment (negative = weaker dollar = good for crypto)
    if ctx.dxy_change_pct is not None:
        if ctx.dxy_change_pct > 0.5:
            risk_factors.append(f"DXY strengthening ({ctx.dxy_change_pct:+.2f}%)")
        elif ctx.dxy_change_pct < -0.5:
            # Weaker dollar is generally good for crypto
            pass

    # VIX assessment
    if ctx.vix_level is not None:
        if ctx.vix_level >= 30:
            risk_factors.append(f"VIX crisis level ({ctx.vix_level:.1f})")
        elif ctx.vix_level >= 20:
            risk_factors.append(f"VIX elevated ({ctx.vix_level:.1f})")

    if len(risk_factors) >= 2:
        return ("HIGH", "; ".join(risk_factors))
    elif len(risk_factors) == 1:
        return ("MEDIUM", risk_factors[0])
    else:
        return ("LOW", "Macro conditions stable")


def clear_cache() -> None:
    """Clear the macro context cache. Useful for testing."""
    global _cache, _cache_timestamp
    _cache = None
    _cache_timestamp = None
