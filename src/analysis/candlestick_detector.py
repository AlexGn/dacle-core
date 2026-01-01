#!/usr/bin/env python3
"""
DEPRECATED: This module has been migrated to src/analysis/technical_patterns.py

Please update your imports:
    OLD: from src.analysis.candlestick_detector import CandlestickDetector
    NEW: from src.analysis.technical_patterns import CandlestickDetector

This file will be removed in a future release.

---
Candlestick Pattern Detector for TGE Short Execution Timing

Implements 14 patterns from Kaizen guide with TGE-optimized thresholds.
Focuses on BEARISH reversal patterns for SHORT entry signals.

Learning 022: Candlestick Pattern Recognition
Session 209: Initial Implementation
Session 256: DEPRECATED - Migrated to src/analysis/technical_patterns.py
Date: 2025-12-18

Pattern Categories:
1. NEUTRAL (2): Doji, Spinning Top
2. BULLISH REVERSAL (6): Morning Star, Morning Doji Star, Bullish Engulfing,
                          Dragonfly Doji, Hammer, Inverted Hammer
3. BEARISH REVERSAL (6): Evening Star, Evening Doji Star, Bearish Engulfing,
                          Gravestone Doji, Shooting Star, Hanging Man

Usage:
    from src.analysis.candlestick_detector import CandlestickDetector

    detector = CandlestickDetector(timeframe="4h")
    signal = detector.get_short_signal(ohlcv_data, context="at_resistance")

    if signal["recommendation"] == "ENTER_SHORT":
        print(f"Bearish pattern detected: {signal['patterns_detected']}")
"""

import warnings
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import logging

# Emit deprecation warning on import
warnings.warn(
    "scripts.helpers.candlestick_detector is deprecated. "
    "Use src.analysis.technical_patterns instead.",
    DeprecationWarning,
    stacklevel=2
)

logger = logging.getLogger(__name__)


class PatternType(Enum):
    """Pattern classification for signal routing."""
    BULLISH_REVERSAL = "bullish_reversal"
    BEARISH_REVERSAL = "bearish_reversal"
    NEUTRAL = "neutral"


class PatternStrength(Enum):
    """Reliability tier based on candle count and confirmation."""
    STRONG = 1.0      # 3-candle patterns (most reliable)
    MODERATE = 0.75   # 2-candle patterns
    WEAK = 0.5        # 1-candle patterns


