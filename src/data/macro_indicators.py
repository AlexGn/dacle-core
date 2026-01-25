#!/usr/bin/env python3
"""
Macro Indicators Fetcher - Phase 3 Task 1

Fetches DXY (US Dollar Index) daily change % and VIX (Volatility Index) level
using the Financial Modeling Prep (FMP) API.

Purpose:
- Provide macro context for BQS (Baseline Quality Score) calculations
- DXY strength impacts risk-on/risk-off sentiment for crypto
- VIX level indicates market fear/volatility expectations

API Source: Financial Modeling Prep (FMP)
- Free tier: 250 calls/day
- DXY symbol: DX-Y.NYB
- VIX symbol: ^VIX (URL encoded as %5EVIX)

Data Format:
    MacroContext(
        dxy_change_pct=0.35,    # Daily change % (positive = stronger dollar)
        vix_level=18.5,         # Current VIX (higher = more fear)
        timestamp=datetime,
        source="FMP" | "CACHED" | "UNAVAILABLE"
    )

Usage:
    from src.data.macro_indicators import fetch_macro_context, MacroContext

    context = fetch_macro_context()
    if context.dxy_change_pct is not None:
        print(f"DXY: {context.dxy_change_pct:+.2f}%")
    if context.vix_level is not None:
        print(f"VIX: {context.vix_level:.1f}")

Session 348: Phase 3 Task 1 - BQS Integration
Author: Claude Code
"""

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# Cache configuration
CACHE_TTL_SECONDS = 15 * 60  # 15 minutes

# Module-level cache
_cache: Optional['MacroContext'] = None
_cache_timestamp: Optional[float] = None


@dataclass
class MacroContext:
    """
    Macro market context data.

    Attributes:
        dxy_change_pct: DXY daily change percentage (e.g., 0.35 or -0.20)
        vix_level: Current VIX level (e.g., 18.5)
        timestamp: When the data was fetched
        source: Data source ("FMP", "CACHED", or "UNAVAILABLE")
    """
    dxy_change_pct: Optional[float]
    vix_level: Optional[float]
    timestamp: datetime
    source: str  # "FMP" | "CACHED" | "UNAVAILABLE"


def _get_api_key() -> Optional[str]:
    """Get FMP API key from environment."""
    return os.environ.get('FMP_API_KEY')


def _make_fmp_request(symbol: str, timeout: int = 10) -> Optional[dict]:
    """
    Make a request to FMP API for quote data.

    Args:
        symbol: The symbol to fetch (e.g., "DX-Y.NYB" or "^VIX")
        timeout: Request timeout in seconds

    Returns:
        Dict with quote data or None on failure
    """
    api_key = _get_api_key()

    # URL encode special characters (^VIX needs encoding)
    encoded_symbol = quote(symbol, safe='')

    if api_key:
        url = f"https://financialmodelingprep.com/api/v3/quote/{encoded_symbol}?apikey={api_key}"
    else:
        # Try without API key (limited access)
        url = f"https://financialmodelingprep.com/api/v3/quote/{encoded_symbol}"
        logger.debug("No FMP_API_KEY found, trying without authentication")

    try:
        response = requests.get(url, timeout=timeout)

        if response.status_code == 401:
            logger.warning(f"FMP API unauthorized for {symbol} - check API key")
            return None

        if response.status_code != 200:
            logger.warning(f"FMP API returned status {response.status_code} for {symbol}")
            return None

        data = response.json()

        if not data or not isinstance(data, list) or len(data) == 0:
            logger.debug(f"FMP API returned empty data for {symbol}")
            return None

        return data[0]

    except requests.exceptions.Timeout:
        logger.warning(f"FMP API timeout for {symbol}")
        return None
    except requests.exceptions.RequestException as e:
        logger.warning(f"FMP API request error for {symbol}: {e}")
        return None
    except ValueError as e:
        logger.warning(f"FMP API JSON decode error for {symbol}: {e}")
        return None
    except Exception as e:
        logger.warning(f"FMP API unexpected error for {symbol}: {e}")
        return None


def fetch_dxy_change_pct() -> Optional[float]:
    """
    Fetch DXY (US Dollar Index) daily change percentage.

    Returns:
        Daily change percentage (e.g., 0.35 for +0.35%) or None on failure
    """
    try:
        data = _make_fmp_request("DX-Y.NYB")

        if data is None:
            return None

        # Try to get changesPercentage directly
        if 'changesPercentage' in data:
            return float(data['changesPercentage'])

        # Fallback: calculate from price and previousClose
        price = data.get('price')
        prev_close = data.get('previousClose')

        if price is not None and prev_close is not None and prev_close > 0:
            return ((float(price) - float(prev_close)) / float(prev_close)) * 100

        return None

    except Exception as e:
        logger.warning(f"Failed to fetch DXY: {e}")
        return None


def fetch_vix_level() -> Optional[float]:
    """
    Fetch current VIX (Volatility Index) level.

    Returns:
        Current VIX level (e.g., 18.5) or None on failure
    """
    try:
        data = _make_fmp_request("^VIX")

        if data is None:
            return None

        if 'price' in data:
            return float(data['price'])

        return None

    except Exception as e:
        logger.warning(f"Failed to fetch VIX: {e}")
        return None


