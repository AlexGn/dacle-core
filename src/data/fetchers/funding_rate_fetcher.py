#!/usr/bin/env python3
"""
SESSION 241: Funding Rate Fetcher

DEPRECATED: Use src.data.fetchers.exchange module instead.
Session 256: Marked for migration to src/data/fetchers/exchange.py

Fetches live funding rates from Binance/Bybit for crowded trade detection.

Gemini Recommendation (Session 240):
- If funding < -0.05% per 8h = crowded short, apply -3 penalty
- If funding < -0.1% per 8h = HARD VETO regardless of conviction

Usage:
    from src.data.fetchers.exchange import get_funding_rate, assess_crowded_trade

Author: Session 241 - Gemini P1 Implementation
"""

import warnings
warnings.warn(
    "scripts.helpers.funding_rate_fetcher is deprecated. "
    "Use src.data.fetchers.exchange module instead.",
    DeprecationWarning,
    stacklevel=2
)

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple
import json

# Add project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Try to import ccxt for exchange API access
try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False
    print("[Funding] Warning: ccxt not available, using fallback")

# Try requests for direct API calls
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


def get_funding_rate_binance(symbol: str) -> Optional[Dict]:
    """
    Fetch current funding rate from Binance Futures.

    Args:
        symbol: Token symbol (e.g., "POWER", "MONAD")

    Returns:
        Dict with funding_rate, next_funding_time, or None if not available
    """
    if not REQUESTS_AVAILABLE:
        return None

    # Try different symbol formats
    symbols_to_try = [
        f"{symbol}USDT",
        f"{symbol.upper()}USDT",
        f"{symbol}USDC",
    ]

    for pair in symbols_to_try:
        try:
            url = f"https://fapi.binance.com/fapi/v1/fundingRate"
            params = {"symbol": pair, "limit": 1}
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if data:
                    latest = data[0]
                    # Funding rate is in decimal (e.g., 0.0001 = 0.01%)
                    funding_rate = float(latest.get("fundingRate", 0)) * 100

                    # Get premium index for more context
                    premium_url = "https://fapi.binance.com/fapi/v1/premiumIndex"
                    premium_resp = requests.get(premium_url, params={"symbol": pair}, timeout=10)
                    next_funding_time = None
                    if premium_resp.status_code == 200:
                        premium_data = premium_resp.json()
                        next_funding_time = premium_data.get("nextFundingTime")

                    return {
                        "symbol": pair,
                        "funding_rate_8h": funding_rate,  # Per 8h funding rate as %
                        "funding_rate_daily": funding_rate * 3,  # Annualized daily
                        "next_funding_time": next_funding_time,
                        "source": "binance",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
        except Exception as e:
            continue

    return None


def get_funding_rate_bybit(symbol: str) -> Optional[Dict]:
    """
    Fetch current funding rate from Bybit.

    Args:
        symbol: Token symbol (e.g., "POWER", "MONAD")

    Returns:
        Dict with funding_rate or None if not available
    """
    if not REQUESTS_AVAILABLE:
        return None

    symbols_to_try = [
        f"{symbol}USDT",
        f"{symbol.upper()}USDT",
    ]

    for pair in symbols_to_try:
        try:
            url = "https://api.bybit.com/v5/market/funding/history"
            params = {"category": "linear", "symbol": pair, "limit": 1}
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if data.get("result", {}).get("list"):
                    latest = data["result"]["list"][0]
                    funding_rate = float(latest.get("fundingRate", 0)) * 100

                    return {
                        "symbol": pair,
                        "funding_rate_8h": funding_rate,
                        "funding_rate_daily": funding_rate * 3,
                        "source": "bybit",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
        except Exception as e:
            continue

    return None


def get_funding_rate(symbol: str) -> Optional[Dict]:
    """
    Get funding rate from available exchanges.

    Tries Binance first, then Bybit.

    Args:
        symbol: Token symbol

    Returns:
        Dict with funding rate data or None
    """
    # Try Binance first (most liquid)
    result = get_funding_rate_binance(symbol)
    if result:
        return result

    # Fallback to Bybit
    result = get_funding_rate_bybit(symbol)
    if result:
        return result

    return None


def assess_crowded_trade(funding_rate_8h: float) -> Dict:
    """
    Assess if trade is crowded based on funding rate.

    Gemini Session 240 Thresholds:
    - < -0.05% per 8h: Crowded short, -3 penalty
    - < -0.1% per 8h: HARD VETO (squeeze is statistical probability)

    Args:
        funding_rate_8h: Funding rate per 8h as percentage

    Returns:
        Dict with crowded assessment
    """
    if funding_rate_8h >= -0.01:
        # Positive or near-zero funding = longs paying shorts
        return {
            "crowded": False,
            "severity": "NONE",
            "penalty": 0,
            "veto": False,
            "message": "Neutral funding - trade not crowded",
            "action": "PROCEED"
        }

    elif funding_rate_8h >= -0.05:
        # Mild negative funding
        return {
            "crowded": True,
            "severity": "LOW",
            "penalty": -1,
            "veto": False,
            "message": f"Slightly negative funding ({funding_rate_8h:.3f}%) - minor crowd",
            "action": "PROCEED_WITH_CAUTION"
        }

    elif funding_rate_8h >= -0.1:
        # Crowded short per Gemini threshold
        return {
            "crowded": True,
            "severity": "HIGH",
            "penalty": -3,
            "veto": False,
            "message": f"CROWDED SHORT ({funding_rate_8h:.3f}% per 8h) - squeeze risk elevated",
            "action": "REDUCE_POSITION_50"
        }

    else:
        # Extremely crowded - HARD VETO per Gemini
        return {
            "crowded": True,
            "severity": "EXTREME",
            "penalty": -10,  # Effective VETO
            "veto": True,
            "message": f"EXTREMELY CROWDED ({funding_rate_8h:.3f}% per 8h) - SQUEEZE PROBABILITY HIGH",
            "action": "HARD_VETO"
        }


def get_funding_with_assessment(symbol: str) -> Dict:
    """
    Get funding rate and crowded trade assessment for a symbol.

    Args:
        symbol: Token symbol

    Returns:
        Combined funding data and assessment
    """
    funding_data = get_funding_rate(symbol)

    if not funding_data:
        return {
            "available": False,
            "symbol": symbol,
            "message": f"No perpetual market found for {symbol}",
            "assessment": {
                "crowded": False,
                "severity": "UNKNOWN",
                "penalty": 0,
                "veto": False,
                "action": "NO_DATA"
            }
        }

    funding_rate_8h = funding_data.get("funding_rate_8h", 0)
    assessment = assess_crowded_trade(funding_rate_8h)

    return {
        "available": True,
        "symbol": funding_data.get("symbol"),
        "funding_rate_8h": funding_rate_8h,
        "funding_rate_daily": funding_data.get("funding_rate_daily", 0),
        "source": funding_data.get("source"),
        "timestamp": funding_data.get("timestamp"),
        "assessment": assessment
    }


# CLI for testing
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch funding rates")
    parser.add_argument("symbol", nargs="?", default="BTC", help="Token symbol")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  FUNDING RATE CHECK: {args.symbol}")
    print(f"{'='*60}")

    result = get_funding_with_assessment(args.symbol)

    if result.get("available"):
        print(f"\n  Symbol: {result['symbol']}")
        print(f"  Source: {result['source']}")
        print(f"  Funding Rate (8h): {result['funding_rate_8h']:.4f}%")
        print(f"  Funding Rate (daily): {result['funding_rate_daily']:.4f}%")

        assessment = result["assessment"]
        print(f"\n  Crowded: {assessment['crowded']}")
        print(f"  Severity: {assessment['severity']}")
        print(f"  Penalty: {assessment['penalty']}")
        print(f"  VETO: {assessment['veto']}")
        print(f"  Action: {assessment['action']}")
        print(f"\n  {assessment['message']}")
    else:
        print(f"\n  {result['message']}")

    print(f"\n{'='*60}\n")