@dataclass
class CandleData:
    """Single candlestick data structure with computed properties."""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def body(self) -> float:
        """Absolute body size (distance between open and close)."""
        return abs(self.close - self.open)

    @property
    def total_range(self) -> float:
        """Total candle range (high - low)."""
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        """Upper wick length (high to top of body)."""
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        """Lower wick length (bottom of body to low)."""
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        """True if close > open (green candle)."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """True if close < open (red candle)."""
        return self.close < self.open

    @property
    def body_pct(self) -> float:
        """Body as percentage of total range."""
        if self.total_range == 0:
            return 0
        return self.body / self.total_range


@dataclass
class PatternResult:
    """Detected pattern with metadata for decision making."""
    pattern_name: str
    pattern_type: PatternType
    strength: PatternStrength
    confidence: float  # 0.0 - 1.0
    candles_used: int
    description: str
    signal_for_short: str  # "ENTRY", "EXIT", "WAIT", "NEUTRAL"
    score_adjustment: float  # Points to add/subtract from TA score

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "pattern_name": self.pattern_name,
            "pattern_type": self.pattern_type.value,
            "strength": self.strength.name,
            "strength_value": self.strength.value,
            "confidence": self.confidence,
            "candles_used": self.candles_used,
            "description": self.description,
            "signal_for_short": self.signal_for_short,
            "score_adjustment": self.score_adjustment
        }


class CandlestickDetector:
    """
    Detects candlestick patterns for TGE execution timing.

    Optimized for SHORT thesis:
    - BEARISH patterns -> Entry signals (PRIMARY FOCUS)
    - BULLISH patterns -> Exit/take-profit signals
    - NEUTRAL patterns -> Wait for confirmation

    Pattern reliability hierarchy:
    1. 3-candle patterns (Star patterns) - STRONGEST
    2. 2-candle patterns (Engulfing) - MODERATE
    3. 1-candle patterns (Doji, Hammer, etc.) - WEAKEST

    Context matters:
    - Same shape pattern means OPPOSITE things based on trend position
    - Hammer at bottom = Bullish, same shape at top = Hanging Man (Bearish)
    """

    # Thresholds (calibrated for crypto volatility)
    BODY_THRESHOLD = 0.003    # 0.3% minimum body for "significant" candle
    DOJI_THRESHOLD = 0.10     # Body < 10% of range = Doji
    SMALL_BODY_THRESHOLD = 0.30  # Body < 30% of range = small body
    WICK_RATIO_MIN = 2.0      # Wick must be 2x body for hammer/shooting star
    ENGULF_MIN_RATIO = 1.1    # Engulfing candle must be 10% larger

    def __init__(self, timeframe: str = "4h"):
        """
        Initialize detector with timeframe context.

        Args:
            timeframe: Chart timeframe for pattern detection.
                       "4h" recommended for TGE execution timing (per L014).
                       "1h" for more granular signals.
        """
        self.timeframe = timeframe

    def detect_patterns(
        self,
        ohlcv_data: List[List],
        lookback: int = 10
    ) -> List[PatternResult]:
        """
        Detect all candlestick patterns in recent price data.

        Args:
            ohlcv_data: List of [timestamp, open, high, low, close, volume]
            lookback: Number of recent candles to analyze (default: 10)

        Returns:
            List of detected patterns, sorted by strength (strongest first)
        """
        if not ohlcv_data or len(ohlcv_data) < 3:
            logger.debug("Insufficient OHLCV data for pattern detection")
            return []

        # Convert to CandleData objects
        candles = []
        for c in ohlcv_data[-lookback:]:
            try:
                candles.append(CandleData(
                    timestamp=float(c[0]),
                    open=float(c[1]),
                    high=float(c[2]),
                    low=float(c[3]),
                    close=float(c[4]),
                    volume=float(c[5]) if len(c) > 5 else 0.0
                ))
            except (IndexError, ValueError, TypeError) as e:
                logger.debug(f"Skipping malformed candle: {e}")
                continue

        if len(candles) < 3:
            return []

        patterns = []

        # Check 3-candle patterns first (most reliable)
        patterns.extend(self._detect_star_patterns(candles))

        # Check 2-candle patterns
        patterns.extend(self._detect_engulfing_patterns(candles))

        # Check 1-candle patterns (on most recent candle)
        patterns.extend(self._detect_single_candle_patterns(candles))

        # Sort by strength and confidence (strongest first)
        patterns.sort(
            key=lambda p: (p.strength.value, p.confidence),
            reverse=True
        )

        if patterns:
            logger.info(f"[CANDLESTICK] Detected {len(patterns)} pattern(s): "
                       f"{[p.pattern_name for p in patterns]}")

        return patterns

    def get_short_signal(
        self,
        ohlcv_data: List[List],
        context: str = "at_resistance"
    ) -> Dict[str, Any]:
        """
        Get aggregated SHORT signal from all detected patterns.

        This is the PRIMARY method for TGE execution timing.

        Args:
            ohlcv_data: OHLCV data for pattern detection
            context: Price context for signal strength:
                     - "at_resistance": Price at or near resistance (strongest bearish)
                     - "at_support": Price at or near support (weakest bearish)
                     - "mid_trend": Price in middle of range (neutral context)

        Returns:
            Dict with:
                signal: "STRONG_BEARISH", "MODERATE_BEARISH", "NEUTRAL",
                        "MODERATE_BULLISH", "STRONG_BULLISH", "NO_PATTERN"
                confidence: 0.0 - 1.0
                patterns_detected: List of pattern names
                recommendation: "ENTER_SHORT", "PREPARE_SHORT", "WAIT",
                               "REDUCE_SHORT", "EXIT_SHORT"
                score_adjustment: Points to add/subtract from TA score
        """
        patterns = self.detect_patterns(ohlcv_data)

        if not patterns:
            return {
                "signal": "NO_PATTERN",
                "confidence": 0.0,
                "patterns_detected": [],
                "bearish_score": 0.0,
                "bullish_score": 0.0,
                "net_score": 0.0,
                "recommendation": "WAIT",
                "score_adjustment": 0.0,
                "context": context,
                "timeframe": self.timeframe,
                "patterns_detail": []
            }

        # Aggregate bearish vs bullish signals
        bearish_score = 0.0
        bullish_score = 0.0
        detected_names = []
        patterns_detail = []

        for p in patterns:
            detected_names.append(p.pattern_name)
            patterns_detail.append(p.to_dict())

            if p.pattern_type == PatternType.BEARISH_REVERSAL:
                bearish_score += p.confidence * p.strength.value
            elif p.pattern_type == PatternType.BULLISH_REVERSAL:
                bullish_score += p.confidence * p.strength.value
            # NEUTRAL patterns contribute 0 to either side

        # Context multiplier - patterns at appropriate levels are stronger
        context_mult = {
            "at_resistance": 1.2,   # Bearish patterns at resistance = stronger
            "at_support": 0.8,      # Bearish patterns at support = weaker
            "mid_trend": 1.0
        }.get(context, 1.0)

        # Apply context to bearish score (good context amplifies bearish)
        # Inverse for bullish (good bearish context = bad for bullish)
        bearish_score *= context_mult
        bullish_score *= (2.0 - context_mult)  # Inverse effect

        net_score = bearish_score - bullish_score

        # Determine signal and recommendation
        if net_score >= 1.5:
            signal = "STRONG_BEARISH"
            recommendation = "ENTER_SHORT"
            score_adj = 1.5
        elif net_score >= 0.75:
            signal = "MODERATE_BEARISH"
            recommendation = "PREPARE_SHORT"
            score_adj = 0.75
        elif net_score <= -1.5:
            signal = "STRONG_BULLISH"
            recommendation = "EXIT_SHORT"
            score_adj = -1.5
        elif net_score <= -0.75:
            signal = "MODERATE_BULLISH"
            recommendation = "REDUCE_SHORT"
            score_adj = -0.75
        else:
            signal = "NEUTRAL"
            recommendation = "WAIT"
            score_adj = 0.0

        return {
            "signal": signal,
            "confidence": min(abs(net_score) / 2.0, 1.0),  # Normalize to 0-1
            "patterns_detected": detected_names,
            "bearish_score": round(bearish_score, 3),
            "bullish_score": round(bullish_score, 3),
            "net_score": round(net_score, 3),
            "recommendation": recommendation,
            "score_adjustment": score_adj,
            "context": context,
            "timeframe": self.timeframe,
            "patterns_detail": patterns_detail
        }

    # ==================== 3-CANDLE PATTERNS (STRONGEST) ====================

    def _detect_star_patterns(self, candles: List[CandleData]) -> List[PatternResult]:
        """
        Detect Morning Star, Evening Star, and their Doji variants.

        These are the MOST RELIABLE reversal patterns (3 candles).

        Evening Star (Bearish):
            1. Large bullish candle (strong buying)
            2. Small neutral candle (uncertainty, gap up ideally)
            3. Large bearish candle closing into body of candle 1

        Morning Star (Bullish):
            1. Large bearish candle (strong selling)
            2. Small neutral candle (uncertainty, gap down ideally)
            3. Large bullish candle closing into body of candle 1
        """
        patterns = []

        if len(candles) < 3:
            return patterns

        # Check last 5 potential 3-candle windows
        for i in range(max(2, len(candles) - 5), len(candles)):
            c1, c2, c3 = candles[i-2], candles[i-1], candles[i]

            # Evening Star (Bearish) - Primary focus for shorts
            if self._is_evening_star(c1, c2, c3):
                is_doji = self._is_doji(c2)
                patterns.append(PatternResult(
                    pattern_name="evening_doji_star" if is_doji else "evening_star",
                    pattern_type=PatternType.BEARISH_REVERSAL,
                    strength=PatternStrength.STRONG,
                    confidence=0.95 if is_doji else 0.85,
                    candles_used=3,
                    description="Strong bearish reversal: buying exhaustion + rejection + selling takeover",
                    signal_for_short="ENTRY",
                    score_adjustment=1.5 if is_doji else 1.25
                ))

            # Morning Star (Bullish) - Exit signal for shorts
            if self._is_morning_star(c1, c2, c3):
                is_doji = self._is_doji(c2)
                patterns.append(PatternResult(
                    pattern_name="morning_doji_star" if is_doji else "morning_star",
                    pattern_type=PatternType.BULLISH_REVERSAL,
                    strength=PatternStrength.STRONG,
                    confidence=0.95 if is_doji else 0.85,
                    candles_used=3,
                    description="Strong bullish reversal: selling exhaustion + uncertainty + buying takeover",
                    signal_for_short="EXIT",
                    score_adjustment=-1.5 if is_doji else -1.25
                ))

        return patterns

    def _is_evening_star(self, c1: CandleData, c2: CandleData, c3: CandleData) -> bool:
        """
        Evening Star detection:
        1. c1: Large bullish candle (body > 50% of range)
        2. c2: Small candle (body < 30% of c1's body)
        3. c3: Large bearish candle closing below midpoint of c1
        """
        if c1.total_range == 0 or c3.total_range == 0:
            return False

        c1_body_pct = c1.body_pct
        c3_body_pct = c3.body_pct

        # c1: Must be significantly bullish
        if not c1.is_bullish or c1_body_pct < 0.5:
            return False

        # c2: Must be small (uncertainty)
        if c1.body > 0 and c2.body >= c1.body * 0.3:
            return False

        # c3: Must be significantly bearish
        if not c3.is_bearish or c3_body_pct < 0.5:
            return False

        # c3 must close into c1's body (below midpoint)
        c1_midpoint = (c1.open + c1.close) / 2
        if c3.close > c1_midpoint:
            return False

        return True

    def _is_morning_star(self, c1: CandleData, c2: CandleData, c3: CandleData) -> bool:
        """
        Morning Star detection (mirror of Evening Star):
        1. c1: Large bearish candle (body > 50% of range)
        2. c2: Small candle (body < 30% of c1's body)
        3. c3: Large bullish candle closing above midpoint of c1
        """
        if c1.total_range == 0 or c3.total_range == 0:
            return False

        c1_body_pct = c1.body_pct
        c3_body_pct = c3.body_pct

        # c1: Must be significantly bearish
        if not c1.is_bearish or c1_body_pct < 0.5:
            return False

        # c2: Must be small (uncertainty)
        if c1.body > 0 and c2.body >= c1.body * 0.3:
            return False

        # c3: Must be significantly bullish
        if not c3.is_bullish or c3_body_pct < 0.5:
            return False

        # c3 must close into c1's body (above midpoint)
        c1_midpoint = (c1.open + c1.close) / 2
        if c3.close < c1_midpoint:
            return False

        return True

    # ==================== 2-CANDLE PATTERNS (MODERATE) ====================

    def _detect_engulfing_patterns(self, candles: List[CandleData]) -> List[PatternResult]:
        """
        Detect Bullish and Bearish Engulfing patterns.

        Bearish Engulfing:
            Small green candle followed by larger red candle that
            completely "engulfs" (contains) the first candle's body.
            Opens above prior close, closes below prior open.

        Bullish Engulfing:
            Small red candle followed by larger green candle that
            completely engulfs the first candle's body.
        """
        patterns = []

        if len(candles) < 2:
            return patterns

        # Check last 3 potential 2-candle windows
        for i in range(max(1, len(candles) - 3), len(candles)):
            c1, c2 = candles[i-1], candles[i]

            # Bearish Engulfing - Primary focus for shorts
            if self._is_bearish_engulfing(c1, c2):
                patterns.append(PatternResult(
                    pattern_name="bearish_engulfing",
                    pattern_type=PatternType.BEARISH_REVERSAL,
                    strength=PatternStrength.MODERATE,
                    confidence=0.80,
                    candles_used=2,
                    description="Bearish momentum overtakes bullish - strong selling pressure",
                    signal_for_short="ENTRY",
                    score_adjustment=1.0
                ))

            # Bullish Engulfing - Exit signal for shorts
            if self._is_bullish_engulfing(c1, c2):
                patterns.append(PatternResult(
                    pattern_name="bullish_engulfing",
                    pattern_type=PatternType.BULLISH_REVERSAL,
                    strength=PatternStrength.MODERATE,
                    confidence=0.80,
                    candles_used=2,
                    description="Bullish momentum overtakes bearish - strong buying pressure",
                    signal_for_short="EXIT",
                    score_adjustment=-1.0
                ))

        return patterns

    def _is_bearish_engulfing(self, c1: CandleData, c2: CandleData) -> bool:
        """
        Bearish Engulfing: Green candle followed by larger red that engulfs it.
        - c2 opens at or above c1's close
        - c2 closes at or below c1's open
        - c2's body is larger than c1's body
        """
        if c1.body == 0:
            return False

        return (
            c1.is_bullish and
            c2.is_bearish and
            c2.open >= c1.close * 0.998 and  # Small tolerance for gaps
            c2.close <= c1.open * 1.002 and
            c2.body >= c1.body * self.ENGULF_MIN_RATIO
        )

    def _is_bullish_engulfing(self, c1: CandleData, c2: CandleData) -> bool:
        """
        Bullish Engulfing: Red candle followed by larger green that engulfs it.
        - c2 opens at or below c1's close
        - c2 closes at or above c1's open
        - c2's body is larger than c1's body
        """
        if c1.body == 0:
            return False

        return (
            c1.is_bearish and
            c2.is_bullish and
            c2.open <= c1.close * 1.002 and  # Small tolerance
            c2.close >= c1.open * 0.998 and
            c2.body >= c1.body * self.ENGULF_MIN_RATIO
        )

    # ==================== 1-CANDLE PATTERNS (WEAKEST) ====================

    def _detect_single_candle_patterns(self, candles: List[CandleData]) -> List[PatternResult]:
        """
        Detect single-candle patterns on the most recent candle.

        IMPORTANT: Single candle patterns require CONTEXT:
        - Hammer shape at BOTTOM of downtrend = Bullish (Hammer)
        - Hammer shape at TOP of uptrend = Bearish (Hanging Man)
        - Same with Shooting Star vs Inverted Hammer
        """
        patterns = []

        if len(candles) < 1:
            return patterns

        # Analyze only the MOST RECENT candle
        c = candles[-1]

        # Need prior trend context for interpretation
        prior_trend = self._get_prior_trend(candles[:-1]) if len(candles) > 1 else "unknown"

        # ===== DOJI FAMILY =====

        # Standard Doji (Neutral)
        if self._is_doji(c) and not self._is_gravestone_doji(c) and not self._is_dragonfly_doji(c):
            patterns.append(PatternResult(
                pattern_name="doji",
                pattern_type=PatternType.NEUTRAL,
                strength=PatternStrength.WEAK,
                confidence=0.50,
                candles_used=1,
                description="Market uncertainty - open equals close, watch for follow-through",
                signal_for_short="WAIT",
                score_adjustment=0.0
            ))

        # Gravestone Doji (Bearish)
        if self._is_gravestone_doji(c):
            patterns.append(PatternResult(
                pattern_name="gravestone_doji",
                pattern_type=PatternType.BEARISH_REVERSAL,
                strength=PatternStrength.WEAK,
                confidence=0.65,
                candles_used=1,
                description="Complete rejection of buying pressure - inverted T shape",
                signal_for_short="ENTRY",
                score_adjustment=0.5
            ))

        # Dragonfly Doji (Bullish)
        if self._is_dragonfly_doji(c):
            patterns.append(PatternResult(
                pattern_name="dragonfly_doji",
                pattern_type=PatternType.BULLISH_REVERSAL,
                strength=PatternStrength.WEAK,
                confidence=0.65,
                candles_used=1,
                description="Complete rejection of selling pressure - T shape",
                signal_for_short="EXIT",
                score_adjustment=-0.5
            ))

        # ===== HAMMER FAMILY (context-dependent) =====

        # Shooting Star (Bearish) - requires prior uptrend
        if self._is_shooting_star(c) and prior_trend == "uptrend":
            patterns.append(PatternResult(
                pattern_name="shooting_star",
                pattern_type=PatternType.BEARISH_REVERSAL,
                strength=PatternStrength.WEAK,
                confidence=0.70,
                candles_used=1,
                description="Resistance rejection after uptrend - long upper wick, small body",
                signal_for_short="ENTRY",
                score_adjustment=0.75
            ))

        # Hammer (Bullish) - requires prior downtrend
        if self._is_hammer(c) and prior_trend == "downtrend":
            patterns.append(PatternResult(
                pattern_name="hammer",
                pattern_type=PatternType.BULLISH_REVERSAL,
                strength=PatternStrength.WEAK,
                confidence=0.70,
                candles_used=1,
                description="Support bounce after downtrend - long lower wick, small body at top",
                signal_for_short="EXIT",
                score_adjustment=-0.75
            ))

        # Hanging Man (Bearish) - hammer shape at TOP of uptrend
        if self._is_hammer(c) and prior_trend == "uptrend":
            patterns.append(PatternResult(
                pattern_name="hanging_man",
                pattern_type=PatternType.BEARISH_REVERSAL,
                strength=PatternStrength.WEAK,
                confidence=0.55,
                candles_used=1,
                description="Potential trend exhaustion - hammer shape at top of move",
                signal_for_short="PREPARE_SHORT",
                score_adjustment=0.25
            ))

        # Inverted Hammer (Bullish) - shooting star shape at BOTTOM of downtrend
        if self._is_shooting_star(c) and prior_trend == "downtrend":
            patterns.append(PatternResult(
                pattern_name="inverted_hammer",
                pattern_type=PatternType.BULLISH_REVERSAL,
                strength=PatternStrength.WEAK,
                confidence=0.55,
                candles_used=1,
                description="New buying pressure emerging - buyers attempted rally",
                signal_for_short="REDUCE_SHORT",
                score_adjustment=-0.25
            ))

        # Spinning Top (Neutral) - small body, large equal wicks
        if self._is_spinning_top(c):
            patterns.append(PatternResult(
                pattern_name="spinning_top",
                pattern_type=PatternType.NEUTRAL,
                strength=PatternStrength.WEAK,
                confidence=0.45,
                candles_used=1,
                description="Neither buyers nor sellers dominate - momentum loss signal",
                signal_for_short="WAIT",
                score_adjustment=0.0
            ))

        return patterns

    # ==================== PATTERN DETECTION HELPERS ====================

    def _is_doji(self, c: CandleData) -> bool:
        """
        Doji: Very small body (open approximately equals close).
        Body is less than 10% of total candle range.
        """
        if c.total_range == 0:
            return False
        return c.body_pct < self.DOJI_THRESHOLD

    def _is_gravestone_doji(self, c: CandleData) -> bool:
        """
        Gravestone Doji: Inverted T shape.
        - Long upper wick (>60% of range)
        - Tiny or no body (<10% of range)
        - Tiny or no lower wick (<10% of range)
        """
        if c.total_range == 0:
            return False
        return (
            c.body_pct < self.DOJI_THRESHOLD and
            c.upper_wick > c.total_range * 0.6 and
            c.lower_wick < c.total_range * 0.1
        )

    def _is_dragonfly_doji(self, c: CandleData) -> bool:
        """
        Dragonfly Doji: T shape (opposite of Gravestone).
        - Long lower wick (>60% of range)
        - Tiny or no body (<10% of range)
        - Tiny or no upper wick (<10% of range)
        """
        if c.total_range == 0:
            return False
        return (
            c.body_pct < self.DOJI_THRESHOLD and
            c.lower_wick > c.total_range * 0.6 and
            c.upper_wick < c.total_range * 0.1
        )

    def _is_shooting_star(self, c: CandleData) -> bool:
        """
        Shooting Star: Small body at bottom, long upper wick.
        - Small body (<30% of range)
        - Upper wick >= 2x body size
        - Lower wick < body size
        """
        if c.total_range == 0 or c.body == 0:
            return False
        return (
            c.body_pct < self.SMALL_BODY_THRESHOLD and
            c.upper_wick >= c.body * self.WICK_RATIO_MIN and
            c.lower_wick < c.body
        )

    def _is_hammer(self, c: CandleData) -> bool:
        """
        Hammer: Small body at top, long lower wick.
        - Small body (<30% of range)
        - Lower wick >= 2x body size
        - Upper wick < body size
        """
        if c.total_range == 0 or c.body == 0:
            return False
        return (
            c.body_pct < self.SMALL_BODY_THRESHOLD and
            c.lower_wick >= c.body * self.WICK_RATIO_MIN and
            c.upper_wick < c.body
        )

    def _is_spinning_top(self, c: CandleData) -> bool:
        """
        Spinning Top: Small body with roughly equal upper and lower wicks.
        - Small body (<30% of range)
        - Both wicks > body
        - Wicks approximately equal (difference < body size)
        """
        if c.total_range == 0:
            return False
        return (
            c.body_pct < self.SMALL_BODY_THRESHOLD and
            c.upper_wick > c.body and
            c.lower_wick > c.body and
            abs(c.upper_wick - c.lower_wick) < c.body
        )

    def _get_prior_trend(self, candles: List[CandleData], lookback: int = 3) -> str:
        """
        Determine prior trend direction from recent candles.

        Simple heuristic: Compare closes over lookback period.
        +2% change = uptrend, -2% change = downtrend, else sideways.

        Returns: "uptrend", "downtrend", or "sideways"
        """
        if len(candles) < lookback:
            return "unknown"

        recent = candles[-lookback:]
        if len(recent) < 2:
            return "unknown"

        first_close = recent[0].close
        last_close = recent[-1].close

        if first_close == 0:
            return "unknown"

        change_pct = ((last_close - first_close) / first_close) * 100

        if change_pct > 2.0:
            return "uptrend"
        elif change_pct < -2.0:
            return "downtrend"
        else:
            return "sideways"


# ==================== CLI / TEST ====================

def main():
    """CLI test for candlestick detector."""
    import sys
    import json
    from datetime import datetime

    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )

    print(f"\n{'='*80}")
    print("CANDLESTICK PATTERN DETECTOR - Learning 022")
    print(f"{'='*80}")

    # Test with sample OHLCV data (Evening Star pattern)
    test_ohlcv = [
        # timestamp, open, high, low, close, volume
        [1702800000000, 1.00, 1.05, 0.98, 1.04, 1000000],  # Bullish
        [1702814400000, 1.04, 1.08, 1.00, 1.06, 1200000],  # Bullish
        [1702828800000, 1.06, 1.10, 1.04, 1.08, 1500000],  # Bullish (c1)
        [1702843200000, 1.08, 1.09, 1.07, 1.075, 500000],  # Small (c2 - doji-ish)
        [1702857600000, 1.08, 1.08, 1.02, 1.03, 2000000],  # Bearish (c3)
    ]

    detector = CandlestickDetector(timeframe="4h")

    print("\nTest OHLCV Data (Evening Star pattern):")
    for i, c in enumerate(test_ohlcv):
        direction = "GREEN" if c[4] > c[1] else "RED" if c[4] < c[1] else "FLAT"
        print(f"  Candle {i+1}: O={c[1]:.3f} H={c[2]:.3f} L={c[3]:.3f} C={c[4]:.3f} [{direction}]")

    # Detect patterns
    patterns = detector.detect_patterns(test_ohlcv)

    print(f"\n{'='*80}")
    print("DETECTED PATTERNS")
    print(f"{'='*80}")

    if patterns:
        for p in patterns:
            print(f"\n  Pattern: {p.pattern_name.upper()}")
            print(f"  Type: {p.pattern_type.value}")
            print(f"  Strength: {p.strength.name} ({p.strength.value})")
            print(f"  Confidence: {p.confidence:.0%}")
            print(f"  Candles: {p.candles_used}")
            print(f"  Signal for Short: {p.signal_for_short}")
            print(f"  Score Adjustment: {p.score_adjustment:+.2f}")
            print(f"  Description: {p.description}")
    else:
        print("  No patterns detected")

    # Get aggregated signal
    signal = detector.get_short_signal(test_ohlcv, context="at_resistance")

    print(f"\n{'='*80}")
    print("AGGREGATED SHORT SIGNAL")
    print(f"{'='*80}")
    print(f"  Signal: {signal['signal']}")
    print(f"  Confidence: {signal['confidence']:.0%}")
    print(f"  Recommendation: {signal['recommendation']}")
    print(f"  Score Adjustment: {signal['score_adjustment']:+.2f}")
    print(f"  Bearish Score: {signal['bearish_score']:.3f}")
    print(f"  Bullish Score: {signal['bullish_score']:.3f}")
    print(f"  Net Score: {signal['net_score']:.3f}")
    print(f"  Context: {signal['context']}")
    print(f"  Timeframe: {signal['timeframe']}")

    print(f"\n{'='*80}\n")

    # Save output
    output_file = f"candlestick_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "patterns": [p.to_dict() for p in patterns],
            "signal": signal
        }, f, indent=2)
    print(f"Saved to: {output_file}\n")


if __name__ == "__main__":
    main()
