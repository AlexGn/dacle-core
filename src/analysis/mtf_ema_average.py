"""
L086: MTF EMA Average (Multi-Timeframe Smoothed EMA)

Calculates a smoothed average of EMAs across multiple timeframes to provide
a unified trend signal with reduced noise.

Components:
- 1H EMA (200-period): Short-term trend
- 4H EMA (200-period): Medium-term trend
- Daily EMA (200-period): Long-term trend
- MTF EMA Average: Weighted or equal average of all timeframes

Session 302 - January 8, 2026
Source: David's TradingView indicator screenshots
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import numpy as np

logger = logging.getLogger(__name__)


class MTFEMAAnalyzer:
    """
    L086: Multi-Timeframe EMA Average analysis for trend confirmation.

    Usage:
        analyzer = MTFEMAAnalyzer()
        result = analyzer.calculate_mtf_ema_average(ohlcv_by_timeframe, current_price)
    """

    # Default EMA periods for each timeframe
    DEFAULT_PERIODS = {
        '1h': 200,
        '4h': 200,
        '1d': 200
    }

    # HTF weights (higher timeframes get more weight)
    HTF_WEIGHTS = {
        '1h': 0.2,
        '4h': 0.3,
        '1d': 0.5
    }

    def __init__(
        self,
        periods: Optional[Dict[str, int]] = None,
        weighting: str = 'equal'  # 'equal' or 'htf_weighted'
    ):
        """
        Initialize MTF EMA analyzer.

        Args:
            periods: EMA periods per timeframe (default: 200 for all)
            weighting: 'equal' for simple average, 'htf_weighted' for higher TF bias
        """
        self.periods = periods or self.DEFAULT_PERIODS.copy()
        self.weighting = weighting

    def calculate_ema(
        self,
        ohlcv_data: List[Dict],
        period: int = 200
    ) -> Optional[float]:
        """
        Calculate Exponential Moving Average from OHLCV data.

        Args:
            ohlcv_data: List of candles with 'close' or 'c' key
            period: EMA period (default 200)

        Returns:
            EMA value or None if insufficient data
        """
        if not ohlcv_data or len(ohlcv_data) < period:
            logger.warning(f"L086: Insufficient data for EMA-{period} (have {len(ohlcv_data) if ohlcv_data else 0})")
            return None

        # Extract close prices
        closes = []
        for candle in ohlcv_data:
            close = candle.get('close', candle.get('c'))
            if close is not None:
                closes.append(float(close))

        if len(closes) < period:
            logger.warning(f"L086: Insufficient close prices for EMA-{period}")
            return None

        # Calculate EMA using standard formula
        # EMA = Close × k + EMA_prev × (1 - k)
        # where k = 2 / (period + 1)
        k = 2 / (period + 1)

        # Start with SMA for first period
        ema = sum(closes[:period]) / period

        # Calculate EMA for remaining data
        for close in closes[period:]:
            ema = close * k + ema * (1 - k)

        return ema

    def calculate_mtf_ema_average(
        self,
        ohlcv_by_timeframe: Dict[str, List[Dict]],
        current_price: float,
        custom_weighting: Optional[str] = None
    ) -> Dict:
        """
        L086: Calculate Multi-Timeframe EMA Average.

        Args:
            ohlcv_by_timeframe: Dict with keys '1h', '4h', '1d' mapping to OHLCV lists
            current_price: Current token price
            custom_weighting: Override default weighting ('equal' or 'htf_weighted')

        Returns:
            {
                "mtf_ema_avg": float,           # The averaged EMA value
                "ema_1h": float,                # Individual 1H EMA
                "ema_4h": float,                # Individual 4H EMA
                "ema_daily": float,             # Individual Daily EMA
                "current_price": float,
                "price_vs_avg": "ABOVE" | "BELOW" | "AT",
                "trend_bias": "STRONG_BULLISH" | "BULLISH" | "NEUTRAL" | "BEARISH" | "STRONG_BEARISH",
                "distance_pct": float,          # % distance from MTF EMA Avg
                "signal": "LONG_BIAS" | "SHORT_BIAS" | "NO_BIAS",
                "ema_alignment": "ALL_BULLISH" | "ALL_BEARISH" | "MIXED",
                "above_count": int,             # How many EMAs price is above
                "weighting_used": str
            }
        """
        weighting = custom_weighting or self.weighting

        # Calculate individual EMAs
        ema_1h = None
        ema_4h = None
        ema_daily = None

        if '1h' in ohlcv_by_timeframe and ohlcv_by_timeframe['1h']:
            ema_1h = self.calculate_ema(ohlcv_by_timeframe['1h'], self.periods.get('1h', 200))

        if '4h' in ohlcv_by_timeframe and ohlcv_by_timeframe['4h']:
            ema_4h = self.calculate_ema(ohlcv_by_timeframe['4h'], self.periods.get('4h', 200))

        if '1d' in ohlcv_by_timeframe and ohlcv_by_timeframe['1d']:
            ema_daily = self.calculate_ema(ohlcv_by_timeframe['1d'], self.periods.get('1d', 200))

        # Check if we have enough data
        available_emas = []
        ema_values = {'1h': ema_1h, '4h': ema_4h, '1d': ema_daily}

        for tf, ema in ema_values.items():
            if ema is not None:
                available_emas.append((tf, ema))

        if not available_emas:
            logger.warning("L086: No EMA data available for any timeframe")
            return self._empty_result(current_price, weighting)

        # Calculate MTF EMA Average
        if weighting == 'htf_weighted':
            mtf_ema_avg = self._calculate_weighted_average(available_emas)
        else:  # equal weighting
            mtf_ema_avg = sum(ema for _, ema in available_emas) / len(available_emas)

        # Determine price position relative to average
        price_vs_avg = self._classify_price_position(current_price, mtf_ema_avg)

        # Calculate distance
        distance_pct = ((current_price - mtf_ema_avg) / mtf_ema_avg * 100) if mtf_ema_avg else 0

        # Count how many EMAs price is above
        above_count = sum(1 for _, ema in available_emas if current_price > ema)
        total_emas = len(available_emas)

        # Determine EMA alignment
        ema_alignment = self._classify_ema_alignment(current_price, available_emas)

        # Determine trend bias
        trend_bias = self._classify_trend_bias(current_price, mtf_ema_avg, above_count, total_emas)

        # Determine signal
        signal = self._determine_signal(trend_bias, ema_alignment)

        return {
            "mtf_ema_avg": round(mtf_ema_avg, 6) if mtf_ema_avg else None,
            "ema_1h": round(ema_1h, 6) if ema_1h else None,
            "ema_4h": round(ema_4h, 6) if ema_4h else None,
            "ema_daily": round(ema_daily, 6) if ema_daily else None,
            "current_price": current_price,
            "price_vs_avg": price_vs_avg,
            "trend_bias": trend_bias,
            "distance_pct": round(distance_pct, 2),
            "signal": signal,
            "ema_alignment": ema_alignment,
            "above_count": above_count,
            "total_emas": total_emas,
            "weighting_used": weighting
        }

    def _calculate_weighted_average(
        self,
        available_emas: List[Tuple[str, float]]
    ) -> float:
        """
        Calculate HTF-weighted average of available EMAs.

        Higher timeframes get more weight:
        - 1H: 20%
        - 4H: 30%
        - 1D: 50%
        """
        weighted_sum = 0.0
        weight_sum = 0.0

        for tf, ema in available_emas:
            weight = self.HTF_WEIGHTS.get(tf, 0.33)  # Default equal weight
            weighted_sum += ema * weight
            weight_sum += weight

        if weight_sum == 0:
            return sum(ema for _, ema in available_emas) / len(available_emas)

        return weighted_sum / weight_sum

    def _classify_price_position(
        self,
        price: float,
        mtf_ema_avg: float,
        tolerance_pct: float = 0.5
    ) -> str:
        """Classify price position relative to MTF EMA Average."""
        if mtf_ema_avg is None:
            return "UNKNOWN"

        distance_pct = abs(price - mtf_ema_avg) / mtf_ema_avg * 100

        if distance_pct <= tolerance_pct:
            return "AT"
        elif price > mtf_ema_avg:
            return "ABOVE"
        else:
            return "BELOW"

    def _classify_ema_alignment(
        self,
        price: float,
        available_emas: List[Tuple[str, float]]
    ) -> str:
        """
        Classify EMA alignment based on price position.

        ALL_BULLISH: Price above all EMAs
        ALL_BEARISH: Price below all EMAs
        MIXED: Price above some, below others
        """
        if not available_emas:
            return "UNKNOWN"

        above_all = all(price > ema for _, ema in available_emas)
        below_all = all(price < ema for _, ema in available_emas)

        if above_all:
            return "ALL_BULLISH"
        elif below_all:
            return "ALL_BEARISH"
        else:
            return "MIXED"

    def _classify_trend_bias(
        self,
        price: float,
        mtf_ema_avg: float,
        above_count: int,
        total_emas: int
    ) -> str:
        """
        Classify trend strength based on price position relative to all EMAs.

        STRONG_BULLISH: Price above ALL EMAs + above MTF Avg
        BULLISH: Price above MTF Avg + above majority of EMAs
        NEUTRAL: Price near MTF Avg (within 1%)
        BEARISH: Price below MTF Avg + below majority of EMAs
        STRONG_BEARISH: Price below ALL EMAs + below MTF Avg
        """
        if mtf_ema_avg is None or total_emas == 0:
            return "NEUTRAL"

        # Check if at MTF Avg (within 1%)
        distance_pct = abs(price - mtf_ema_avg) / mtf_ema_avg * 100
        if distance_pct <= 1.0:
            return "NEUTRAL"

        above_avg = price > mtf_ema_avg
        majority_above = above_count > total_emas / 2

        if above_count == total_emas and above_avg:
            return "STRONG_BULLISH"
        elif above_avg and majority_above:
            return "BULLISH"
        elif above_count == 0 and not above_avg:
            return "STRONG_BEARISH"
        elif not above_avg and not majority_above:
            return "BEARISH"
        else:
            return "NEUTRAL"

    def _determine_signal(
        self,
        trend_bias: str,
        ema_alignment: str
    ) -> str:
        """
        Determine trading signal based on trend bias and alignment.

        LONG_BIAS: Strong bullish conditions
        SHORT_BIAS: Strong bearish conditions
        NO_BIAS: Mixed or neutral conditions
        """
        if trend_bias in ["STRONG_BULLISH", "BULLISH"] and ema_alignment == "ALL_BULLISH":
            return "LONG_BIAS"
        elif trend_bias in ["STRONG_BEARISH", "BEARISH"] and ema_alignment == "ALL_BEARISH":
            return "SHORT_BIAS"
        else:
            return "NO_BIAS"

    def _empty_result(self, current_price: float, weighting: str) -> Dict:
        """Return empty result structure."""
        return {
            "mtf_ema_avg": None,
            "ema_1h": None,
            "ema_4h": None,
            "ema_daily": None,
            "current_price": current_price,
            "price_vs_avg": "UNKNOWN",
            "trend_bias": "NEUTRAL",
            "distance_pct": 0,
            "signal": "NO_BIAS",
            "ema_alignment": "UNKNOWN",
            "above_count": 0,
            "total_emas": 0,
            "weighting_used": weighting
        }

    def get_comprehensive_ema_analysis(
        self,
        ohlcv_by_timeframe: Dict[str, List[Dict]],
        current_price: float,
        dual_ema_data: Optional[Dict] = None
    ) -> Dict:
        """
        Combines existing L046/L047 EMAs with new MTF EMA Average.

        Args:
            ohlcv_by_timeframe: Dict with OHLCV data per timeframe
            current_price: Current token price
            dual_ema_data: Optional existing L046/L047 analysis

        Returns:
            {
                "dual_ema": {...},        # L046: 12+24 EMA analysis (if provided)
                "mtf_200": {...},         # L047: MTF 200 EMA filter
                "mtf_ema_avg": {...},     # L086: NEW smoothed average
                "overall_trend": "STRONG_BULLISH" | "BULLISH" | "NEUTRAL" | "BEARISH" | "STRONG_BEARISH",
                "confluence_count": int,   # How many EMA systems align
                "recommendation": str
            }
        """
        # Calculate MTF EMA Average (L086)
        mtf_avg_result = self.calculate_mtf_ema_average(ohlcv_by_timeframe, current_price)

        # Build comprehensive result
        result = {
            "dual_ema": dual_ema_data,
            "mtf_ema_avg": mtf_avg_result,
            "overall_trend": mtf_avg_result.get("trend_bias", "NEUTRAL"),
            "confluence_count": 0,
            "recommendation": ""
        }

        # Calculate confluence
        signals = []

        # L086 MTF EMA Avg signal
        if mtf_avg_result.get("signal") == "LONG_BIAS":
            signals.append("BULLISH")
        elif mtf_avg_result.get("signal") == "SHORT_BIAS":
            signals.append("BEARISH")

        # L046 Dual EMA signal (if provided)
        if dual_ema_data:
            if dual_ema_data.get("conviction_modifier", 0) > 0:
                signals.append("BEARISH")  # Bearish for shorts
            elif dual_ema_data.get("conviction_modifier", 0) < 0:
                signals.append("BULLISH")  # Bullish alignment

        # Count confluence
        bullish_count = signals.count("BULLISH")
        bearish_count = signals.count("BEARISH")
        result["confluence_count"] = max(bullish_count, bearish_count)

        # Generate recommendation
        if bullish_count >= 2:
            result["recommendation"] = "STRONG_LONG_BIAS - Multiple EMA systems aligned bullish"
            result["overall_trend"] = "STRONG_BULLISH"
        elif bearish_count >= 2:
            result["recommendation"] = "STRONG_SHORT_BIAS - Multiple EMA systems aligned bearish"
            result["overall_trend"] = "STRONG_BEARISH"
        elif bullish_count == 1:
            result["recommendation"] = "MODERATE_LONG_BIAS - Single EMA system bullish"
        elif bearish_count == 1:
            result["recommendation"] = "MODERATE_SHORT_BIAS - Single EMA system bearish"
        else:
            result["recommendation"] = "NO_BIAS - EMA systems not aligned"

        return result


# Convenience function for external use
def calculate_mtf_ema_average(
    ohlcv_by_timeframe: Dict[str, List[Dict]],
    current_price: float,
    weighting: str = 'equal',
    periods: Optional[Dict[str, int]] = None
) -> Dict:
    """
    L086: Calculate MTF EMA Average (Multi-Timeframe Smoothed EMA).

    Args:
        ohlcv_by_timeframe: Dict with keys '1h', '4h', '1d' mapping to OHLCV lists
        current_price: Current token price
        weighting: 'equal' for simple average, 'htf_weighted' for higher TF bias
        periods: Optional custom EMA periods per timeframe

    Returns:
        MTF EMA analysis dict with trend_bias, signal, and distances
    """
    analyzer = MTFEMAAnalyzer(periods=periods, weighting=weighting)
    return analyzer.calculate_mtf_ema_average(ohlcv_by_timeframe, current_price)


def get_comprehensive_ema_analysis(
    ohlcv_by_timeframe: Dict[str, List[Dict]],
    current_price: float,
    dual_ema_data: Optional[Dict] = None
) -> Dict:
    """
    L086: Get comprehensive EMA analysis combining L046/L047/L086.

    Args:
        ohlcv_by_timeframe: Dict with OHLCV data per timeframe
        current_price: Current token price
        dual_ema_data: Optional existing L046/L047 analysis

    Returns:
        Comprehensive EMA analysis with confluence count and recommendation
    """
    analyzer = MTFEMAAnalyzer()
    return analyzer.get_comprehensive_ema_analysis(ohlcv_by_timeframe, current_price, dual_ema_data)
