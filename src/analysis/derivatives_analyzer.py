"""
Derivatives Analyzer -- OI + Liquidation (4-Quadrant Matrix)
Session 440: Phase 5 of Quick TA Audit plan.

Fetches OI from Binance Futures API and combines with price change
to produce a 4-quadrant signal:
  - Price Down + OI Up = Strong Short (new short positions entering)
  - Price Up + OI Down = Short Squeeze (shorts covering)
  - Price Down + OI Down = Long Liquidation (dump exhausting)
  - Price Up + OI Up = Strong Long (new long positions entering)
"""
from typing import Dict, Optional

import logging

import requests

logger = logging.getLogger(__name__)

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
REQUEST_TIMEOUT = 10

# --- Quadrant thresholds (pct) ---
PRICE_THRESHOLD = 1.0   # +/- 1% price change triggers a quadrant classification
OI_THRESHOLD = 2.0      # +/- 2% OI change triggers a quadrant classification

# Quadrant labels
STRONG_SHORT = "STRONG_SHORT"
SHORT_SQUEEZE = "SHORT_SQUEEZE"
LONG_LIQUIDATION = "LONG_LIQUIDATION"
STRONG_LONG = "STRONG_LONG"
NEUTRAL = "NEUTRAL"

# BQS modifier lookup: {quadrant: {direction: modifier_points}}
_BQS_MODIFIERS: Dict[str, Dict[str, int]] = {
    STRONG_SHORT:      {"SHORT": 3, "LONG": -2},
    SHORT_SQUEEZE:     {"SHORT": -2, "LONG": 1},
    LONG_LIQUIDATION:  {"SHORT": -1, "LONG": 0},
    STRONG_LONG:       {"SHORT": -2, "LONG": 3},
    NEUTRAL:           {"SHORT": 0, "LONG": 0},
}

# Direction impact labels
_DIRECTION_IMPACT: Dict[str, str] = {
    STRONG_SHORT:     "Confirms SHORT bias -- new short positions entering",
    SHORT_SQUEEZE:    "Warns against SHORT -- shorts covering, squeeze risk",
    LONG_LIQUIDATION: "Dump exhausting -- longs liquidated, downside fading",
    STRONG_LONG:      "Confirms LONG bias -- new long positions entering",
    NEUTRAL:          "No significant OI signal",
}


# ============================================================================
# FETCH FUNCTIONS (I/O)
# ============================================================================


