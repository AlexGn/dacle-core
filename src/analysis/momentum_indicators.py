"""Momentum-based technical indicators.

Pure-function implementations — no I/O, no network calls.
All functions accept lists/arrays of price data and return dicts.

Created: Session 440 (Phase 2 TA Audit).
"""

from typing import List, Optional

import numpy as np

from src.ta.indicators.ema import calculate_ema


# ---------------------------------------------------------------------------
# 1. MACD
# ---------------------------------------------------------------------------

def calculate_macd(
    closes: List[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> dict:
    """Calculate MACD (Moving Average Convergence Divergence).

    Returns:
        {
            "macd_line": float,
            "signal_line": float,
            "histogram": float,
            "direction": str,       # "bullish_cross" | "bearish_cross" | "bullish" | "bearish"
            "cross_recency": int | None,
        }
    """
    min_len = slow + signal_period
    if len(closes) < min_len:
        return {
            "macd_line": 0.0,
            "signal_line": 0.0,
            "histogram": 0.0,
            "direction": "bearish",
            "cross_recency": None,
        }

    fast_ema = calculate_ema(closes, fast)
    slow_ema = calculate_ema(closes, slow)

    # Build MACD line where both EMAs are valid (from index slow-1 onward)
    macd_values: list[float] = []
    for i in range(len(closes)):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            macd_values.append(fast_ema[i] - slow_ema[i])

    if len(macd_values) < signal_period:
        return {
            "macd_line": macd_values[-1] if macd_values else 0.0,
            "signal_line": 0.0,
            "histogram": macd_values[-1] if macd_values else 0.0,
            "direction": "bullish" if macd_values and macd_values[-1] > 0 else "bearish",
            "cross_recency": None,
        }

    signal_ema = calculate_ema(macd_values, signal_period)

    # Find latest valid signal value
    macd_line = macd_values[-1]
    signal_line = signal_ema[-1] if signal_ema[-1] is not None else 0.0
    histogram = macd_line - signal_line

    # Determine cross direction and recency
    cross_recency = None
    direction = "bullish" if macd_line > signal_line else "bearish"

    # Walk backward through the aligned series to find most recent cross
    for i in range(len(macd_values) - 2, -1, -1):
        if signal_ema[i] is None:
            break
        prev_macd = macd_values[i]
        prev_signal = signal_ema[i]
        curr_macd = macd_values[i + 1]
        curr_signal = signal_ema[i + 1]

        prev_diff = prev_macd - prev_signal
        curr_diff = curr_macd - curr_signal

        if prev_diff <= 0 < curr_diff:
            # Bullish cross at i+1
            cross_recency = len(macd_values) - 1 - (i + 1)
            if cross_recency <= 3:
                direction = "bullish_cross"
            break
        elif prev_diff >= 0 > curr_diff:
            # Bearish cross at i+1
            cross_recency = len(macd_values) - 1 - (i + 1)
            if cross_recency <= 3:
                direction = "bearish_cross"
            break

    return {
        "macd_line": macd_line,
        "signal_line": signal_line,
        "histogram": histogram,
        "direction": direction,
        "cross_recency": cross_recency,
    }


# ---------------------------------------------------------------------------
# 2. Stochastic Oscillator
# ---------------------------------------------------------------------------

def calculate_stochastic(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    k_period: int = 14,
    d_period: int = 3,
) -> dict:
    """Calculate Stochastic Oscillator.

    Returns:
        {
            "k_value": float,      # %K (0-100)
            "d_value": float,      # %D (SMA of %K)
            "crossover": str,      # "bullish" | "bearish" | "none"
            "zone": str,           # "overbought" | "oversold" | "neutral"
        }
    """
    n = len(closes)
    if n < k_period or len(highs) < k_period or len(lows) < k_period:
        return {
            "k_value": 50.0,
            "d_value": 50.0,
            "crossover": "none",
            "zone": "neutral",
        }

    h = np.array(highs, dtype=float)
    l = np.array(lows, dtype=float)
    c = np.array(closes, dtype=float)

    # Compute %K for each candle where we have k_period of history
    k_values: list[float] = []
    for i in range(k_period - 1, n):
        highest = np.max(h[i - k_period + 1 : i + 1])
        lowest = np.min(l[i - k_period + 1 : i + 1])
        if highest == lowest:
            k_values.append(50.0)
        else:
            k_values.append((c[i] - lowest) / (highest - lowest) * 100.0)

    # %D = SMA of %K over d_period
    d_values: list[float] = []
    for i in range(len(k_values)):
        if i < d_period - 1:
            d_values.append(k_values[i])  # Not enough for SMA, use raw %K
        else:
            d_values.append(np.mean(k_values[i - d_period + 1 : i + 1]))

    k_val = k_values[-1]
    d_val = d_values[-1]

    # Crossover detection
    crossover = "none"
    if len(k_values) >= 2 and len(d_values) >= 2:
        prev_k = k_values[-2]
        prev_d = d_values[-2]
        if prev_k <= prev_d and k_val > d_val:
            crossover = "bullish"
        elif prev_k >= prev_d and k_val < d_val:
            crossover = "bearish"

    # Zone
    if k_val > 80:
        zone = "overbought"
    elif k_val < 20:
        zone = "oversold"
    else:
        zone = "neutral"

    return {
        "k_value": round(k_val, 2),
        "d_value": round(d_val, 2),
        "crossover": crossover,
        "zone": zone,
    }


# ---------------------------------------------------------------------------
# 3. RSI Divergence
# ---------------------------------------------------------------------------

def _find_swing_highs(data: np.ndarray, order: int = 3) -> list[tuple[int, float]]:
    """Find swing highs (local maxima) in data.

    Returns list of (index, value) tuples.
    """
    swings = []
    for i in range(order, len(data) - order):
        if all(data[i] >= data[i - j] for j in range(1, order + 1)) and \
           all(data[i] >= data[i + j] for j in range(1, order + 1)):
            swings.append((i, float(data[i])))
    return swings


def _find_swing_lows(data: np.ndarray, order: int = 3) -> list[tuple[int, float]]:
    """Find swing lows (local minima) in data.

    Returns list of (index, value) tuples.
    """
    swings = []
    for i in range(order, len(data) - order):
        if all(data[i] <= data[i - j] for j in range(1, order + 1)) and \
           all(data[i] <= data[i + j] for j in range(1, order + 1)):
            swings.append((i, float(data[i])))
    return swings


def detect_rsi_divergence(
    closes: List[float],
    rsi_values: List[float],
    direction: str = "SHORT",
    lookback: int = 20,
) -> dict:
    """Detect RSI divergence (price vs RSI disagreement).

    Returns:
        {
            "detected": bool,
            "type": str,       # "bearish" | "bullish" | "none"
            "strength": str,   # "strong" | "moderate" | "weak" | "none"
            "price_trend": str,  # "higher_highs" | "lower_lows" | "flat"
            "rsi_trend": str,    # "lower_highs" | "higher_lows" | "flat"
        }
    """
    no_divergence = {
        "detected": False,
        "type": "none",
        "strength": "none",
        "price_trend": "flat",
        "rsi_trend": "flat",
    }

    if len(closes) < lookback or len(rsi_values) < lookback:
        return no_divergence

    price_arr = np.array(closes[-lookback:], dtype=float)
    rsi_arr = np.array(rsi_values[-lookback:], dtype=float)

    # Use smaller swing order for shorter lookback windows
    order = max(2, min(3, lookback // 8))

    # Check for bearish divergence: price HH + RSI LH
    price_highs = _find_swing_highs(price_arr, order=order)
    rsi_highs = _find_swing_highs(rsi_arr, order=order)

    bearish_count = 0
    if len(price_highs) >= 2 and len(rsi_highs) >= 2:
        # Compare the last two swing highs
        for i in range(1, len(price_highs)):
            ph_prev = price_highs[i - 1]
            ph_curr = price_highs[i]
            # Find matching RSI highs near these indices
            rh_prev = _nearest_swing(rsi_highs, ph_prev[0])
            rh_curr = _nearest_swing(rsi_highs, ph_curr[0])
            if rh_prev and rh_curr:
                if ph_curr[1] > ph_prev[1] and rh_curr[1] < rh_prev[1]:
                    bearish_count += 1

    # Check for bullish divergence: price LL + RSI HL
    price_lows = _find_swing_lows(price_arr, order=order)
    rsi_lows = _find_swing_lows(rsi_arr, order=order)

    bullish_count = 0
    if len(price_lows) >= 2 and len(rsi_lows) >= 2:
        for i in range(1, len(price_lows)):
            pl_prev = price_lows[i - 1]
            pl_curr = price_lows[i]
            rl_prev = _nearest_swing(rsi_lows, pl_prev[0])
            rl_curr = _nearest_swing(rsi_lows, pl_curr[0])
            if rl_prev and rl_curr:
                if pl_curr[1] < pl_prev[1] and rl_curr[1] > rl_prev[1]:
                    bullish_count += 1

    # Determine result
    if bearish_count > 0 and bearish_count >= bullish_count:
        strength = "strong" if bearish_count >= 2 else "moderate"
        return {
            "detected": True,
            "type": "bearish",
            "strength": strength,
            "price_trend": "higher_highs",
            "rsi_trend": "lower_highs",
        }
    elif bullish_count > 0:
        strength = "strong" if bullish_count >= 2 else "moderate"
        return {
            "detected": True,
            "type": "bullish",
            "strength": strength,
            "price_trend": "lower_lows",
            "rsi_trend": "higher_lows",
        }

    return no_divergence


def _nearest_swing(
    swings: list[tuple[int, float]], target_idx: int, max_dist: int = 5,
) -> Optional[tuple[int, float]]:
    """Find the swing point nearest to *target_idx* within *max_dist*."""
    best = None
    best_dist = max_dist + 1
    for idx, val in swings:
        d = abs(idx - target_idx)
        if d < best_dist:
            best = (idx, val)
            best_dist = d
    return best


# ---------------------------------------------------------------------------
# 4. EMA Cross
# ---------------------------------------------------------------------------

def detect_ema_cross(
    closes: List[float],
    fast: int = 12,
    slow: int = 24,
) -> dict:
    """Detect EMA crossover (death cross / golden cross).

    Returns:
        {
            "type": str,           # "death" | "golden" | "none"
            "candles_since": int | None,
            "fast_above_slow": bool,
        }
    """
    if len(closes) < slow:
        return {
            "type": "none",
            "candles_since": None,
            "fast_above_slow": False,
        }

    fast_ema = calculate_ema(closes, fast)
    slow_ema = calculate_ema(closes, slow)

    # Current state
    last_fast = fast_ema[-1]
    last_slow = slow_ema[-1]
    fast_above = last_fast > last_slow if (last_fast is not None and last_slow is not None) else False

    # Walk backward to find most recent cross
    cross_type = "none"
    candles_since = None

    for i in range(len(closes) - 2, -1, -1):
        if fast_ema[i] is None or slow_ema[i] is None:
            break
        if fast_ema[i + 1] is None or slow_ema[i + 1] is None:
            continue

        prev_diff = fast_ema[i] - slow_ema[i]
        curr_diff = fast_ema[i + 1] - slow_ema[i + 1]

        if prev_diff <= 0 < curr_diff:
            # Golden cross at i+1
            cross_type = "golden"
            candles_since = len(closes) - 1 - (i + 1)
            break
        elif prev_diff >= 0 > curr_diff:
            # Death cross at i+1
            cross_type = "death"
            candles_since = len(closes) - 1 - (i + 1)
            break

    return {
        "type": cross_type,
        "candles_since": candles_since,
        "fast_above_slow": fast_above,
    }


# ---------------------------------------------------------------------------
# 5. Bollinger Band Squeeze
# ---------------------------------------------------------------------------

def calculate_bb_squeeze(
    closes: List[float],
    period: int = 20,
    std_mult: float = 2.0,
) -> dict:
    """Calculate Bollinger Bands and detect squeeze.

    Returns:
        {
            "upper": float,
            "lower": float,
            "middle": float,
            "width": float,
            "width_percentile": float,
            "is_squeeze": bool,
            "break_direction": str | None,
        }
    """
    if len(closes) < period:
        return {
            "upper": 0.0,
            "lower": 0.0,
            "middle": 0.0,
            "width": 0.0,
            "width_percentile": 50.0,
            "is_squeeze": False,
            "break_direction": None,
        }

    arr = np.array(closes, dtype=float)

    # Compute bands for each candle where we have `period` bars of history
    widths: list[float] = []
    uppers: list[float] = []
    lowers: list[float] = []
    middles: list[float] = []

    for i in range(period - 1, len(arr)):
        window = arr[i - period + 1 : i + 1]
        mid = np.mean(window)
        std = np.std(window, ddof=0)
        upper = mid + std_mult * std
        lower = mid - std_mult * std
        w = (upper - lower) / mid if mid != 0 else 0.0
        middles.append(float(mid))
        uppers.append(float(upper))
        lowers.append(float(lower))
        widths.append(float(w))

    # Current values
    current_upper = uppers[-1]
    current_lower = lowers[-1]
    current_middle = middles[-1]
    current_width = widths[-1]

    # Width percentile over the available history (up to last 100)
    recent_widths = widths[-100:]
    width_percentile = float(
        np.sum(np.array(recent_widths) <= current_width) / len(recent_widths) * 100
    )
    is_squeeze = width_percentile < 20.0

    # Break direction: if previous candle was in squeeze and price broke a band
    break_direction: Optional[str] = None
    if len(widths) >= 2 and len(closes) >= 2:
        prev_recent = widths[-101:-1] if len(widths) > 100 else widths[:-1]
        if prev_recent:
            prev_width = widths[-2]
            prev_pctile = float(
                np.sum(np.array(prev_recent) <= prev_width) / len(prev_recent) * 100
            )
            prev_squeeze = prev_pctile < 20.0
            if prev_squeeze:
                if closes[-1] > uppers[-1]:
                    break_direction = "up"
                elif closes[-1] < lowers[-1]:
                    break_direction = "down"

    return {
        "upper": round(current_upper, 6),
        "lower": round(current_lower, 6),
        "middle": round(current_middle, 6),
        "width": round(current_width, 6),
        "width_percentile": round(width_percentile, 2),
        "is_squeeze": is_squeeze,
        "break_direction": break_direction,
    }
