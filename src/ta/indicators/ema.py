"""EMA (Exponential Moving Average) indicator.

Migrated from PriceActionAnalyzer._calculate_ema in
src/analysis/price_action_analyzer.py (Session 440).
"""
from typing import List, Optional


def calculate_ema(prices: List[float], period: int) -> list:
    """Calculate Exponential Moving Average.

    Starts with an SMA seed for the first EMA value, then applies
    the standard EMA formula: ema = price * k + prev_ema * (1 - k)
    where k = 2 / (period + 1).

    Args:
        prices: List of prices (oldest first).
        period: EMA period.

    Returns:
        List of EMA values (same length as *prices*).  The first
        ``period - 1`` entries are ``None`` (insufficient history).
    """
    if not prices or len(prices) < period:
        return prices

    multiplier = 2 / (period + 1)
    sma = sum(prices[:period]) / period
    ema_values = [None] * (period - 1) + [sma]

    for i in range(period, len(prices)):
        ema = (prices[i] * multiplier) + (ema_values[-1] * (1 - multiplier))
        ema_values.append(ema)

    return ema_values


def ema_latest(prices: List[float], period: int) -> Optional[float]:
    """Return just the latest EMA value, or ``None`` if insufficient data."""
    values = calculate_ema(prices, period)
    if not values:
        return None
    for v in reversed(values):
        if v is not None:
            return v
    return None