def fetch_open_interest(symbol: str) -> Optional[dict]:
    """Fetch current open interest for a symbol from Binance Futures API.

    Args:
        symbol: Token symbol (e.g. 'BTC'). USDT is appended automatically.

    Returns:
        {"oi": float, "timestamp": str} or None on failure.
    """
    pair = f"{symbol.upper()}USDT"
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/openInterest"
    try:
        resp = requests.get(url, params={"symbol": pair}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return {
            "oi": float(data.get("openInterest", 0)),
            "timestamp": str(data.get("time", "")),
        }
    except Exception as exc:
        logger.warning("fetch_open_interest(%s) failed: %s", symbol, exc)
        return None


def fetch_oi_history(
    symbol: str, period: str = "5m", limit: int = 48
) -> list:
    """Fetch OI history from Binance Futures API.

    Args:
        symbol: Token symbol (e.g. 'BTC').
        period: Kline period for OI history (5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d).
        limit: Number of data points (max 500).

    Returns:
        List of {"sumOpenInterest": str, "sumOpenInterestValue": str, "timestamp": int}
        or empty list on failure.
    """
    pair = f"{symbol.upper()}USDT"
    url = f"{BINANCE_FUTURES_BASE}/futures/data/openInterestHist"
    try:
        resp = requests.get(
            url,
            params={"symbol": pair, "period": period, "limit": min(limit, 500)},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("fetch_oi_history(%s) failed: %s", symbol, exc)
        return []


# ============================================================================
# ANALYSIS (pure after fetch)
# ============================================================================


def _classify_quadrant(
    price_change: float, oi_change: float
) -> str:
    """Classify price + OI change into one of five quadrants."""
    if price_change < -PRICE_THRESHOLD and oi_change > OI_THRESHOLD:
        return STRONG_SHORT
    if price_change > PRICE_THRESHOLD and oi_change < -OI_THRESHOLD:
        return SHORT_SQUEEZE
    if price_change < -PRICE_THRESHOLD and oi_change < -OI_THRESHOLD:
        return LONG_LIQUIDATION
    if price_change > PRICE_THRESHOLD and oi_change > OI_THRESHOLD:
        return STRONG_LONG
    return NEUTRAL


def _signal_strength(oi_change_abs: float) -> str:
    """Determine signal strength based on absolute OI change."""
    if oi_change_abs >= 8.0:
        return "EXTREME"
    if oi_change_abs >= 5.0:
        return "STRONG"
    if oi_change_abs >= 2.0:
        return "MODERATE"
    return "WEAK"


def analyze_oi_matrix(
    symbol: str,
    price_change_4h: float,
    price_change_24h: float,
) -> dict:
    """Fetch OI and classify into the 4-quadrant matrix.

    Args:
        symbol: Token symbol.
        price_change_4h: 4-hour price change percentage (e.g. -3.5 for -3.5%).
        price_change_24h: 24-hour price change percentage.

    Returns:
        Dict with quadrant, oi changes, signal strength, BQS modifier, direction impact.
        On fetch failure, returns a NEUTRAL result with zero OI changes.
    """
    history = fetch_oi_history(symbol, period="5m", limit=48)

    oi_change_4h_pct = 0.0
    oi_change_24h_pct = 0.0

    if history and len(history) >= 2:
        latest_oi = float(history[-1].get("sumOpenInterestValue", 0))

        # 4h approx: 48 x 5min = 240min = 4h
        idx_4h = max(0, len(history) - 48)
        oi_4h_ago = float(history[idx_4h].get("sumOpenInterestValue", 0))
        if oi_4h_ago > 0:
            oi_change_4h_pct = ((latest_oi - oi_4h_ago) / oi_4h_ago) * 100

        # 24h: fetch separately with wider period if needed
        # For now approximate from available data (max ~4h window with 5m/48 limit)
        # Use 4h change as primary signal; 24h would need a second fetch with period=1h limit=24
        oi_change_24h_pct = oi_change_4h_pct  # conservative fallback

    # Also try 24h from a separate fetch
    history_24h = fetch_oi_history(symbol, period="1h", limit=24)
    if history_24h and len(history_24h) >= 2:
        latest_oi_24 = float(history_24h[-1].get("sumOpenInterestValue", 0))
        oi_24h_ago = float(history_24h[0].get("sumOpenInterestValue", 0))
        if oi_24h_ago > 0:
            oi_change_24h_pct = ((latest_oi_24 - oi_24h_ago) / oi_24h_ago) * 100

    # Classify using 4h price change + 4h OI change as primary signal
    quadrant = _classify_quadrant(price_change_4h, oi_change_4h_pct)
    strength = _signal_strength(abs(oi_change_4h_pct))

    return {
        "quadrant": quadrant,
        "oi_change_4h_pct": round(oi_change_4h_pct, 2),
        "oi_change_24h_pct": round(oi_change_24h_pct, 2),
        "signal_strength": strength,
        "bqs_modifier": _BQS_MODIFIERS.get(quadrant, {}).copy(),
        "direction_impact": _DIRECTION_IMPACT.get(quadrant, ""),
    }


# ============================================================================
# BQS MODIFIER (pure function)
# ============================================================================


def get_oi_bqs_modifier(oi_result: dict, direction: str) -> int:
    """Return the BQS modifier points for a given OI result and trade direction.

    Args:
        oi_result: Dict returned by analyze_oi_matrix() (must have 'quadrant' key).
        direction: Trade direction, 'SHORT' or 'LONG'.

    Returns:
        Integer BQS modifier (positive = confirmation, negative = warning).
    """
    quadrant = oi_result.get("quadrant", NEUTRAL)
    direction_upper = direction.upper()
    return _BQS_MODIFIERS.get(quadrant, {}).get(direction_upper, 0)
