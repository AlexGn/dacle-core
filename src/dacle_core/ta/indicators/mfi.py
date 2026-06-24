"""Dacle-style Money Flow Index (z-score variant).

NOT the traditional RSI-based MFI. This is the Market Cipher B / Dacle Cipher
variant using z-score normalization of the typical price against its SMA/StDev.

Logic (from Pine Script):
    hlc3    = (H + L + C) / 3
    mfi_sma = SMA(hlc3, length)
    mfi_std = STDEV(hlc3, length)
    mfi     = (hlc3 - mfi_sma) / (0.015 * mfi_std) * 1.5

Positive values = bullish (green area on chart).
Negative values = bearish (red area on chart).

Pure function, no I/O.
"""
import math
from typing import List, Optional


def calculate_dacle_mfi(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    length: int = 60,
) -> dict:
    """Calculate Dacle-style MFI oscillator.

    Args:
        highs:   High prices (oldest first).
        lows:    Low prices (oldest first).
        closes:  Close prices (oldest first).
        length:  Lookback window for SMA and StDev (default 60).

    Returns:
        {
            "latest_mfi": float | None,   # Latest MFI value
            "previous_mfi": float | None, # Previous MFI value
            "is_bullish": bool,           # True when latest_mfi > 0
            "crossed_above_zero": bool,   # True when MFI crossed from <=0 to >0
            "crossed_below_zero": bool,   # True when MFI crossed from >=0 to <0
            "mfi_series": list[float],    # Full MFI series (same length as input)
        }
    """
    n = len(closes)
    if n < length or len(highs) < n or len(lows) < n:
        return {
            "latest_mfi": None,
            "previous_mfi": None,
            "is_bullish": False,
            "crossed_above_zero": False,
            "crossed_below_zero": False,
            "mfi_series": [],
        }

    hlc3 = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]

    mfi_series: List[Optional[float]] = [None] * (length - 1)

    for i in range(length - 1, n):
        window = hlc3[i - length + 1 : i + 1]
        sma = sum(window) / length
        variance = sum((x - sma) ** 2 for x in window) / length
        std = math.sqrt(variance)

        if std == 0:
            mfi_series.append(0.0)
        else:
            mfi_val = (hlc3[i] - sma) / (0.015 * std) * 1.5
            mfi_series.append(mfi_val)

    # Latest valid value
    latest_mfi: Optional[float] = None
    previous_mfi: Optional[float] = None
    for v in reversed(mfi_series):
        if v is not None:
            latest_mfi = v
            break

    if latest_mfi is not None:
        latest_idx = len(mfi_series) - 1 - list(reversed(mfi_series)).index(latest_mfi)
        for i in range(latest_idx - 1, -1, -1):
            if mfi_series[i] is not None:
                previous_mfi = mfi_series[i]
                break

    valid_series = [round(v, 4) for v in mfi_series if v is not None]
    crossed_above_zero = (
        previous_mfi is not None and latest_mfi is not None
        and previous_mfi <= 0 < latest_mfi
    )
    crossed_below_zero = (
        previous_mfi is not None and latest_mfi is not None
        and previous_mfi >= 0 > latest_mfi
    )

    return {
        "latest_mfi": round(latest_mfi, 4) if latest_mfi is not None else None,
        "previous_mfi": round(previous_mfi, 4) if previous_mfi is not None else None,
        "is_bullish": (latest_mfi is not None and latest_mfi > 0),
        "crossed_above_zero": crossed_above_zero,
        "crossed_below_zero": crossed_below_zero,
        "mfi_series": valid_series,
    }
