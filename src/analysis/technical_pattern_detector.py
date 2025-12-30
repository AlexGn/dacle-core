#!/usr/bin/env python3
"""
Technical Pattern Detector - Tier 1 Rule-Based TA Automation

Implements free, fast heuristics for detecting:
1. Trendline breaks (support/resistance breakdown)
2. Rejection candles (wick-dominant patterns)
3. Retest confirmations (price return + rejection)

Uses CCXT OHLCV data only (no paid APIs). Expected accuracy: 65-75%.

Migration History:
- Session 267: Migrated from scripts/helpers/technical_pattern_detector.py

Usage:
    from src.analysis.technical_pattern_detector import TechnicalPatternDetector
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np

from src.data.multi_source_fetcher import MultiSourceFetcher

logger = logging.getLogger(__name__)


@dataclass
class PatternResult:
    """Result of pattern detection."""
    met: bool
    confidence: float  # 0.0-1.0
    reason: str
    data: Optional[Dict] = None


class TrendlineBreakDetector:
    """
    Detect trendline breaks using swing points and EMA crossovers.

    Algorithm:
    1. Calculate swing highs/lows over 14-period lookback
    2. Identify support/resistance zones (±2% around swing points)
    3. Detect EMA crossovers (20/50 period)
    4. Confirm with volume surge (1.5x 20-period average)
    """

    def __init__(self, lookback_periods: int = 14, swing_threshold: float = 0.02):
        self.lookback_periods = lookback_periods
        self.swing_threshold = swing_threshold
        self.fetcher = MultiSourceFetcher()

    def detect_break(
        self,
        token_symbol: str,
        direction: str,  # 'upside' or 'downside'
        timeframe: str = '4h'
    ) -> PatternResult:
        """
        Detect trendline break for given token and direction.

        Args:
            token_symbol: Token to analyze (e.g., 'RLS/USDT')
            direction: 'upside' (bullish break) or 'downside' (bearish break)
            timeframe: Candlestick timeframe (default: 4h)

        Returns:
            PatternResult with met status, confidence, and reasoning
        """
        try:
            # Fetch OHLCV data (100 candles for EMA calculation)
            ohlcv_data = self._fetch_ohlcv(token_symbol, timeframe, limit=100)
            if not ohlcv_data:
                return PatternResult(
                    met=False,
                    confidence=0.0,
                    reason=f"No data available for {token_symbol}"
                )

            # Extract price and volume arrays
            timestamps = [c[0] for c in ohlcv_data]
            opens = np.array([c[1] for c in ohlcv_data])
            highs = np.array([c[2] for c in ohlcv_data])
            lows = np.array([c[3] for c in ohlcv_data])
            closes = np.array([c[4] for c in ohlcv_data])
            volumes = np.array([c[5] for c in ohlcv_data])

            # Calculate indicators
            swing_high, swing_low = self._find_swing_points(highs, lows, closes)
            ema_20 = self._calculate_ema(closes, 20)
            ema_50 = self._calculate_ema(closes, 50)
            volume_avg = self._calculate_sma(volumes, 20)

            current_price = closes[-1]
            current_volume = volumes[-1]

            # Detect break based on direction
            if direction == 'downside':
                result = self._detect_downside_break(
                    current_price, swing_low, ema_20, ema_50,
                    current_volume, volume_avg[-1], closes
                )
            elif direction == 'upside':
                result = self._detect_upside_break(
                    current_price, swing_high, ema_20, ema_50,
                    current_volume, volume_avg[-1], closes
                )
            else:
                return PatternResult(
                    met=False,
                    confidence=0.0,
                    reason=f"Invalid direction: {direction}"
                )

            return result

        except Exception as e:
            logger.error(f"Error detecting trendline break for {token_symbol}: {e}")
            return PatternResult(
                met=False,
                confidence=0.0,
                reason=f"Error: {str(e)}"
            )

    def _fetch_ohlcv(
        self,
        token_symbol: str,
        timeframe: str,
        limit: int
    ) -> Optional[List]:
        """Fetch OHLCV data using MultiSourceFetcher."""
        try:
            # Extract token symbol without /USDT pair
            symbol_only = token_symbol.split('/')[0] if '/' in token_symbol else token_symbol

            result = self.fetcher.get_ohlcv_adaptive(
                symbol=symbol_only,
                timeframe=timeframe,
                limit=limit
            )
            return result.get('ohlcv') if result.get('ohlcv') else None
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {token_symbol}: {e}")
            return None

    def _find_swing_points(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray
    ) -> Tuple[float, float]:
        """
        Find swing high and swing low over lookback period.

        Returns:
            (swing_high, swing_low)
        """
        lookback = min(self.lookback_periods, len(highs))
        recent_highs = highs[-lookback:]
        recent_lows = lows[-lookback:]

        swing_high = np.max(recent_highs)
        swing_low = np.min(recent_lows)

        return swing_high, swing_low

    def _calculate_ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """Calculate Exponential Moving Average."""
        if len(data) < period:
            return np.array([data[-1]] * len(data))

        alpha = 2 / (period + 1)
        ema = np.zeros(len(data))
        ema[0] = data[0]

        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i-1]

        return ema

    def _calculate_sma(self, data: np.ndarray, period: int) -> np.ndarray:
        """Calculate Simple Moving Average."""
        if len(data) < period:
            return np.array([np.mean(data)] * len(data))

        sma = np.convolve(data, np.ones(period), 'valid') / period
        # Pad beginning with first value
        padding = np.array([sma[0]] * (len(data) - len(sma)))
        return np.concatenate([padding, sma])

    def _detect_downside_break(
        self,
        current_price: float,
        swing_low: float,
        ema_20: np.ndarray,
        ema_50: np.ndarray,
        current_volume: float,
        avg_volume: float,
        closes: np.ndarray
    ) -> PatternResult:
        """Detect bearish (downside) trendline break."""
        confidence_factors = []
        reasons = []

        # Factor 1: Price below swing low (support break)
        support_zone = swing_low * (1 + self.swing_threshold)
        if current_price < support_zone:
            confidence_factors.append(0.35)
            reasons.append(f"Price ${current_price:.4f} broke below support ${swing_low:.4f}")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"Price ${current_price:.4f} still above support ${swing_low:.4f}")

        # Factor 2: Death cross (EMA 20 below EMA 50)
        if ema_20[-1] < ema_50[-1] and ema_20[-2] >= ema_50[-2]:
            confidence_factors.append(0.30)
            reasons.append(f"Death cross confirmed (EMA20 crossed below EMA50)")
        elif ema_20[-1] < ema_50[-1]:
            confidence_factors.append(0.15)
            reasons.append(f"EMA20 below EMA50 (bearish)")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"EMA20 above EMA50 (not bearish)")

        # Factor 3: Volume surge (1.5x average)
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
        if volume_ratio >= 1.5:
            confidence_factors.append(0.25)
            reasons.append(f"Volume surge {volume_ratio:.1f}x average")
        elif volume_ratio >= 1.2:
            confidence_factors.append(0.10)
            reasons.append(f"Volume elevated {volume_ratio:.1f}x average")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"Volume below average ({volume_ratio:.1f}x)")

        # Factor 4: Momentum confirmation (3 consecutive lower closes)
        if len(closes) >= 3 and closes[-1] < closes[-2] < closes[-3]:
            confidence_factors.append(0.10)
            reasons.append("3 consecutive lower closes")
        else:
            confidence_factors.append(0.0)

        total_confidence = sum(confidence_factors)
        met = total_confidence >= 0.50  # 50% threshold for "met"

        reason_text = " | ".join(reasons)

        return PatternResult(
            met=met,
            confidence=total_confidence,
            reason=reason_text,
            data={
                'current_price': current_price,
                'swing_low': swing_low,
                'ema_20': ema_20[-1],
                'ema_50': ema_50[-1],
                'volume_ratio': volume_ratio
            }
        )

    def _detect_upside_break(
        self,
        current_price: float,
        swing_high: float,
        ema_20: np.ndarray,
        ema_50: np.ndarray,
        current_volume: float,
        avg_volume: float,
        closes: np.ndarray
    ) -> PatternResult:
        """Detect bullish (upside) trendline break."""
        confidence_factors = []
        reasons = []

        # Factor 1: Price above swing high (resistance break)
        resistance_zone = swing_high * (1 - self.swing_threshold)
        if current_price > resistance_zone:
            confidence_factors.append(0.35)
            reasons.append(f"Price ${current_price:.4f} broke above resistance ${swing_high:.4f}")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"Price ${current_price:.4f} still below resistance ${swing_high:.4f}")

        # Factor 2: Golden cross (EMA 20 above EMA 50)
        if ema_20[-1] > ema_50[-1] and ema_20[-2] <= ema_50[-2]:
            confidence_factors.append(0.30)
            reasons.append(f"Golden cross confirmed (EMA20 crossed above EMA50)")
        elif ema_20[-1] > ema_50[-1]:
            confidence_factors.append(0.15)
            reasons.append(f"EMA20 above EMA50 (bullish)")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"EMA20 below EMA50 (not bullish)")

        # Factor 3: Volume surge
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
        if volume_ratio >= 1.5:
            confidence_factors.append(0.25)
            reasons.append(f"Volume surge {volume_ratio:.1f}x average")
        elif volume_ratio >= 1.2:
            confidence_factors.append(0.10)
            reasons.append(f"Volume elevated {volume_ratio:.1f}x average")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"Volume below average ({volume_ratio:.1f}x)")

        # Factor 4: Momentum confirmation (3 consecutive higher closes)
        if len(closes) >= 3 and closes[-1] > closes[-2] > closes[-3]:
            confidence_factors.append(0.10)
            reasons.append("3 consecutive higher closes")
        else:
            confidence_factors.append(0.0)

        total_confidence = sum(confidence_factors)
        met = total_confidence >= 0.50

        reason_text = " | ".join(reasons)

        return PatternResult(
            met=met,
            confidence=total_confidence,
            reason=reason_text,
            data={
                'current_price': current_price,
                'swing_high': swing_high,
                'ema_20': ema_20[-1],
                'ema_50': ema_50[-1],
                'volume_ratio': volume_ratio
            }
        )


class CandlestickAnalyzer:
    """
    Detect rejection candles (pin bars, hammers, shooting stars).

    Algorithm:
    1. Analyze last N candles for wick-to-body ratio
    2. Check close position within range
    3. Verify volume behavior (declining on rejection)
    """

    def __init__(self):
        self.fetcher = MultiSourceFetcher()

    def detect_rejection(
        self,
        token_symbol: str,
        level: float,
        direction: str = 'downside',  # 'downside' = bearish rejection, 'upside' = bullish rejection
        timeframe: str = '4h',
        lookback: int = 3
    ) -> PatternResult:
        """
        Detect rejection candles at a key level.

        Args:
            token_symbol: Token to analyze
            level: Price level where rejection should occur
            direction: 'downside' (reject from resistance) or 'upside' (reject from support)
            timeframe: Candlestick timeframe
            lookback: Number of recent candles to analyze

        Returns:
            PatternResult with rejection detection
        """
        try:
            # Fetch recent candles
            ohlcv_data = self._fetch_ohlcv(token_symbol, timeframe, limit=lookback + 20)
            if not ohlcv_data or len(ohlcv_data) < lookback:
                return PatternResult(
                    met=False,
                    confidence=0.0,
                    reason=f"Insufficient data for {token_symbol}"
                )

            # Analyze last N candles
            recent_candles = ohlcv_data[-lookback:]
            volumes = np.array([c[5] for c in ohlcv_data])
            volume_avg = np.mean(volumes[:-lookback])

            rejection_found = False
            best_confidence = 0.0
            best_reason = ""
            best_data = None

            for i, candle in enumerate(recent_candles):
                timestamp, open_price, high, low, close, volume = candle

                # Check if candle touched the level
                level_tolerance = level * 0.02  # ±2%
                touched_level = (low <= level + level_tolerance and high >= level - level_tolerance)

                if not touched_level:
                    continue

                # Analyze candle structure
                body_size = abs(close - open_price)
                total_range = high - low

                if total_range == 0:
                    continue

                upper_wick = high - max(open_price, close)
                lower_wick = min(open_price, close) - low

                # Detect rejection based on direction
                if direction == 'downside':
                    result = self._detect_bearish_rejection(
                        open_price, high, low, close, body_size, total_range,
                        upper_wick, lower_wick, volume, volume_avg, level
                    )
                else:
                    result = self._detect_bullish_rejection(
                        open_price, high, low, close, body_size, total_range,
                        upper_wick, lower_wick, volume, volume_avg, level
                    )

                if result.confidence > best_confidence:
                    best_confidence = result.confidence
                    best_reason = result.reason
                    best_data = result.data
                    rejection_found = result.met

            if rejection_found:
                return PatternResult(
                    met=True,
                    confidence=best_confidence,
                    reason=best_reason,
                    data=best_data
                )
            else:
                return PatternResult(
                    met=False,
                    confidence=best_confidence,
                    reason=f"No strong rejection at ${level:.4f} in last {lookback} candles",
                    data=best_data
                )

        except Exception as e:
            logger.error(f"Error detecting rejection for {token_symbol}: {e}")
            return PatternResult(
                met=False,
                confidence=0.0,
                reason=f"Error: {str(e)}"
            )

    def _fetch_ohlcv(
        self,
        token_symbol: str,
        timeframe: str,
        limit: int
    ) -> Optional[List]:
        """Fetch OHLCV data."""
        try:
            # Extract token symbol without /USDT pair
            symbol_only = token_symbol.split('/')[0] if '/' in token_symbol else token_symbol

            result = self.fetcher.get_ohlcv_adaptive(
                symbol=symbol_only,
                timeframe=timeframe,
                limit=limit
            )
            return result.get('ohlcv') if result.get('ohlcv') else None
        except Exception as e:
            logger.error(f"Error fetching OHLCV: {e}")
            return None

    def _detect_bearish_rejection(
        self,
        open_price: float,
        high: float,
        low: float,
        close: float,
        body_size: float,
        total_range: float,
        upper_wick: float,
        lower_wick: float,
        volume: float,
        volume_avg: float,
        level: float
    ) -> PatternResult:
        """Detect bearish rejection (shooting star, bearish pin bar)."""
        confidence_factors = []
        reasons = []

        # Factor 1: Upper wick dominant (>2x body size)
        wick_to_body_ratio = upper_wick / body_size if body_size > 0 else 0
        if wick_to_body_ratio >= 2.5:
            confidence_factors.append(0.40)
            reasons.append(f"Strong upper wick ({wick_to_body_ratio:.1f}x body)")
        elif wick_to_body_ratio >= 1.5:
            confidence_factors.append(0.20)
            reasons.append(f"Upper wick present ({wick_to_body_ratio:.1f}x body)")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"Weak upper wick ({wick_to_body_ratio:.1f}x body)")

        # Factor 2: Close in lower 30% of range
        close_position = (close - low) / total_range if total_range > 0 else 0.5
        if close_position <= 0.30:
            confidence_factors.append(0.30)
            reasons.append(f"Close in lower {close_position*100:.0f}% of range")
        elif close_position <= 0.50:
            confidence_factors.append(0.15)
            reasons.append(f"Close below midpoint ({close_position*100:.0f}%)")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"Close too high ({close_position*100:.0f}%)")

        # Factor 3: Volume declining or average (not surging)
        volume_ratio = volume / volume_avg if volume_avg > 0 else 1.0
        if volume_ratio <= 0.8:
            confidence_factors.append(0.20)
            reasons.append(f"Volume declining ({volume_ratio:.1f}x avg)")
        elif volume_ratio <= 1.2:
            confidence_factors.append(0.10)
            reasons.append(f"Volume neutral ({volume_ratio:.1f}x avg)")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"Volume high ({volume_ratio:.1f}x avg)")

        # Factor 4: Red candle (bearish)
        if close < open_price:
            confidence_factors.append(0.10)
            reasons.append("Bearish close")
        else:
            confidence_factors.append(0.0)

        total_confidence = sum(confidence_factors)
        met = total_confidence >= 0.55  # Higher threshold for rejection candles

        reason_text = f"At ${level:.4f}: " + " | ".join(reasons)

        return PatternResult(
            met=met,
            confidence=total_confidence,
            reason=reason_text,
            data={
                'candle_high': high,
                'candle_low': low,
                'close': close,
                'wick_to_body_ratio': wick_to_body_ratio,
                'close_position': close_position,
                'volume_ratio': volume_ratio
            }
        )

    def _detect_bullish_rejection(
        self,
        open_price: float,
        high: float,
        low: float,
        close: float,
        body_size: float,
        total_range: float,
        upper_wick: float,
        lower_wick: float,
        volume: float,
        volume_avg: float,
        level: float
    ) -> PatternResult:
        """Detect bullish rejection (hammer, bullish pin bar)."""
        confidence_factors = []
        reasons = []

        # Factor 1: Lower wick dominant (>2x body size)
        wick_to_body_ratio = lower_wick / body_size if body_size > 0 else 0
        if wick_to_body_ratio >= 2.5:
            confidence_factors.append(0.40)
            reasons.append(f"Strong lower wick ({wick_to_body_ratio:.1f}x body)")
        elif wick_to_body_ratio >= 1.5:
            confidence_factors.append(0.20)
            reasons.append(f"Lower wick present ({wick_to_body_ratio:.1f}x body)")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"Weak lower wick ({wick_to_body_ratio:.1f}x body)")

        # Factor 2: Close in upper 30% of range
        close_position = (close - low) / total_range if total_range > 0 else 0.5
        if close_position >= 0.70:
            confidence_factors.append(0.30)
            reasons.append(f"Close in upper {close_position*100:.0f}% of range")
        elif close_position >= 0.50:
            confidence_factors.append(0.15)
            reasons.append(f"Close above midpoint ({close_position*100:.0f}%)")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"Close too low ({close_position*100:.0f}%)")

        # Factor 3: Volume declining or average
        volume_ratio = volume / volume_avg if volume_avg > 0 else 1.0
        if volume_ratio <= 0.8:
            confidence_factors.append(0.20)
            reasons.append(f"Volume declining ({volume_ratio:.1f}x avg)")
        elif volume_ratio <= 1.2:
            confidence_factors.append(0.10)
            reasons.append(f"Volume neutral ({volume_ratio:.1f}x avg)")
        else:
            confidence_factors.append(0.0)
            reasons.append(f"Volume high ({volume_ratio:.1f}x avg)")

        # Factor 4: Green candle (bullish)
        if close > open_price:
            confidence_factors.append(0.10)
            reasons.append("Bullish close")
        else:
            confidence_factors.append(0.0)

        total_confidence = sum(confidence_factors)
        met = total_confidence >= 0.55

        reason_text = f"At ${level:.4f}: " + " | ".join(reasons)

        return PatternResult(
            met=met,
            confidence=total_confidence,
            reason=reason_text,
            data={
                'candle_high': high,
                'candle_low': low,
                'close': close,
                'wick_to_body_ratio': wick_to_body_ratio,
                'close_position': close_position,
                'volume_ratio': volume_ratio
            }
        )


class RetestDetector:
    """
    Detect retest confirmations (breakdown → return → rejection).

    Algorithm:
    1. Identify recent breakdown level (within 24-48h)
    2. Check if price returned to within 2% of level
    3. Verify rejection occurred (declining volume + lower high)
    """

    def __init__(self, retest_window_hours: int = 48):
        self.retest_window_hours = retest_window_hours
        self.fetcher = MultiSourceFetcher()

    def detect_retest(
        self,
        token_symbol: str,
        level: float,
        direction: str = 'downside',  # 'downside' = retest resistance, 'upside' = retest support
        timeframe: str = '4h'
    ) -> PatternResult:
        """
        Detect retest confirmation after breakdown.

        Args:
            token_symbol: Token to analyze
            level: Key level that was broken
            direction: 'downside' (broken support, now resistance) or 'upside' (broken resistance, now support)
            timeframe: Candlestick timeframe

        Returns:
            PatternResult with retest detection
        """
        try:
            # Calculate required candles for retest window
            if timeframe == '4h':
                candles_per_day = 6
            elif timeframe == '1h':
                candles_per_day = 24
            else:
                candles_per_day = 6  # Default

            required_candles = int((self.retest_window_hours / 24) * candles_per_day * 1.5)

            # Fetch data
            ohlcv_data = self._fetch_ohlcv(token_symbol, timeframe, limit=required_candles)
            if not ohlcv_data or len(ohlcv_data) < 10:
                return PatternResult(
                    met=False,
                    confidence=0.0,
                    reason=f"Insufficient data for {token_symbol}"
                )

            # Find breakdown event
            breakdown_index = self._find_breakdown(ohlcv_data, level, direction)
            if breakdown_index is None:
                return PatternResult(
                    met=False,
                    confidence=0.0,
                    reason=f"No breakdown found at ${level:.4f} in last {self.retest_window_hours}h"
                )

            # Check for retest after breakdown
            retest_candles = ohlcv_data[breakdown_index:]
            result = self._analyze_retest(retest_candles, level, direction)

            return result

        except Exception as e:
            logger.error(f"Error detecting retest for {token_symbol}: {e}")
            return PatternResult(
                met=False,
                confidence=0.0,
                reason=f"Error: {str(e)}"
            )

    def _fetch_ohlcv(
        self,
        token_symbol: str,
        timeframe: str,
        limit: int
    ) -> Optional[List]:
        """Fetch OHLCV data."""
        try:
            # Extract token symbol without /USDT pair
            symbol_only = token_symbol.split('/')[0] if '/' in token_symbol else token_symbol

            result = self.fetcher.get_ohlcv_adaptive(
                symbol=symbol_only,
                timeframe=timeframe,
                limit=limit
            )
            return result.get('ohlcv') if result.get('ohlcv') else None
        except Exception as e:
            logger.error(f"Error fetching OHLCV: {e}")
            return None

    def _find_breakdown(
        self,
        ohlcv_data: List,
        level: float,
        direction: str
    ) -> Optional[int]:
        """
        Find the index where breakdown occurred.

        Returns:
            Index of breakdown candle, or None if not found
        """
        level_tolerance = level * 0.02  # ±2%

        for i in range(len(ohlcv_data) - 1, 0, -1):  # Search backwards
            timestamp, open_price, high, low, close, volume = ohlcv_data[i]
            prev_close = ohlcv_data[i-1][4]

            if direction == 'downside':
                # Looking for support break (close below level)
                if prev_close >= level - level_tolerance and close < level - level_tolerance:
                    return i
            else:
                # Looking for resistance break (close above level)
                if prev_close <= level + level_tolerance and close > level + level_tolerance:
                    return i

        return None

    def _analyze_retest(
        self,
        retest_candles: List,
        level: float,
        direction: str
    ) -> PatternResult:
        """Analyze candles after breakdown for retest confirmation."""
        if len(retest_candles) < 2:
            return PatternResult(
                met=False,
                confidence=0.0,
                reason="Not enough data after breakdown"
            )

        level_tolerance = level * 0.02

        # Track retest characteristics
        touched_level = False
        rejection_found = False
        lower_high_formed = False
        volume_declining = False

        highs = [c[2] for c in retest_candles]
        lows = [c[3] for c in retest_candles]
        closes = [c[4] for c in retest_candles]
        volumes = np.array([c[5] for c in retest_candles])

        breakdown_price = closes[0]

        # Check if price returned to level
        if direction == 'downside':
            # After breakdown, looking for retest from below
            for high in highs[1:]:
                if high >= level - level_tolerance:
                    touched_level = True
                    break
        else:
            # After breakout, looking for retest from above
            for low in lows[1:]:
                if low <= level + level_tolerance:
                    touched_level = True
                    break

        if not touched_level:
            return PatternResult(
                met=False,
                confidence=0.0,
                reason=f"Price has not returned to ${level:.4f} after breakdown"
            )

        # Check for rejection (lower high for downtrend, higher low for uptrend)
        if direction == 'downside':
            # Looking for lower high (failed to reclaim level)
            max_high_after_breakdown = max(highs[1:])
            if max_high_after_breakdown < level:
                rejection_found = True
            if len(highs) >= 3 and highs[-1] < highs[-2]:
                lower_high_formed = True
        else:
            # Looking for higher low (failed to break below level)
            min_low_after_breakout = min(lows[1:])
            if min_low_after_breakout > level:
                rejection_found = True
            if len(lows) >= 3 and lows[-1] > lows[-2]:
                lower_high_formed = True

        # Check volume trend (declining suggests rejection)
        if len(volumes) >= 3:
            recent_volume_avg = np.mean(volumes[-3:])
            earlier_volume_avg = np.mean(volumes[:3])
            if recent_volume_avg < earlier_volume_avg * 0.8:
                volume_declining = True

        # Calculate confidence
        confidence_factors = []
        reasons = []

        if touched_level:
            confidence_factors.append(0.30)
            reasons.append(f"Price returned to ${level:.4f}")

        if rejection_found:
            confidence_factors.append(0.40)
            reasons.append("Failed to reclaim level")
        else:
            reasons.append("Level reclaimed (no rejection)")

        if lower_high_formed:
            confidence_factors.append(0.20)
            reasons.append("Lower high formed")

        if volume_declining:
            confidence_factors.append(0.10)
            reasons.append("Volume declining on retest")

        total_confidence = sum(confidence_factors)
        met = total_confidence >= 0.60  # 60% threshold for retest confirmation

        reason_text = " | ".join(reasons)

        return PatternResult(
            met=met,
            confidence=total_confidence,
            reason=reason_text,
            data={
                'level': level,
                'touched': touched_level,
                'rejected': rejection_found,
                'lower_high': lower_high_formed,
                'volume_declining': volume_declining,
                'current_price': closes[-1]
            }
        )
