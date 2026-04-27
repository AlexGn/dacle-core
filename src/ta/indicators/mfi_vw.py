"""Volume-weighted MFI for Market Cipher B replication.

Computes a signed, volume-weighted Money Flow Index normalised to [-100, +100]
using tanh squashing.  Matches TradingView Cipher B behaviour:

    raw_mf = ((close - open) / (high - low + eps)) * volume
    mfi    = SMA(raw_mf, length)
    scaled = tanh(mfi / median_volume) * 100

Positive values = buying pressure (bullish).
Negative values = selling pressure (bearish).
"""

import math
from typing import List, Optional


def calculate_mfi_vw(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    opens: Optional[List[float]] = None,
    volumes: Optional[List[float]] = None,
    length: int = 14,
) -> dict:
    """Volume-weighted MFI.

    Args:
        highs:   High prices (oldest first).
        lows:    Low prices (oldest first).
        closes:  Close prices (oldest first).
        opens:   Open prices (oldest first).  Falls back to previous close when None.
        volumes: Volume (oldest first).  Uses 1.0 when None or zero.
        length:  SMA lookback (default 14).

    Returns:
        {
            "latest_value": float | None,
            "previous_value": float | None,
            "is_bullish": bool,
            "crossed_above_zero": bool,
            "crossed_below_zero": bool,
            "series": list[float],
        }
    """
    n = len(closes)
    if n < length + 1:
        return _empty()

    _opens = opens if opens is not None else [closes[i - 1] if i > 0 else closes[0] for i in range(n)]
    _vols = volumes if volumes is not None else [1.0] * n
    eps = 1e-10

    # 1. Raw money flow per bar
    raw_mf: List[float] = []
    for i in range(n):
        hl_range = highs[i] - lows[i]
        if abs(hl_range) < eps:
            mf = 0.0
        else:
            mf = ((closes[i] - _opens[i]) / hl_range) * max(_vols[i], eps)
        raw_mf.append(mf)

    # 2. SMA of raw MF
    mfi_series: List[Optional[float]] = [None] * (length - 1)
    for i in range(length - 1, n):
        window = raw_mf[i - length + 1 : i + 1]
        sma = sum(window) / length
        mfi_series.append(sma)

    # 3. Scale to [-100, +100] via tanh (volume-normalised)
    # Use median absolute raw_mf as denominator so scaling adapts to volume
    abs_mf = [abs(v) for v in raw_mf if abs(v) > eps]
    median_vol_scale = sorted(abs_mf)[len(abs_mf) // 2] if abs_mf else 1.0
    if median_vol_scale < eps:
        median_vol_scale = 1.0

    scaled_series: List[Optional[float]] = []
    for v in mfi_series:
        if v is None:
            scaled_series.append(None)
        else:
            scaled_series.append(round(math.tanh(v / median_vol_scale) * 100.0, 4))

    # Latest / previous
    latest_value: Optional[float] = None
    previous_value: Optional[float] = None
    for v in reversed(scaled_series):
        if v is not None:
            latest_value = v
            break
    if latest_value is not None:
        idx = len(scaled_series) - 1 - list(reversed(scaled_series)).index(latest_value)
        for i in range(idx - 1, -1, -1):
            if scaled_series[i] is not None:
                previous_value = scaled_series[i]
                break

    valid_series = [v for v in scaled_series if v is not None]

    return {
        "latest_value": latest_value,
        "previous_value": previous_value,
        "is_bullish": bool(latest_value is not None and latest_value > 0),
        "crossed_above_zero": bool(
            previous_value is not None and latest_value is not None
            and previous_value <= 0 < latest_value
        ),
        "crossed_below_zero": bool(
            previous_value is not None and latest_value is not None
            and previous_value >= 0 > latest_value
        ),
        "series": valid_series,
    }


def _empty():
    return {
        "latest_value": None,
        "previous_value": None,
        "is_bullish": False,
        "crossed_above_zero": False,
        "crossed_below_zero": False,
        "series": [],
    }
