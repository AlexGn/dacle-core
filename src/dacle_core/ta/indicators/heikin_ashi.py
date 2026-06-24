"""Heikin Ashi candle transform.

Converts standard OHLC candles into smoothed Heikin Ashi candles.
Used by the Dacle Cipher HA indicator overlay.

Formula:
    HA_Close = (O + H + L + C) / 4
    HA_Open  = (prev_HA_Open + prev_HA_Close) / 2   [seed: (O[0] + C[0]) / 2]
    HA_High  = max(H, HA_Open, HA_Close)
    HA_Low   = min(L, HA_Open, HA_Close)

Pure function, no I/O.
"""
from typing import List


def to_heikin_ashi(
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
) -> dict:
    """Transform OHLC series into Heikin Ashi candles.

    Args:
        opens:   Open prices (oldest first).
        highs:   High prices (oldest first).
        lows:    Low prices (oldest first).
        closes:  Close prices (oldest first).

    Returns:
        {
            "ha_open":  list[float],   # Heikin Ashi open prices
            "ha_high":  list[float],   # Heikin Ashi high prices
            "ha_low":   list[float],   # Heikin Ashi low prices
            "ha_close": list[float],   # Heikin Ashi close prices
            "is_bullish": list[bool],  # True when ha_close >= ha_open
            "latest_is_bullish": bool, # Trend of the most recent candle
            "bullish_streak": int,     # Consecutive bullish HA candles at end
            "bearish_streak": int,     # Consecutive bearish HA candles at end
        }
    """
    n = len(closes)
    if n == 0 or len(opens) < n or len(highs) < n or len(lows) < n:
        return {
            "ha_open": [],
            "ha_high": [],
            "ha_low": [],
            "ha_close": [],
            "is_bullish": [],
            "latest_is_bullish": False,
            "bullish_streak": 0,
        "bearish_streak": 0,
        }

    ha_close: List[float] = []
    ha_open: List[float] = []
    ha_high: List[float] = []
    ha_low: List[float] = []

    for i in range(n):
        hc = (opens[i] + highs[i] + lows[i] + closes[i]) / 4.0
        ha_close.append(hc)

        if i == 0:
            ho = (opens[i] + closes[i]) / 2.0
        else:
            ho = (ha_open[i - 1] + ha_close[i - 1]) / 2.0
        ha_open.append(ho)

        ha_high.append(max(highs[i], ho, hc))
        ha_low.append(min(lows[i], ho, hc))

    is_bullish = [ha_close[i] >= ha_open[i] for i in range(n)]

    # Consecutive bullish/bearish streaks at the end
    streak = 0
    for bull in reversed(is_bullish):
        if bull:
            streak += 1
        else:
            break

    bearish_streak = 0
    for bull in reversed(is_bullish):
        if not bull:
            bearish_streak += 1
        else:
            break

    return {
        "ha_open": [round(v, 8) for v in ha_open],
        "ha_high": [round(v, 8) for v in ha_high],
        "ha_low": [round(v, 8) for v in ha_low],
        "ha_close": [round(v, 8) for v in ha_close],
        "is_bullish": is_bullish,
        "latest_is_bullish": is_bullish[-1] if is_bullish else False,
        "bullish_streak": streak,
        "bearish_streak": bearish_streak,
    }
