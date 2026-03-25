"""
Choppiness Index Indicator.
Formula: 100 * LOG10( SUM(ATR(1), n) / (MAX(High, n) - MIN(Low, n)) ) / LOG10(n)
Higher values = sideways/choppy.
Lower values = trending.
"""
import math
from typing import List, Dict, Optional

def calculate_choppiness(ohlcv_data: List[List], period: int = 14) -> float:
    """
    Calculate the Choppiness Index.
    
    Args:
        ohlcv_data: List of candles in CCXT format [ts, o, h, l, c, v]
        period: Lookback period (default 14)
        
    Returns:
        float: Choppiness value (usually 0-100)
    """
    if not ohlcv_data or len(ohlcv_data) < period + 1:
        return 50.0 # Return neutral if insufficient data
        
    try:
        # 1. Calculate ATR(1) for each candle
        true_ranges = []
        for i in range(1, len(ohlcv_data)):
            h = ohlcv_data[i][2]
            l = ohlcv_data[i][3]
            pc = ohlcv_data[i-1][4]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            true_ranges.append(tr)
            
        # 2. Get the last 'period' true ranges and their sum
        recent_tr = true_ranges[-period:]
        tr_sum = sum(recent_tr)
        
        # 3. Get the high of high and low of low for the period
        recent_candles = ohlcv_data[-period:]
        highest_high = max(c[2] for c in recent_candles)
        lowest_low = min(c[3] for c in recent_candles)
        
        price_range = highest_high - lowest_low
        if price_range == 0:
            return 100.0 # Perfectly choppy
            
        # 4. Apply formula
        choppiness = 100 * math.log10(tr_sum / price_range) / math.log10(period)
        return float(choppiness)
        
    except Exception:
        return 50.0
