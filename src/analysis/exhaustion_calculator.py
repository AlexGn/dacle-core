#!/usr/bin/env python3
"""
Exhaustion Score Calculator for ATH Sniper Mode.

DEPRECATED: Use src.analysis module instead.
Session 256: Marked for migration to src/analysis/

Session 113: Calculates exhaustion score based on David's confirmed criteria:
- RSI Extreme (30pts): RSI > 80 on 15m
- Volume Divergence (25pts): Price up but volume declining
- Shooting Star (25pts): Upper wick > 40% of candle body
- Parabolic Extension (20pts): > 50% gain in 4 hours

Usage:
    from src.analysis import calculate_exhaustion_score
    score = calculate_exhaustion_score("POWER", timeframe="15m")
"""

import warnings
warnings.warn(
    "scripts.helpers.exhaustion_calculator is deprecated. "
    "Use src.analysis module instead.",
    DeprecationWarning,
    stacklevel=2
)

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load sniper config
SNIPER_CONFIG_PATH = PROJECT_ROOT / "config" / "sniper_mode.json"


def load_sniper_config() -> Dict[str, Any]:
    """Load sniper mode configuration."""
    if SNIPER_CONFIG_PATH.exists():
        with open(SNIPER_CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {
        "exhaustion_score": {
            "threshold": 70,
            "components": {
                "rsi_extreme": {"weight": 30, "trigger": "RSI > 80"},
                "volume_divergence": {"weight": 25, "trigger": "Price up, volume down"},
                "shooting_star": {"weight": 25, "trigger": "Upper wick > 40%"},
                "parabolic_extension": {"weight": 20, "trigger": "> 50% gain in 4h"}
            }
        }
    }


def calculate_rsi_score(rsi: float, config: Dict) -> Tuple[float, str]:
    """
    Calculate RSI exhaustion component.

    Returns (score, reason)
    """
    weight = config.get("weight", 30)

    if rsi >= 90:
        return weight, f"RSI EXTREME ({rsi:.1f}) - Full points"
    elif rsi >= 80:
        # Scale 80-90 to 70-100% of weight
        pct = 0.7 + (rsi - 80) / 10 * 0.3
        return weight * pct, f"RSI overbought ({rsi:.1f})"
    elif rsi >= 70:
        # Scale 70-80 to 30-70% of weight
        pct = 0.3 + (rsi - 70) / 10 * 0.4
        return weight * pct, f"RSI elevated ({rsi:.1f})"
    else:
        return 0, f"RSI neutral ({rsi:.1f})"


def calculate_volume_divergence_score(
    price_change_pct: float,
    volume_change_pct: float,
    config: Dict
) -> Tuple[float, str]:
    """
    Calculate volume divergence component.

    Bearish divergence = price up, volume down
    """
    weight = config.get("weight", 25)

    # Price up but volume down = exhaustion signal
    if price_change_pct > 0 and volume_change_pct < 0:
        # Stronger divergence = more points
        divergence = abs(volume_change_pct) + price_change_pct
        if divergence > 30:
            return weight, f"STRONG divergence (price +{price_change_pct:.1f}%, vol {volume_change_pct:.1f}%)"
        elif divergence > 15:
            return weight * 0.7, f"Moderate divergence"
        else:
            return weight * 0.4, f"Weak divergence"
    elif price_change_pct > 5 and volume_change_pct < 10:
        # Price up significantly but volume not following
        return weight * 0.3, f"Volume not confirming (+{price_change_pct:.1f}%)"
    else:
        return 0, "No volume divergence"


def calculate_shooting_star_score(
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    config: Dict
) -> Tuple[float, str]:
    """
    Calculate shooting star candle pattern score.

    Shooting star = long upper wick, small body, little/no lower wick
    Upper wick > 40% of total range = strong signal
    """
    weight = config.get("weight", 25)

    total_range = high_price - low_price
    if total_range == 0:
        return 0, "No price range"

    body = abs(close_price - open_price)
    upper_wick = high_price - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low_price

    upper_wick_pct = (upper_wick / total_range) * 100
    body_pct = (body / total_range) * 100

    # Must be a red candle (close < open) for bearish shooting star
    is_red = close_price < open_price

    if upper_wick_pct >= 60 and body_pct <= 30 and is_red:
        return weight, f"PERFECT shooting star ({upper_wick_pct:.0f}% wick)"
    elif upper_wick_pct >= 40 and body_pct <= 40:
        score = weight * (0.5 + (upper_wick_pct - 40) / 40)
        color = "RED" if is_red else "GREEN"
        return min(score, weight), f"Shooting star pattern ({upper_wick_pct:.0f}% wick, {color})"
    elif upper_wick_pct >= 30:
        return weight * 0.3, f"Long upper wick ({upper_wick_pct:.0f}%)"
    else:
        return 0, f"No shooting star ({upper_wick_pct:.0f}% wick)"


def calculate_parabolic_extension_score(
    gain_4h_pct: float,
    config: Dict
) -> Tuple[float, str]:
    """
    Calculate parabolic extension score.

    > 50% gain in 4 hours = parabolic move, likely to correct
    """
    weight = config.get("weight", 20)

    if gain_4h_pct >= 100:
        return weight, f"EXTREME parabolic (+{gain_4h_pct:.0f}% in 4h)"
    elif gain_4h_pct >= 75:
        return weight * 0.9, f"Strong parabolic (+{gain_4h_pct:.0f}% in 4h)"
    elif gain_4h_pct >= 50:
        return weight * 0.7, f"Parabolic extension (+{gain_4h_pct:.0f}% in 4h)"
    elif gain_4h_pct >= 30:
        return weight * 0.4, f"Strong momentum (+{gain_4h_pct:.0f}% in 4h)"
    elif gain_4h_pct >= 20:
        return weight * 0.2, f"Elevated momentum (+{gain_4h_pct:.0f}% in 4h)"
    else:
        return 0, f"Normal movement (+{gain_4h_pct:.0f}% in 4h)"


def calculate_cvd_divergence_score(
    ohlcv_data: List[Dict],
    config: Dict
) -> Tuple[float, str]:
    """
    Calculate CVD (Cumulative Volume Delta) Divergence score.

    Session 251 Gemini Review: "CVD Divergence is more predictive than RSI.
    If the price is hitting an ATH but CVD is flat or falling, it confirms
    retail is buying the top while limit orders (whales) are absorbing the
    exit liquidity."

    CVD = Σ(buy_volume - sell_volume) over time

    Session 280 Improvement: Volume-weighted CVD calculation
    Instead of binary buy/sell, we weight by candle body proportion:
    - delta = volume × (close - open) / (high - low)
    - Range: -1.0 (full bearish) to +1.0 (full bullish)
    This better captures the strength of buying/selling within each candle.

    Divergence detected when:
    - Price making higher highs (ATH)
    - CVD is flat or declining (whales distributing)

    Args:
        ohlcv_data: List of OHLCV candles
        config: Config dict with weight

    Returns:
        (score, reason)
    """
    weight = config.get("weight", 35)  # Session 251: Primary signal

    if not ohlcv_data or len(ohlcv_data) < 10:
        return 0, "Insufficient data for CVD calculation"

    try:
        # Calculate CVD using close vs open as buy/sell proxy
        cvd_values = []
        cumulative_cvd = 0

        for candle in ohlcv_data:
            volume = candle.get("volume", 0)
            open_price = candle.get("open", 0)
            close_price = candle.get("close", 0)

            if open_price == 0:
                continue

            # Session 280: Volume-weighted CVD calculation
            # Instead of binary buy/sell, weight by candle body proportion
            # Green candle: +volume * (close-open)/(high-low) = partial buy pressure
            # Red candle: -volume * (open-close)/(high-low) = partial sell pressure
            # This better captures the strength of buying/selling within each candle
            high_price = candle.get("high", 0)
            low_price = candle.get("low", 0)
            candle_range = high_price - low_price

            if candle_range <= 0:
                # Doji or invalid candle - use simple proxy
                delta = volume if close_price >= open_price else -volume
            else:
                # Volume-weighted: proportion of range that's bullish/bearish
                body_size = close_price - open_price  # Positive = green, negative = red
                range_proportion = body_size / candle_range  # -1 to +1
                delta = volume * range_proportion

            cumulative_cvd += delta
            cvd_values.append(cumulative_cvd)

        if len(cvd_values) < 5:
            return 0, "Insufficient CVD data points"

        # Check for divergence: price at high but CVD declining
        # Compare first half CVD to second half
        mid_point = len(cvd_values) // 2
        first_half_avg = sum(cvd_values[:mid_point]) / mid_point if mid_point > 0 else 0
        second_half_avg = sum(cvd_values[mid_point:]) / (len(cvd_values) - mid_point) if len(cvd_values) > mid_point else 0

        # Price trend: compare closes
        first_price = ohlcv_data[0].get("close", 0)
        last_price = ohlcv_data[-1].get("close", 0)
        price_change_pct = ((last_price - first_price) / first_price * 100) if first_price > 0 else 0

        # CVD trend
        cvd_change = second_half_avg - first_half_avg
        cvd_declining = cvd_change < 0

        # DIVERGENCE: Price up but CVD down/flat = whales distributing
        if price_change_pct > 5 and cvd_declining:
            if cvd_change < -abs(first_half_avg) * 0.3:  # Significant CVD decline
                return weight, f"STRONG CVD DIVERGENCE: Price +{price_change_pct:.1f}% but CVD declining (whales exiting)"
            else:
                return weight * 0.7, f"CVD divergence: Price +{price_change_pct:.1f}% but CVD flat/weak"

        elif price_change_pct > 0 and cvd_declining:
            return weight * 0.4, f"Weak CVD divergence: Price +{price_change_pct:.1f}%, CVD slightly negative"

        else:
            return 0, f"No CVD divergence (CVD confirms price action)"

    except Exception as e:
        return 0, f"CVD calculation error: {str(e)}"


def calculate_funding_rate_bonus(
    funding_rate: Optional[float],
    config: Dict
) -> Tuple[float, str]:
    """
    Calculate funding rate bonus.

    Gemini recommendation: Additive bonus when funding > 0.1% (annualized > 100%).
    High funding means retail is piling in long - good for shorts.
    """
    weight = config.get("weight", 10)

    if funding_rate is None:
        return 0, "Funding rate unavailable"

    if funding_rate >= 0.3:
        return weight, f"EXTREME funding ({funding_rate:.3f}%) - retail max long"
    elif funding_rate >= 0.1:
        return weight * 0.7, f"High funding ({funding_rate:.3f}%) - longs paying"
    elif funding_rate >= 0.05:
        return weight * 0.3, f"Elevated funding ({funding_rate:.3f}%)"
    else:
        return 0, f"Normal funding ({funding_rate:.3f}%)"


def check_funding_abort(funding_rate: Optional[float]) -> tuple:
    """
    Session 114 Gemini recommendation: Negative funding = ABORT.

    If funding < -0.05%, shorts are crowded and squeeze is likely.
    This is an ENTRY GATE - skip the trade entirely if this triggers.

    Args:
        funding_rate: Funding rate as percentage (e.g., -0.05 = -0.05%)

    Returns:
        (should_abort: bool, reason: str, severity: str)
    """
    if funding_rate is None:
        return False, "Funding rate unavailable - proceed with caution", "WARNING"

    if funding_rate < -0.05:
        return True, f"ABORT: Negative funding ({funding_rate:.3f}%) - shorts crowded, squeeze likely", "CRITICAL"
    elif funding_rate < 0:
        return False, f"WARNING: Slightly negative funding ({funding_rate:.3f}%) - monitor closely", "WARNING"
    else:
        return False, f"OK: Funding rate positive ({funding_rate:.3f}%)", "OK"


def calculate_breakeven_velocity(
    funding_rate: Optional[float],
    expected_hold_days: int = 7,
    expected_drop_pct: float = 30.0
) -> Dict[str, Any]:
    """
    Session 117+ Gemini Critical Fix: Calculate Breakeven Velocity.

    The "Funding Rate Death Spiral" problem:
    - TGE tokens often have extreme negative funding (-0.5% to -2.0% per 8 hours)
    - A short can bleed 6% of principal DAILY just in fees
    - A "profitable" trade can become a loser from fee burn alone

    Formula:
        daily_funding_cost = funding_rate * 3 (8h intervals)
        total_funding_cost = daily_funding_cost * hold_days
        breakeven_drop_required = total_funding_cost (price must drop this much just to break even)

    If breakeven_drop_required > expected_drop, the trade is UNPROFITABLE.

    Args:
        funding_rate: Current funding rate as percentage (e.g., -0.5 = -0.5%)
        expected_hold_days: How many days we expect to hold (default 7 for TGE)
        expected_drop_pct: Expected price drop percentage (default 30% for TGE)

    Returns:
        {
            "funding_rate": float,
            "daily_cost_pct": float,
            "total_cost_pct": float,
            "breakeven_drop_required": float,
            "expected_drop": float,
            "net_profit_potential": float,
            "verdict": "PROFITABLE" | "MARGINAL" | "UNPROFITABLE",
            "conviction_penalty": float,  # 0 to -3.0 points
            "warning": str
        }
    """
    result = {
        "funding_rate": funding_rate,
        "expected_hold_days": expected_hold_days,
        "expected_drop_pct": expected_drop_pct,
        "daily_cost_pct": 0.0,
        "total_cost_pct": 0.0,
        "breakeven_drop_required": 0.0,
        "net_profit_potential": expected_drop_pct,
        "verdict": "UNKNOWN",
        "conviction_penalty": 0.0,
        "warning": ""
    }

    if funding_rate is None:
        result["verdict"] = "UNKNOWN"
        result["warning"] = "Funding rate unavailable - cannot calculate breakeven"
        return result

    # For SHORTS, we PAY when funding is NEGATIVE (shorts crowded)
    # When funding is positive, shorts RECEIVE funding (longs pay us)
    if funding_rate >= 0:
        # Positive funding = we receive money, no cost
        daily_income = abs(funding_rate) * 3  # 3 funding periods per day
        total_income = daily_income * expected_hold_days
        result["daily_cost_pct"] = -daily_income  # Negative = income
        result["total_cost_pct"] = -total_income
        result["breakeven_drop_required"] = 0  # No drop needed to cover fees
        result["net_profit_potential"] = expected_drop_pct + total_income
        result["verdict"] = "PROFITABLE"
        result["conviction_penalty"] = 0.0
        result["warning"] = f"Positive funding (+{total_income:.1f}% income over {expected_hold_days}d)"
        return result

    # Negative funding = we pay to hold short
    daily_cost = abs(funding_rate) * 3  # 3 funding periods per day (8h each)
    total_cost = daily_cost * expected_hold_days

    result["daily_cost_pct"] = daily_cost
    result["total_cost_pct"] = total_cost
    result["breakeven_drop_required"] = total_cost
    result["net_profit_potential"] = expected_drop_pct - total_cost

    # Verdict based on net profit potential
    if result["net_profit_potential"] >= expected_drop_pct * 0.7:
        # Still > 70% of expected profit after fees
        result["verdict"] = "PROFITABLE"
        result["conviction_penalty"] = 0.0
        result["warning"] = f"Fee drag: -{total_cost:.1f}% over {expected_hold_days}d (acceptable)"
    elif result["net_profit_potential"] >= expected_drop_pct * 0.4:
        # 40-70% of expected profit - marginal
        result["verdict"] = "MARGINAL"
        result["conviction_penalty"] = -1.0
        result["warning"] = f"HIGH FEE DRAG: -{total_cost:.1f}% over {expected_hold_days}d (consider shorter hold)"
    elif result["net_profit_potential"] > 0:
        # Still profitable but barely
        result["verdict"] = "MARGINAL"
        result["conviction_penalty"] = -2.0
        result["warning"] = f"SEVERE FEE DRAG: -{total_cost:.1f}% over {expected_hold_days}d (reduce size/duration)"
    else:
        # Negative profit = UNPROFITABLE
        result["verdict"] = "UNPROFITABLE"
        result["conviction_penalty"] = -3.0
        result["warning"] = f"DEATH SPIRAL: Fees ({total_cost:.1f}%) > Expected drop ({expected_drop_pct}%). SKIP TRADE."

    return result


def calculate_wick_ratio(
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float
) -> Dict[str, Any]:
    """
    Session 117+ Gemini Optimization: Wick Ratio Filter.

    Filters continuation patterns (Hammers, Dojis) that look like reversals but aren't.

    A long LOWER wick at ATH indicates buying pressure - bulls are defending.
    This is NOT a reversal signal, it's a continuation signal.

    Args:
        OHLC candle data

    Returns:
        {
            "upper_wick_ratio": float,  # Upper wick as % of total range
            "lower_wick_ratio": float,  # Lower wick as % of total range
            "body_ratio": float,        # Body as % of total range
            "is_hammer": bool,          # Long lower wick, small body
            "is_doji": bool,            # Very small body
            "is_shooting_star": bool,   # Long upper wick, small body
            "invalidate_signal": bool,  # If True, do NOT short
            "reason": str
        }
    """
    total_range = high_price - low_price

    result = {
        "upper_wick_ratio": 0.0,
        "lower_wick_ratio": 0.0,
        "body_ratio": 0.0,
        "is_hammer": False,
        "is_doji": False,
        "is_shooting_star": False,
        "invalidate_signal": False,
        "reason": ""
    }

    if total_range == 0:
        result["reason"] = "No price range (flat candle)"
        return result

    body = abs(close_price - open_price)
    upper_wick = high_price - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low_price

    result["upper_wick_ratio"] = (upper_wick / total_range)
    result["lower_wick_ratio"] = (lower_wick / total_range)
    result["body_ratio"] = (body / total_range)

    # Pattern detection
    is_red = close_price < open_price

    # Hammer: Long lower wick (>50%), small body (<30%), at bottom of range
    result["is_hammer"] = (
        result["lower_wick_ratio"] > 0.5 and
        result["body_ratio"] < 0.3
    )

    # Doji: Very small body (<10%)
    result["is_doji"] = result["body_ratio"] < 0.1

    # Shooting Star: Long upper wick (>50%), small body (<30%)
    result["is_shooting_star"] = (
        result["upper_wick_ratio"] > 0.5 and
        result["body_ratio"] < 0.3
    )

    # INVALIDATION LOGIC:
    # If lower wick > 40% and body < 40%, buying pressure is strong
    # This is likely a continuation, NOT a reversal
    if result["lower_wick_ratio"] > 0.4 and result["body_ratio"] < 0.4:
        result["invalidate_signal"] = True
        if result["is_hammer"]:
            result["reason"] = f"HAMMER detected (lower wick {result['lower_wick_ratio']*100:.0f}%) - buying pressure, likely continuation"
        elif result["is_doji"]:
            result["reason"] = f"DOJI with long lower wick ({result['lower_wick_ratio']*100:.0f}%) - indecision favors bulls"
        else:
            result["reason"] = f"Long lower wick ({result['lower_wick_ratio']*100:.0f}%) indicates buying pressure"
    elif result["is_shooting_star"] and is_red:
        result["invalidate_signal"] = False
        result["reason"] = f"Valid SHOOTING STAR (upper wick {result['upper_wick_ratio']*100:.0f}%, RED) - reversal likely"
    elif result["is_shooting_star"]:
        result["invalidate_signal"] = False
        result["reason"] = f"Shooting star (GREEN) - weaker signal but valid"
    else:
        result["invalidate_signal"] = False
        result["reason"] = "Normal candle pattern"

    return result


def get_4h_candle_state(token: str) -> Dict[str, Any]:
    """
    Session 119 Phase 3: Get current 4H candle state for checklist automation.

    Returns:
        {
            "state": "BEARISH" | "BULLISH" | "WAITING",
            "is_closed": bool,  # True if most recent 4H candle is closed
            "candle_color": "RED" | "GREEN",
            "time_to_close_minutes": int,  # Minutes until next 4H close
            "change_pct": float,  # Candle body change percentage
            "reason": str
        }

    Used by sniper to automate entry checklist item: "4H candle closes RED"
    """
    result = {
        "state": "WAITING",
        "is_closed": False,
        "candle_color": "UNKNOWN",
        "time_to_close_minutes": 0,
        "change_pct": 0.0,
        "reason": "Unable to fetch 4H data"
    }

    try:
        # Fetch 4H candles
        ohlcv_data = fetch_ohlcv_ccxt(token, timeframe="4h", limit=3)
        if not ohlcv_data or len(ohlcv_data) < 2:
            result["reason"] = "Insufficient 4H data"
            return result

        # Get the most recently CLOSED candle (second to last)
        closed_candle = ohlcv_data[-2]
        current_candle = ohlcv_data[-1]

        # Calculate time to next 4H close
        now = datetime.now(timezone.utc)
        candle_ts = datetime.fromtimestamp(current_candle["timestamp"] / 1000, tz=timezone.utc)
        next_close = candle_ts + timedelta(hours=4)
        time_to_close = (next_close - now).total_seconds() / 60
        result["time_to_close_minutes"] = max(0, int(time_to_close))

        # Analyze the CLOSED candle (this is what the checklist cares about)
        close_price = closed_candle["close"]
        open_price = closed_candle["open"]
        change_pct = ((close_price - open_price) / open_price) * 100 if open_price > 0 else 0

        result["change_pct"] = round(change_pct, 2)
        result["is_closed"] = True  # The previous candle is always closed

        if close_price < open_price:
            result["candle_color"] = "RED"
            if change_pct <= -2.0:
                result["state"] = "BEARISH"
                result["reason"] = f"4H candle closed RED ({change_pct:.1f}%) - strong bearish"
            else:
                result["state"] = "BEARISH"
                result["reason"] = f"4H candle closed RED ({change_pct:.1f}%)"
        else:
            result["candle_color"] = "GREEN"
            result["state"] = "BULLISH"
            result["reason"] = f"4H candle closed GREEN (+{change_pct:.1f}%) - wait for red"

        # Add context about current (open) candle
        current_open = current_candle["open"]
        current_close = current_candle["close"]
        current_change = ((current_close - current_open) / current_open) * 100 if current_open > 0 else 0
        result["current_candle"] = {
            "color": "RED" if current_close < current_open else "GREEN",
            "change_pct": round(current_change, 2),
            "time_to_close_minutes": result["time_to_close_minutes"]
        }

        return result

    except Exception as e:
        result["reason"] = f"Error fetching 4H data: {str(e)}"
        return result


def get_live_macro_confluence() -> Dict[str, Any]:
    """
    Session 119 Phase 3: Get real-time macro confluence score for sniper integration.

    Fetches live macro data and calculates confluence score (0-100).
    Used to downgrade sniper signals when macro is unfavorable.

    Returns:
        {
            "score": 0-100,
            "favorable": bool,  # True if score >= 60
            "signals": [
                {"name": "BTC Trend", "bullish": False, "value": "downtrend"},
                ...
            ],
            "btc_trend": str,
            "usdt_d_trend": str,
            "total3_trend": str,
            "fear_greed": int,
            "reason": str
        }
    """
    result = {
        "score": 50,  # Neutral default
        "favorable": False,
        "signals": [],
        "btc_trend": "unknown",
        "usdt_d_trend": "unknown",
        "total3_trend": "unknown",
        "fear_greed": 50,
        "reason": "Macro data unavailable"
    }

    try:
        # Import the indices tracker for macro data
        from src.data.indices_tracker import IndicesTracker
        tracker = IndicesTracker(use_cache=True)

        indices_data = tracker.fetch_all_indices()
        indices = indices_data.get('indices', {})

        bearish_count = 0
        total_signals = 5

        # 1. BTC Trend (bearish = good for shorts)
        # Use 24h change and realtime_sentiment for trend detection
        btc_24h_change = indices.get('btc_24h_change', 0)
        realtime = indices.get('realtime_sentiment', {})
        btc_rsi = realtime.get('btc_rsi', 50)

        # Determine BTC trend: bearish if down >2% OR RSI < 40
        btc_bearish = btc_24h_change < -2 or btc_rsi < 40
        btc_trend = "bearish" if btc_bearish else ("bullish" if btc_24h_change > 2 or btc_rsi > 60 else "neutral")
        result["btc_trend"] = btc_trend
        if btc_bearish:
            bearish_count += 1
        result["signals"].append({
            "name": "BTC Trend",
            "bullish": not btc_bearish,
            "value": f"{btc_24h_change:+.1f}% (RSI {btc_rsi:.0f})",
            "good_for_short": btc_bearish
        })

        # 2. USDT.D (rising = risk-off = good for shorts)
        usdt_d_data = indices.get('usdt_d', {})
        usdt_d_value = usdt_d_data.get('value', 0) if isinstance(usdt_d_data, dict) else indices.get('usdt_d_value', 5.0)
        # USDT.D > 5.5% indicates risk-off sentiment (good for shorts)
        usdt_d_rising = usdt_d_value > 5.5
        usdt_d_trend = "rising" if usdt_d_rising else "stable"
        result["usdt_d_trend"] = usdt_d_trend
        if usdt_d_rising:
            bearish_count += 1
        result["signals"].append({
            "name": "USDT.D",
            "bullish": not usdt_d_rising,
            "value": f"{usdt_d_value:.2f}%",
            "good_for_short": usdt_d_rising
        })

        # 3. TOTAL3 (altcoin cap declining = good for shorts)
        total3_24h_change = indices.get('total3_24h_change', 0)
        # TOTAL3 declining > 2% is bearish for alts (good for shorts)
        total3_declining = total3_24h_change < -2
        total3_trend = "declining" if total3_declining else ("rising" if total3_24h_change > 2 else "neutral")
        result["total3_trend"] = total3_trend
        if total3_declining:
            bearish_count += 1
        result["signals"].append({
            "name": "TOTAL3",
            "bullish": not total3_declining,
            "value": f"{total3_24h_change:+.1f}%",
            "good_for_short": total3_declining
        })

        # 4. Fear & Greed (fear = good for shorts on TGE tokens)
        fg_data = indices.get('fear_greed_index', {})
        fg_value = fg_data.get('value', 50) if isinstance(fg_data, dict) else 50
        result["fear_greed"] = fg_value
        fg_fear = fg_value < 40
        if fg_fear:
            bearish_count += 1
        result["signals"].append({
            "name": "Fear & Greed",
            "bullish": fg_value > 60,
            "value": fg_value,
            "good_for_short": fg_fear
        })

        # 5. ETH Trend (bearish = good for alt shorts)
        eth_24h_change = indices.get('eth_24h_change', 0)
        eth_rsi = realtime.get('eth_rsi', 50)

        # Determine ETH trend: bearish if down >3% OR RSI < 40
        eth_bearish = eth_24h_change < -3 or eth_rsi < 40
        eth_trend = "bearish" if eth_bearish else ("bullish" if eth_24h_change > 3 or eth_rsi > 60 else "neutral")
        if eth_bearish:
            bearish_count += 1
        result["signals"].append({
            "name": "ETH Trend",
            "bullish": not eth_bearish,
            "value": f"{eth_24h_change:+.1f}% (RSI {eth_rsi:.0f})",
            "good_for_short": eth_bearish
        })

        # Calculate score (0-100)
        result["score"] = int((bearish_count / total_signals) * 100)
        result["favorable"] = result["score"] >= 60

        # Build reason string
        good_signals = [s["name"] for s in result["signals"] if s.get("good_for_short")]
        bad_signals = [s["name"] for s in result["signals"] if not s.get("good_for_short")]

        if result["favorable"]:
            result["reason"] = f"Macro favorable ({bearish_count}/{total_signals}): {', '.join(good_signals)}"
        else:
            result["reason"] = f"Macro headwind ({bearish_count}/{total_signals}): {', '.join(bad_signals)} bullish"

        return result

    except Exception as e:
        result["reason"] = f"Error fetching macro: {str(e)}"
        return result


def fetch_funding_rate(token: str) -> Optional[float]:
    """
    Fetch current funding rate for a token's perpetual.

    Gemini recommendation: High funding (>0.1%) means retail piling in long.
    Returns funding rate as percentage (0.1 = 0.1%).
    """
    try:
        import ccxt

        trading_symbol = get_trading_symbol(token)
        exchanges = ["mexc", "binance", "bybit"]

        for exchange_id in exchanges:
            try:
                exchange = getattr(ccxt, exchange_id)({
                    'enableRateLimit': True,
                    'timeout': 10000
                })

                symbol = f"{trading_symbol}/USDT:USDT"
                try:
                    funding = exchange.fetch_funding_rate(symbol)
                    if funding and funding.get("fundingRate"):
                        # Convert to percentage (rate is usually decimal like 0.0001)
                        return float(funding["fundingRate"]) * 100
                except Exception:
                    continue
            except Exception:
                continue

        return None
    except ImportError:
        return None


def get_trading_symbol(token: str) -> str:
    """
    Get the actual trading symbol for a token.

    Some tokens have different folder names than trading symbols:
    - RAYLS folder -> RLS trading symbol
    - HUMIDIFI folder -> WET trading symbol
    """
    consolidated_path = PROJECT_ROOT / "data" / "tokens" / token / "consolidated.json"
    if consolidated_path.exists():
        try:
            with open(consolidated_path, 'r') as f:
                data = json.load(f)
            # Check for trading_symbol first, then symbol field
            trading_sym = data.get("trading_symbol") or data.get("symbol")
            if trading_sym and trading_sym != token:
                return trading_sym
        except Exception:
            pass
    return token


def fetch_ohlcv_ccxt(token: str, timeframe: str = "15m", limit: int = 50) -> List[Dict]:
    """Fetch OHLCV data using ccxt. Tries spot first, then perps."""
    try:
        import ccxt

        # Get the actual trading symbol (may differ from folder name)
        trading_symbol = get_trading_symbol(token)

        # Try multiple exchanges
        exchanges = ["mexc", "gate", "kucoin", "binance"]
        # Try both the trading symbol and original token name
        symbols_to_try = [trading_symbol]
        if trading_symbol != token:
            symbols_to_try.append(token)

        for exchange_id in exchanges:
            try:
                exchange = getattr(ccxt, exchange_id)({
                    'enableRateLimit': True,
                    'timeout': 10000
                })

                for sym in symbols_to_try:
                    # Try spot first, then perp symbols
                    for symbol in [f"{sym}/USDT", f"{sym}/USDT:USDT"]:
                        try:
                            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

                            if ohlcv and len(ohlcv) > 0:
                                # Convert to dict format
                                return [
                                    {
                                        "timestamp": row[0],
                                        "open": row[1],
                                        "high": row[2],
                                        "low": row[3],
                                        "close": row[4],
                                        "volume": row[5]
                                    }
                                    for row in ohlcv
                                ]
                        except Exception:
                            continue
            except Exception:
                continue

        return []
    except ImportError:
        return []


def calculate_exhaustion_score(
    token: str,
    timeframe: str = "15m",
    ohlcv_data: Optional[List[Dict]] = None,
    current_rsi: Optional[float] = None
) -> Dict[str, Any]:
    """
    Calculate total exhaustion score for a token.

    Args:
        token: Token symbol (e.g., "POWER")
        timeframe: Timeframe for analysis (default "15m" per David's config)
        ohlcv_data: Optional OHLCV data (will fetch if not provided)
        current_rsi: Optional RSI value (will calculate if not provided)

    Returns:
        {
            "score": 0-100,
            "threshold": 70,
            "signal": "SNIPE" | "WATCH" | "SKIP",
            "components": {...},
            "reasons": [...]
        }
    """
    config = load_sniper_config()
    exhaustion_config = config.get("exhaustion_score", {})
    threshold = exhaustion_config.get("threshold", 70)
    components = exhaustion_config.get("components", {})

    result = {
        "token": token,
        "timeframe": timeframe,
        "score": 0,
        "threshold": threshold,
        "signal": "SKIP",
        "components": {},
        "reasons": [],
        "calculated_at": datetime.now(timezone.utc).isoformat()
    }

    # Fetch OHLCV data if not provided
    if ohlcv_data is None:
        try:
            ohlcv_data = fetch_ohlcv_ccxt(token, timeframe=timeframe, limit=50)
        except Exception as e:
            result["error"] = f"Failed to fetch OHLCV: {e}"
            return result

    if not ohlcv_data or len(ohlcv_data) < 5:
        result["error"] = "Insufficient OHLCV data"
        return result

    # Get latest candle data
    latest = ohlcv_data[-1]
    prev = ohlcv_data[-2] if len(ohlcv_data) >= 2 else latest

    open_price = latest.get("open", 0)
    high_price = latest.get("high", 0)
    low_price = latest.get("low", 0)
    close_price = latest.get("close", 0)
    volume = latest.get("volume", 0)
    prev_volume = prev.get("volume", 1)

    # Calculate 4h price change (last 16 candles for 15m timeframe)
    candles_4h = 16 if timeframe == "15m" else 4  # Adjust for timeframe
    if len(ohlcv_data) >= candles_4h:
        price_4h_ago = ohlcv_data[-candles_4h].get("close", close_price)
        gain_4h_pct = ((close_price - price_4h_ago) / price_4h_ago * 100) if price_4h_ago > 0 else 0
    else:
        gain_4h_pct = 0

    # Calculate volume change
    volume_change_pct = ((volume - prev_volume) / prev_volume * 100) if prev_volume > 0 else 0
    price_change_pct = ((close_price - prev.get("close", close_price)) / prev.get("close", 1) * 100)

    total_score = 0

    # Session 251 Gemini Review: Reordered by predictive power
    # 1. CVD Divergence (NEW - 35% weight, most predictive per Gemini)
    cvd_config = components.get("cvd_divergence", {"weight": 35})
    cvd_score, cvd_reason = calculate_cvd_divergence_score(ohlcv_data, cvd_config)
    result["components"]["cvd_divergence"] = {
        "score": cvd_score,
        "reason": cvd_reason
    }
    if cvd_score > 0:
        result["reasons"].append(cvd_reason)
    total_score += cvd_score

    # 2. Volume Divergence Score (35% weight, second most predictive)
    vol_score, vol_reason = calculate_volume_divergence_score(
        price_change_pct, volume_change_pct, components.get("volume_divergence", {"weight": 35})
    )
    result["components"]["volume_divergence"] = {
        "score": vol_score,
        "reason": vol_reason,
        "price_change": price_change_pct,
        "volume_change": volume_change_pct
    }
    if vol_score > 0:
        result["reasons"].append(vol_reason)
    total_score += vol_score

    # 3. RSI Score (20% weight, reduced from 30% - less reliable in parabolic runs)
    if current_rsi is None:
        try:
            # Calculate RSI from OHLCV
            closes = [c.get("close", 0) for c in ohlcv_data[-15:]]
            current_rsi = calculate_rsi(closes)
        except:
            current_rsi = 50  # Default neutral

    rsi_score, rsi_reason = calculate_rsi_score(current_rsi, components.get("rsi_extreme", {"weight": 20}))
    result["components"]["rsi_extreme"] = {"score": rsi_score, "reason": rsi_reason, "rsi": current_rsi}
    if rsi_score > 0:
        result["reasons"].append(rsi_reason)
    total_score += rsi_score

    # 4. Shooting Star Score
    star_score, star_reason = calculate_shooting_star_score(
        open_price, high_price, low_price, close_price,
        components.get("shooting_star", {})
    )
    result["components"]["shooting_star"] = {
        "score": star_score,
        "reason": star_reason,
        "candle": {"open": open_price, "high": high_price, "low": low_price, "close": close_price}
    }
    if star_score > 0:
        result["reasons"].append(star_reason)
    total_score += star_score

    # 4. Parabolic Extension Score
    para_score, para_reason = calculate_parabolic_extension_score(
        gain_4h_pct, components.get("parabolic_extension", {})
    )
    result["components"]["parabolic_extension"] = {
        "score": para_score,
        "reason": para_reason,
        "gain_4h_pct": gain_4h_pct
    }
    if para_score > 0:
        result["reasons"].append(para_reason)
    total_score += para_score

    # 5. Funding Rate Bonus (Gemini recommendation)
    funding_config = components.get("funding_rate_bonus", {})
    if funding_config:
        funding_rate = fetch_funding_rate(token)
        funding_score, funding_reason = calculate_funding_rate_bonus(
            funding_rate, funding_config
        )
        result["components"]["funding_rate_bonus"] = {
            "score": funding_score,
            "reason": funding_reason,
            "funding_rate": funding_rate
        }
        if funding_score > 0:
            result["reasons"].append(funding_reason)
        total_score += funding_score

    # Final score and signal
    result["score"] = round(total_score, 1)

    if total_score >= threshold:
        result["signal"] = "SNIPE"
    elif total_score >= threshold * 0.7:
        result["signal"] = "WATCH"
    else:
        result["signal"] = "SKIP"

    return result


def calculate_rsi(closes: List[float], period: int = 14) -> float:
    """Calculate RSI from close prices."""
    if len(closes) < period + 1:
        return 50.0  # Default neutral

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    if len(gains) < period:
        return 50.0

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def main():
    """CLI for testing exhaustion calculator."""
    import argparse

    parser = argparse.ArgumentParser(description="Calculate exhaustion score for ATH Sniper Mode")
    parser.add_argument("token", help="Token symbol (e.g., POWER)")
    parser.add_argument("--timeframe", "-t", default="15m", help="Timeframe (default: 15m)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    result = calculate_exhaustion_score(args.token.upper(), args.timeframe)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"  EXHAUSTION SCORE: {args.token.upper()}")
        print(f"{'='*50}")
        print(f"\n  Score: {result['score']}/100 (threshold: {result['threshold']})")
        print(f"  Signal: {result['signal']}")

        if result.get("error"):
            print(f"\n  ERROR: {result['error']}")
        else:
            print(f"\n  Components:")
            for name, data in result.get("components", {}).items():
                print(f"    {name}: {data['score']:.1f} - {data['reason']}")

            if result["reasons"]:
                print(f"\n  Triggers:")
                for r in result["reasons"]:
                    print(f"    - {r}")

        print(f"\n{'='*50}\n")


if __name__ == "__main__":
    main()