def fetch_macro_context(use_cache: bool = True) -> MacroContext:
    """
    Fetch combined macro context (DXY + VIX).

    Implements caching to avoid hitting rate limits.
    Always returns a MacroContext, never raises exceptions.

    Args:
        use_cache: Whether to use cached data if available (default: True)

    Returns:
        MacroContext with DXY change %, VIX level, timestamp, and source
    """
    global _cache, _cache_timestamp

    # Check cache
    if use_cache and _cache is not None and _cache_timestamp is not None:
        cache_age = time.time() - _cache_timestamp
        if cache_age < CACHE_TTL_SECONDS:
            logger.debug(f"Returning cached macro context (age: {cache_age:.0f}s)")
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
    if dxy is None and vix is None:
        source = "UNAVAILABLE"
    else:
        source = "FMP"

    context = MacroContext(
        dxy_change_pct=dxy,
        vix_level=vix,
        timestamp=datetime.utcnow(),
        source=source
    )

    # Update cache
    _cache = context
    _cache_timestamp = time.time()

    return context


def _clear_cache() -> None:
    """Clear the module-level cache. Used for testing."""
    global _cache, _cache_timestamp
    _cache = None
    _cache_timestamp = None


def _get_cache_age() -> Optional[float]:
    """Get cache age in seconds. Used for testing."""
    global _cache_timestamp
    if _cache_timestamp is None:
        return None
    return time.time() - _cache_timestamp


def get_macro_risk_assessment(context: Optional[MacroContext] = None) -> dict:
    """
    Assess macro risk level based on DXY and VIX.

    This is a convenience function for BQS integration.

    Args:
        context: MacroContext to assess (fetches if not provided)

    Returns:
        Dict with:
            - risk_level: "LOW", "MODERATE", "HIGH", "EXTREME"
            - dxy_signal: "BULLISH_CRYPTO" (DXY down), "BEARISH_CRYPTO" (DXY up), "NEUTRAL"
            - vix_signal: "LOW_FEAR", "MODERATE_FEAR", "HIGH_FEAR", "EXTREME_FEAR"
            - confidence: 0.0-1.0 (based on data availability)
    """
    if context is None:
        context = fetch_macro_context()

    result = {
        "risk_level": "MODERATE",
        "dxy_signal": "NEUTRAL",
        "vix_signal": "MODERATE_FEAR",
        "confidence": 0.0,
        "dxy_change_pct": context.dxy_change_pct,
        "vix_level": context.vix_level,
        "data_source": context.source
    }

    confidence = 0.0

    # Assess DXY (stronger dollar = worse for crypto)
    if context.dxy_change_pct is not None:
        confidence += 0.5
        if context.dxy_change_pct > 0.5:
            result["dxy_signal"] = "BEARISH_CRYPTO"
        elif context.dxy_change_pct < -0.5:
            result["dxy_signal"] = "BULLISH_CRYPTO"
        else:
            result["dxy_signal"] = "NEUTRAL"

    # Assess VIX (higher = more fear)
    if context.vix_level is not None:
        confidence += 0.5
        if context.vix_level < 15:
            result["vix_signal"] = "LOW_FEAR"
        elif context.vix_level < 20:
            result["vix_signal"] = "MODERATE_FEAR"
        elif context.vix_level < 30:
            result["vix_signal"] = "HIGH_FEAR"
        else:
            result["vix_signal"] = "EXTREME_FEAR"

    result["confidence"] = confidence

    # Combined risk assessment
    if context.vix_level is not None and context.dxy_change_pct is not None:
        if context.vix_level >= 30 or context.dxy_change_pct > 1.0:
            result["risk_level"] = "EXTREME"
        elif context.vix_level >= 25 or context.dxy_change_pct > 0.7:
            result["risk_level"] = "HIGH"
        elif context.vix_level >= 20 or context.dxy_change_pct > 0.3:
            result["risk_level"] = "MODERATE"
        else:
            result["risk_level"] = "LOW"
    elif context.source == "UNAVAILABLE":
        result["risk_level"] = "UNKNOWN"

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("\n" + "=" * 60)
    print("MACRO INDICATORS - Phase 3 Task 1")
    print("=" * 60 + "\n")

    print("Fetching macro context...")
    context = fetch_macro_context()

    print(f"\nSource: {context.source}")
    print(f"Timestamp: {context.timestamp.isoformat()}")

    if context.dxy_change_pct is not None:
        print(f"DXY Change: {context.dxy_change_pct:+.2f}%")
    else:
        print("DXY Change: N/A")

    if context.vix_level is not None:
        print(f"VIX Level: {context.vix_level:.1f}")
    else:
        print("VIX Level: N/A")

    print("\nRisk Assessment:")
    assessment = get_macro_risk_assessment(context)
    print(f"  Risk Level: {assessment['risk_level']}")
    print(f"  DXY Signal: {assessment['dxy_signal']}")
    print(f"  VIX Signal: {assessment['vix_signal']}")
    print(f"  Confidence: {assessment['confidence']:.1%}")

    print("\n" + "=" * 60 + "\n")
