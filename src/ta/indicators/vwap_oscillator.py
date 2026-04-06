"""VWAP zero-line oscillator for Market Cipher-style confluence checks.

The implementation uses a rolling cumulative VWAP over the provided series and
expresses the latest price location as a percent distance from VWAP. Positive
values indicate price above VWAP, negative values below VWAP.
"""

from typing import List, Optional


def calculate_vwap_oscillator(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
) -> dict:
    """Return VWAP oscillator state and cross flags.

    Args:
        highs: High prices ordered oldest-first.
        lows: Low prices ordered oldest-first.
        closes: Close prices ordered oldest-first.
        volumes: Volumes ordered oldest-first.

    Returns:
        {
            "latest_value": float | None,
            "previous_value": float | None,
            "above_zero": bool,
            "crossed_above_zero": bool,
            "crossed_below_zero": bool,
            "series": list[float],
        }
    """
    n = len(closes)
    if n == 0 or len(highs) < n or len(lows) < n or len(volumes) < n:
        return {
            "latest_value": None,
            "previous_value": None,
            "above_zero": False,
            "crossed_above_zero": False,
            "crossed_below_zero": False,
            "series": [],
        }

    typical_prices = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
    osc_series: List[float] = []
    cumulative_tpv = 0.0
    cumulative_volume = 0.0

    for tp, close, volume in zip(typical_prices, closes, volumes):
        effective_volume = float(volume) if volume and volume > 0 else 1.0
        cumulative_tpv += tp * effective_volume
        cumulative_volume += effective_volume
        if cumulative_volume <= 0:
            osc_series.append(0.0)
            continue
        vwap = cumulative_tpv / cumulative_volume
        if vwap == 0:
            osc_series.append(0.0)
            continue
        osc_series.append(((close - vwap) / vwap) * 100.0)

    latest_value: Optional[float] = osc_series[-1] if osc_series else None
    previous_value: Optional[float] = osc_series[-2] if len(osc_series) >= 2 else None

    crossed_above_zero = (
        previous_value is not None and latest_value is not None
        and previous_value <= 0 < latest_value
    )
    crossed_below_zero = (
        previous_value is not None and latest_value is not None
        and previous_value >= 0 > latest_value
    )

    return {
        "latest_value": round(latest_value, 4) if latest_value is not None else None,
        "previous_value": round(previous_value, 4) if previous_value is not None else None,
        "above_zero": bool(latest_value is not None and latest_value > 0),
        "crossed_above_zero": crossed_above_zero,
        "crossed_below_zero": crossed_below_zero,
        "series": [round(v, 4) for v in osc_series],
    }
