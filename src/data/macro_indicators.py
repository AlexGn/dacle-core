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

# YFinance integration
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

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
        source: Data source ("YAHOO", "CACHED", or "UNAVAILABLE")
    """
    dxy_change_pct: Optional[float]
    vix_level: Optional[float]
    timestamp: datetime
    source: str  # "YAHOO" | "CACHED" | "UNAVAILABLE"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception)
)
def fetch_dxy_change_pct() -> Optional[float]:
    """
    Fetch DXY (US Dollar Index) daily change percentage using yfinance.

    Returns:
        Daily change percentage (e.g., -1.20 for -1.20%) or None on failure
    """
    try:
        # Fetch 5 days of history to ensure we get previous close
        # "DX-Y.NYB" is the symbol for US Dollar Index on Yahoo Finance
        ticker = yf.Ticker("DX-Y.NYB")
        hist = ticker.history(period="5d")

        if hist.empty or len(hist) < 2:
            logger.warning("Empty DXY history from yfinance")
            return None

        # Get latest close and previous close
        # iloc[-1] is today/latest, iloc[-2] is previous trading day
        current_price = hist['Close'].iloc[-1]
        previous_close = hist['Close'].iloc[-2]

        if not current_price or not previous_close:
            return None

        # Calculate change percentage
        change_pct = ((current_price - previous_close) / previous_close) * 100
        return round(change_pct, 2)

    except Exception as e:
        logger.warning(f"Error fetching DXY with yfinance: {e}")
        raise e  # Raise to trigger retry


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception)
)
def fetch_vix_level() -> Optional[float]:
    """
    Fetch current VIX (Volatility Index) level using yfinance.

    Returns:
        Current VIX level (e.g., 16.09) or None on failure
    """
    try:
        # "^VIX" is the symbol for CBOE Volatility Index
        ticker = yf.Ticker("^VIX")
        # For VIX, we just need the latest price
        # fast_info often has it, or we can use history
        
        # Try history first (most reliable for "current" level)
        hist = ticker.history(period="1d")
        
        if not hist.empty:
            vix_level = hist['Close'].iloc[-1]
            return round(float(vix_level), 2)
            
        # Fallback to info (slower but sometimes works if history fails)
        info = ticker.info
        vix_level = info.get('regularMarketPrice') or info.get('previousClose')
        
        if vix_level is not None:
             return round(float(vix_level), 2)
             
        logger.warning("No VIX data found in history or info")
        return None

    except Exception as e:
        logger.warning(f"Error fetching VIX with yfinance: {e}")
        raise e  # Raise to trigger retry


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
    try:
        dxy = fetch_dxy_change_pct()
    except Exception as e:
        logger.warning(f"Failed to fetch DXY after retries: {e}")
        dxy = None

    try:
        vix = fetch_vix_level()
    except Exception as e:
        logger.warning(f"Failed to fetch VIX after retries: {e}")
        vix = None

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
