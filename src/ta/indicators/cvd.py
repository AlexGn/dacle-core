"""CVD (Cumulative Volume Delta) indicator with divergence detection.

Migrated from src/analysis/exhaustion_calculator.py
``calculate_cvd_divergence_score`` (Session 440).

Session 280: Volume-weighted CVD calculation (not binary buy/sell).
"""
from typing import Dict, List


def calculate_cvd(ohlcv_data: List[Dict], direction: str = "SHORT") -> Dict:
    """Calculate CVD and detect divergence against price.

    For each candle the delta is volume-weighted by the candle body
    proportion::

        body_size = close - open          # positive = green
        range     = high - low
        delta     = volume * (body_size / range)   # -1 .. +1
        CVD       = cumulative sum of deltas

    Divergence is detected by comparing the first-half vs second-half
    CVD averages against the overall price trend.

    Args:
        ohlcv_data: List of candle dicts with keys
            ``open``, ``high``, ``low``, ``close``, ``volume``.
        direction: ``"SHORT"`` or ``"LONG"`` (for context-appropriate
            labelling; the math is the same).

    Returns:
        Dictionary with keys ``cvd_values``, ``divergence_detected``,
        ``divergence_type``, ``strength``, ``price_change_pct``, and
        ``reason``.
    """
    empty = {
        "cvd_values": [],
        "divergence_detected": False,
        "divergence_type": "none",
        "strength": "none",
        "price_change_pct": 0.0,
        "reason": "",
    }

    if not ohlcv_data or len(ohlcv_data) < 10:
        empty["reason"] = "Insufficient data for CVD calculation"
        return empty

    # --- build CVD series ---
    cvd_values: list[float] = []
    cumulative_cvd = 0.0

    for candle in ohlcv_data:
        volume = candle.get("volume", 0)
        open_price = candle.get("open", 0)
        close_price = candle.get("close", 0)

        if open_price == 0:
            continue

        high_price = candle.get("high", 0)
        low_price = candle.get("low", 0)
        candle_range = high_price - low_price

        if candle_range <= 0:
            # Doji or invalid candle — simple proxy
            delta = volume if close_price >= open_price else -volume
        else:
            body_size = close_price - open_price
            range_proportion = body_size / candle_range
            delta = volume * range_proportion

        cumulative_cvd += delta
        cvd_values.append(cumulative_cvd)

    if len(cvd_values) < 5:
        empty["reason"] = "Insufficient CVD data points"
        return empty

    # --- divergence detection ---
    mid = len(cvd_values) // 2
    first_half_avg = sum(cvd_values[:mid]) / mid
    second_half_avg = sum(cvd_values[mid:]) / (len(cvd_values) - mid)

    first_price = ohlcv_data[0].get("close", 0)
    last_price = ohlcv_data[-1].get("close", 0)
    price_change_pct = (
        ((last_price - first_price) / first_price * 100) if first_price > 0 else 0.0
    )

    cvd_change = second_half_avg - first_half_avg
    cvd_declining = cvd_change < 0

    result = {
        "cvd_values": cvd_values,
        "divergence_detected": False,
        "divergence_type": "none",
        "strength": "none",
        "price_change_pct": price_change_pct,
        "reason": "",
    }

    # Bearish divergence: price up but CVD declining (whales distributing)
    if price_change_pct > 5 and cvd_declining:
        result["divergence_detected"] = True
        result["divergence_type"] = "bearish"
        if cvd_change < -abs(first_half_avg) * 0.3:
            result["strength"] = "strong"
            result["reason"] = (
                f"STRONG CVD DIVERGENCE: Price +{price_change_pct:.1f}% "
                f"but CVD declining (whales exiting)"
            )
        else:
            result["strength"] = "moderate"
            result["reason"] = (
                f"CVD divergence: Price +{price_change_pct:.1f}% but CVD flat/weak"
            )
    elif price_change_pct > 0 and cvd_declining:
        result["divergence_detected"] = True
        result["divergence_type"] = "bearish"
        result["strength"] = "weak"
        result["reason"] = (
            f"Weak CVD divergence: Price +{price_change_pct:.1f}%, "
            f"CVD slightly negative"
        )
    # Bullish divergence: price down but CVD rising
    elif price_change_pct < -5 and not cvd_declining:
        result["divergence_detected"] = True
        result["divergence_type"] = "bullish"
        if cvd_change > abs(first_half_avg) * 0.3:
            result["strength"] = "strong"
            result["reason"] = (
                f"STRONG bullish CVD DIVERGENCE: Price {price_change_pct:.1f}% "
                f"but CVD rising (accumulation)"
            )
        else:
            result["strength"] = "moderate"
            result["reason"] = (
                f"Bullish CVD divergence: Price {price_change_pct:.1f}% "
                f"but CVD holding"
            )
    else:
        result["reason"] = "No CVD divergence (CVD confirms price action)"

    return result
