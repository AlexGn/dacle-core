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
    from dacle_core.data.macro_indicators import fetch_macro_context, MacroContext

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


# NOTE: yfinance >= 0.2.31 uses curl_cffi internally and rejects
# external requests.Session objects.  Let yfinance manage its own
# session — the old custom-session approach caused 100% failure on VPS.

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



# Disk cache configuration
import json
from pathlib import Path

DISK_CACHE_DIR = Path("data/cache")
DISK_CACHE_FILE = DISK_CACHE_DIR / "macro_context.json"
MAX_STALE_AGE_HOURS = 24  # Allow loading stale data up to 24 hours old

def save_cache_to_disk(context: MacroContext):
    """Save valid macro context to disk."""
    try:
        DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
        data = {
            "dxy_change_pct": context.dxy_change_pct,
            "vix_level": context.vix_level,
            "timestamp": context.timestamp.timestamp(),
            "source": context.source   
        }
        
        with open(DISK_CACHE_FILE, 'w') as f:
            json.dump(data, f)
            
    except Exception as e:
        logger.warning(f"Failed to save macro cache to disk: {e}")

def load_cache_from_disk() -> Optional[MacroContext]:
    """Load macro context from disk if available."""
    try:
        if not DISK_CACHE_FILE.exists():
            return None
            
        with open(DISK_CACHE_FILE, 'r') as f:
            data = json.load(f)
            
        ts = datetime.fromtimestamp(data["timestamp"])
        
        # Check if too old (absolute safety limit)
        if (datetime.now() - ts).total_seconds() > MAX_STALE_AGE_HOURS * 3600:
            logger.warning("Disk cache too old, ignoring")
            return None
            
        return MacroContext(
            dxy_change_pct=data.get("dxy_change_pct"),
            vix_level=data.get("vix_level"),
            timestamp=ts,
            source="STALE" # Mark as stale when loading from disk fallback
        )
            
    except Exception as e:
        logger.warning(f"Failed to load macro cache from disk: {e}")
        return None

def fetch_macro_context() -> MacroContext:
    """
    Fetch complete macro context (DXY change + VIX level).

    Strategy:
    1. Check memory cache (fastest)
    2. Try live fetch (freshness) -> Save to disk on success
    3. Fallback to disk cache (robustness)
    4. Return UNAVAILABLE if all fail

    Returns:
        MacroContext with DXY change %, VIX level, timestamp, and source
    """
    global _cache, _cache_timestamp

    # 1. Check memory cache
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

    # 2. Try  Fetch fresh data
    dxy = None
    vix = None
    fetch_success = False
    
    try:
        dxy = fetch_dxy_change_pct()
    except Exception as e:
        logger.warning(f"Failed to fetch DXY after retries: {e}")

    try:
        vix = fetch_vix_level()
    except Exception as e:
        logger.warning(f"Failed to fetch VIX after retries: {e}")

    # Determine if live fetch succeeded (partial success is okay)
    if dxy is not None or vix is not None:
        source = "YAHOO"
        fetch_success = True
    else:
        source = "UNAVAILABLE"

    # Construct context
    context = MacroContext(
        dxy_change_pct=dxy,
        vix_level=vix,
        timestamp=datetime.now(),
        source=source
    )

    # If successfully fetched, save to both caches
    if fetch_success:
        _cache = context
        _cache_timestamp = current_time
        save_cache_to_disk(context)
        return context
    
    # 3. Fallback to disk cache if live fetch failed
    logger.warning("Live macro fetch failed, attempting disk fallback...")
    disk_context = load_cache_from_disk()
    
    if disk_context:
        logger.info(f"Loaded macro context from disk (timestamp: {disk_context.timestamp})")
        # Update memory cache with the disk data so we don't hit disk every time
        _cache = disk_context
        _cache_timestamp = current_time 
        return disk_context
        
    # 4. Total failure
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
