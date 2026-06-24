"""WaveTrend oscillator — Dacle Cipher blue waves.

Replicates the Market Cipher B / Dacle Cipher Pine Script v6 WaveTrend logic.
Pure function, no I/O.

Logic:
    ap  = (H + L + C) / 3          (typical price)
    esa = EMA(ap, n1)
    d   = EMA(|ap - esa|, n1)
    ci  = (ap - esa) / (0.015 * d)
    wt1 = EMA(ci, n2)              <- Wave 1 (blue)
    wt2 = SMA(wt1, 4)              <- Wave 2 (red)
"""
from typing import List, Optional

from src.ta.indicators.ema import calculate_ema


def calculate_wavetrend(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    n1: int = 10,
    n2: int = 21,
) -> dict:
    """Calculate WaveTrend oscillator.

    Args:
        highs:   High prices (oldest first).
        lows:    Low prices (oldest first).
        closes:  Close prices (oldest first).
        n1:      Channel length (EMA period for smoothing typical price).
        n2:      Average length (EMA period for CI to produce WT1).

    Returns:
        {
            "wt1": float | None,          # Wave 1 (latest value)
            "wt2": float | None,          # Wave 2 (latest value, SMA-4 of wt1)
            "long_signal": bool,          # wt1 crossed above wt2 AND wt1 < -60
            "short_signal": bool,         # wt1 crossed under wt2 AND wt1 > 60
            "zone": str,                  # "overbought" | "oversold" | "neutral"
            "wt1_series": list[float],    # Full WT1 series (for downstream use)
            "wt2_series": list[float],    # Full WT2 series (for downstream use)
        }
    """
    n = len(closes)
    min_len = n1 + n2 + 4  # need enough bars for nested EMAs
    if n < min_len or len(highs) < n or len(lows) < n:
        return {
            "wt1": None,
            "wt2": None,
            "long_signal": False,
            "short_signal": False,
            "zone": "neutral",
            "wt1_series": [],
            "wt2_series": [],
        }

    # Step 1: typical price
    ap = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]

    # Step 2: ESA = EMA(ap, n1)
    esa_raw = calculate_ema(ap, n1)

    # Step 3: d = EMA(|ap - esa|, n1)
    abs_diff = []
    for i in range(n):
        if esa_raw[i] is None:
            abs_diff.append(0.0)
        else:
            abs_diff.append(abs(ap[i] - esa_raw[i]))

    d_raw = calculate_ema(abs_diff, n1)

    # Step 4: CI = (ap - esa) / (0.015 * d)
    ci: List[Optional[float]] = []
    for i in range(n):
        if esa_raw[i] is None or d_raw[i] is None or d_raw[i] == 0:
            ci.append(None)
        else:
            ci.append((ap[i] - esa_raw[i]) / (0.015 * d_raw[i]))

    # Replace None with 0 before feeding into next EMA
    ci_filled = [v if v is not None else 0.0 for v in ci]

    # Step 5: WT1 = EMA(ci, n2)
    wt1_raw = calculate_ema(ci_filled, n2)

    # Step 6: WT2 = SMA(wt1, 4)  — simple moving average (TradingView uses SMA here)
    wt2_raw: List[Optional[float]] = []
    for i in range(len(wt1_raw)):
        if wt1_raw[i] is None:
            wt2_raw.append(None)
        elif i < 3:
            wt2_raw.append(None)
        else:
            window = [v for v in wt1_raw[i - 3 : i + 1] if v is not None]
            if len(window) == 4:
                wt2_raw.append(sum(window) / 4.0)
            else:
                wt2_raw.append(None)

    # Extract valid series (strip leading Nones for downstream)
    wt1_series = [v for v in wt1_raw if v is not None]
    wt2_series = [v for v in wt2_raw if v is not None]

    # Latest values
    wt1_latest: Optional[float] = None
    for v in reversed(wt1_raw):
        if v is not None:
            wt1_latest = v
            break

    wt2_latest: Optional[float] = None
    for v in reversed(wt2_raw):
        if v is not None:
            wt2_latest = v
            break

    # Crossover detection: compare [current] vs [previous] relationship
    long_signal = False
    short_signal = False

    valid_pairs = [
        (w1, w2)
        for w1, w2 in zip(wt1_raw, wt2_raw)
        if w1 is not None and w2 is not None
    ]

    if len(valid_pairs) >= 2:
        prev_w1, prev_w2 = valid_pairs[-2]
        curr_w1, curr_w2 = valid_pairs[-1]

        if prev_w1 <= prev_w2 and curr_w1 > curr_w2 and curr_w1 < -60:
            long_signal = True
        if prev_w1 >= prev_w2 and curr_w1 < curr_w2 and curr_w1 > 60:
            short_signal = True

    # Zone
    zone = "neutral"
    if wt1_latest is not None:
        if wt1_latest > 60:
            zone = "overbought"
        elif wt1_latest < -60:
            zone = "oversold"

    return {
        "wt1": round(wt1_latest, 4) if wt1_latest is not None else None,
        "wt2": round(wt2_latest, 4) if wt2_latest is not None else None,
        "long_signal": long_signal,
        "short_signal": short_signal,
        "zone": zone,
        "wt1_series": [round(v, 4) for v in wt1_series],
        "wt2_series": [round(v, 4) for v in wt2_series],
    }
