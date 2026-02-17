"""RSI (Relative Strength Index) indicator.

Migrated from src/analysis/exhaustion_calculator.py (Session 440).
Uses SMA-based RSI (not Wilder smoothing).
"""
from typing import List


def calculate_rsi(closes: List[float], period: int = 14) -> float:
    """Calculate RSI from close prices.

    Args:
        closes: List of closing prices (oldest first).
        period: Lookback period (default 14).

    Returns:
        RSI value between 0 and 100. Returns 50.0 (neutral) if
        insufficient data.
    """
    if len(closes) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
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
    return 100 - (100 / (1 + rs))
