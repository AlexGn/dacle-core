#!/usr/bin/env python3
"""
Automated Confluence Counter - Phase 1 Session 268 / Phase C Session 280

Counts technical confluence factors from multiple indicators to classify
setup quality as SINGLE/DOUBLE/TRIPLE/QUAD.

Purpose:
    Replace hardcoded confluence_count=4 default with automated scoring
    based on actual technical alignment across 11 confluence types.

Integration:
    - Pipeline: integrated_pipeline.py (replaces hardcoded count)
    - Alerts: alert_generator.py (displays confluence breakdown)
    - Sherlock Risk: Used in position sizing calculations

Session 280 F5: Added Sherlock chart pattern integration
    - Integrated CandlestickDetector for automatic pattern detection
    - Added CHART_PATTERN confluence type for detected candlestick patterns
    - Patterns: DOJI, Shooting Star, Engulfing, Evening Star, etc.

Author: Claude Code (Session 268 Phase 1, Session 280 Phase C)
Date: 2026-01-01, Updated: 2026-01-03
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Session 280 F5: Import CandlestickDetector for Sherlock patterns
try:
    from src.analysis.technical_patterns import CandlestickDetector, PatternResult, PatternType
    CANDLESTICK_DETECTOR_AVAILABLE = True
except ImportError:
    CANDLESTICK_DETECTOR_AVAILABLE = False
    CandlestickDetector = None
    PatternResult = None
    PatternType = None
    logger.debug("CandlestickDetector not available, chart pattern detection disabled")


class ConfluenceType(Enum):
    """Confluence factor types with Sherlock learning references."""

    EMA_ALIGNMENT = "ema_alignment"  # L046: 12+24 EMA aligned bearish
    EMA_200_POSITION = "ema_200_position"  # L047: Price below 200 EMA
    QVWAP_RETEST = "qvwap_retest"  # L038: Price at QVWAP level
    YVWAP_SUPPORT = "yvwap_support"  # Price at YVWAP level
    SUPPORT_RETEST = "support_retest"  # L029: S/R level retest
    RESISTANCE_RETEST = "resistance_retest"  # L029: Resistance retest
    TRENDLINE_BREAK = "trendline_break"  # Trendline breakout
    PATTERN_DETECTED = "pattern_detected"  # L059: Chart pattern (legacy - basic detection)
    VOLUME_SPIKE = "volume_spike"  # Volume >1.5x average
    FUNDING_RATE = "funding_rate"  # L051: Funding rate extreme
    TVEM_BAND = "tvem_band"  # L058: TVEM band confluence
    # Session 280 F5: Sherlock candlestick patterns
    CHART_PATTERN_STRONG = "chart_pattern_strong"  # L032: 3-candle patterns (Evening/Morning Star)
    CHART_PATTERN_MODERATE = "chart_pattern_moderate"  # 2-candle patterns (Engulfing)
    CHART_PATTERN_WEAK = "chart_pattern_weak"  # 1-candle patterns (Doji, Shooting Star)


@dataclass
class ConfluenceResult:
    """
    Confluence analysis result.

    Attributes:
        score: Numeric confluence count (1-4)
        rating: Text rating (SINGLE/DOUBLE/TRIPLE/QUAD)
        factors: List of active confluence types
        factor_descriptions: Human-readable factor descriptions
        conviction_modifier: Conviction score adjustment (±0.5 to ±1.0)
    """

    score: int  # 1=SINGLE, 2=DOUBLE, 3=TRIPLE, 4=QUAD
    rating: str  # "SINGLE", "DOUBLE", "TRIPLE", "QUAD"
    factors: List[ConfluenceType]  # Active confluence factors
    factor_descriptions: List[str]  # Human-readable descriptions
    conviction_modifier: float  # ±0.5 to ±1.5 adjustment for conviction


class ConfluenceCounter:
    """
    Automated confluence scoring from TA features.

    Counts active confluence factors across 9 technical categories:
    1. EMA Alignment (12+24 EMA bearish/bullish)
    2. EMA 200 Position (above/below 200 EMA)
    3. QVWAP Retest (price within 1% of QVWAP)
    4. YVWAP Support (price within 1% of YVWAP)
    5. Support/Resistance Retest (price near S/R level)
    6. Chart Patterns (inverse H&S, cup & handle, etc.)
    7. Volume Spike (>1.5x average volume)
    8. Funding Rate (extreme negative for SHORT)
    9. TVEM Band (bearish signal from TVEM indicator)

    Example:
        counter = ConfluenceCounter()
        result = counter.count_confluence(
            ema_data={"dual_ema": {"alignment": "bearish"}},
            vwap_data={"qvwap_distance_pct": 0.5},
            sr_levels={"near_resistance": True},
            patterns=["inverse_h_and_s"],
            volume_data={"volume_spike": True},
            funding_rate=-0.08
        )

        # Output: score=4, rating="QUAD", conviction_modifier=+1.0
    """

    # Thresholds
    QVWAP_DISTANCE_THRESHOLD = 1.0  # <1% from QVWAP = confluence
    YVWAP_DISTANCE_THRESHOLD = 1.0  # <1% from YVWAP = confluence
    FUNDING_RATE_EXTREME = -0.05  # <-0.05% = extremely negative (squeeze risk)
    VOLUME_SPIKE_THRESHOLD = 1.5  # >1.5x average volume

    def count_confluence(
        self,
        ema_data: Dict,
        vwap_data: Dict,
        sr_levels: Dict,
        patterns: List[str],
        volume_data: Dict,
        funding_rate: Optional[float] = None,
        tvem_data: Optional[Dict] = None,
        ohlcv_data: Optional[List[List]] = None,
        timeframe: str = "4h",
    ) -> ConfluenceResult:
        """
        Count active confluence factors.

        Args:
            ema_data: EMA analysis (12/24/200)
                - dual_ema.alignment: "bearish"/"bullish"/"choppy"
                - mtf_ema_200.position_vs_ema: "below"/"above"
            vwap_data: VWAP data (Q/Y VWAP)
                - qvwap_distance_pct: Distance from QVWAP (%)
                - yvwap_distance_pct: Distance from YVWAP (%)
            sr_levels: Support/resistance levels
                - near_resistance: True/False
                - near_support: True/False
                - retest_number: 1, 2, 3+
            patterns: Detected chart patterns (e.g., ["inverse_h_and_s"]) - legacy
            volume_data: Volume analysis
                - volume_spike: True/False
            funding_rate: Funding rate (optional, for SHORT trades)
            tvem_data: TVEM band data (optional)
                - signal: "bearish"/"bullish"
            ohlcv_data: OHLCV candlestick data for automatic pattern detection
                - List of [timestamp, open, high, low, close, volume]
                - Session 280 F5: Enables Sherlock pattern detection
            timeframe: Chart timeframe for pattern detection (default "4h")

        Returns:
            ConfluenceResult with score 1-4, rating, factors, and conviction modifier
        """
        factors = []
        descriptions = []

        # 1. EMA Alignment (L046: 1D 24 EMA)
        if ema_data.get("dual_ema", {}).get("alignment") == "bearish":
            factors.append(ConfluenceType.EMA_ALIGNMENT)
            descriptions.append("12+24 EMA bearish alignment")

        # 2. EMA 200 Position (L047: MTF 200 EMA)
        if ema_data.get("mtf_ema_200", {}).get("position_vs_ema") == "below":
            factors.append(ConfluenceType.EMA_200_POSITION)
            descriptions.append("Price below 200 EMA")

        # 3. QVWAP Retest (L038)
        qvwap_distance = abs(vwap_data.get("qvwap_distance_pct", 100))
        if qvwap_distance < self.QVWAP_DISTANCE_THRESHOLD:
            factors.append(ConfluenceType.QVWAP_RETEST)
            descriptions.append(f"QVWAP retest ({qvwap_distance:.1f}% away)")

        # 4. YVWAP Support
        yvwap_distance = abs(vwap_data.get("yvwap_distance_pct", 100))
        if yvwap_distance < self.YVWAP_DISTANCE_THRESHOLD:
            factors.append(ConfluenceType.YVWAP_SUPPORT)
            descriptions.append(f"YVWAP support ({yvwap_distance:.1f}% away)")

        # 5. S/R Retest (L029)
        if sr_levels.get("near_resistance", False):
            factors.append(ConfluenceType.RESISTANCE_RETEST)
            retest_num = sr_levels.get("retest_number", 1)
            descriptions.append(f"Resistance retest #{retest_num}")
        elif sr_levels.get("near_support", False):
            factors.append(ConfluenceType.SUPPORT_RETEST)
            retest_num = sr_levels.get("retest_number", 1)
            descriptions.append(f"Support retest #{retest_num}")

        # 6. Chart Pattern (L059)
        if patterns:
            factors.append(ConfluenceType.PATTERN_DETECTED)
            pattern_names = ", ".join([p.replace("_", " ").title() for p in patterns])
            descriptions.append(f"Pattern: {pattern_names}")

        # 7. Volume Spike
        if volume_data.get("volume_spike", False):
            factors.append(ConfluenceType.VOLUME_SPIKE)
            volume_ratio = volume_data.get("volume_ratio", 0)
            descriptions.append(f"Volume spike ({volume_ratio:.1f}x avg)")

        # 8. Funding Rate (L051)
        if funding_rate is not None and funding_rate < self.FUNDING_RATE_EXTREME:
            factors.append(ConfluenceType.FUNDING_RATE)
            descriptions.append(f"Extreme funding rate: {funding_rate:.3f}%")

        # 9. TVEM Band (L058)
        if tvem_data and tvem_data.get("signal") == "bearish":
            factors.append(ConfluenceType.TVEM_BAND)
            descriptions.append("TVEM band bearish signal")

        # 10. Session 280 F5: Sherlock Chart Pattern Detection
        # Uses CandlestickDetector for automatic pattern recognition
        if ohlcv_data and CANDLESTICK_DETECTOR_AVAILABLE:
            detected_patterns = self._detect_sherlock_patterns(
                ohlcv_data, timeframe, sr_levels
            )
            for pattern_type, pattern_info in detected_patterns:
                factors.append(pattern_type)
                descriptions.append(pattern_info["description"])

        # Calculate score (1-4, capped at QUAD)
        count = len(factors)
        if count >= 4:
            score, rating = 4, "QUAD"
        elif count == 3:
            score, rating = 3, "TRIPLE"
        elif count == 2:
            score, rating = 2, "DOUBLE"
        else:
            score, rating = 1, "SINGLE"

        # Conviction modifier (L059 implicit weighting)
        # QUAD boosts conviction, SINGLE reduces it
        conviction_modifier_map = {
            1: -0.5,  # SINGLE: reduce conviction
            2: 0.0,  # DOUBLE: neutral
            3: +0.5,  # TRIPLE: boost conviction
            4: +1.0,  # QUAD: strong boost
        }
        conviction_modifier = conviction_modifier_map[score]

        # Log result
        logger.info(
            f"🎯 Confluence: {rating} ({score} factors) | "
            f"Conviction modifier: {conviction_modifier:+.1f}"
        )
        for desc in descriptions:
            logger.info(f"   ✓ {desc}")

        return ConfluenceResult(
            score=score,
            rating=rating,
            factors=factors,
            factor_descriptions=descriptions,
            conviction_modifier=conviction_modifier,
        )

    def _detect_sherlock_patterns(
        self,
        ohlcv_data: List[List],
        timeframe: str,
        sr_levels: Dict,
    ) -> List[tuple]:
        """
        Session 280 F5: Detect Sherlock candlestick patterns.

        Uses CandlestickDetector to identify reversal patterns:
        - STRONG (3-candle): Evening Star, Morning Star (L032)
        - MODERATE (2-candle): Bearish/Bullish Engulfing
        - WEAK (1-candle): Doji, Shooting Star, Hammer

        Args:
            ohlcv_data: OHLCV candlestick data
            timeframe: Chart timeframe (e.g., "4h")
            sr_levels: S/R context for pattern strength

        Returns:
            List of (ConfluenceType, pattern_info) tuples
        """
        if not CANDLESTICK_DETECTOR_AVAILABLE:
            return []

        detected = []

        try:
            # Initialize detector
            detector = CandlestickDetector(timeframe=timeframe)

            # Determine context for pattern strength
            context = "mid_trend"
            if sr_levels.get("near_resistance", False):
                context = "at_resistance"
            elif sr_levels.get("near_support", False):
                context = "at_support"

            # Get short signal with all detected patterns
            signal = detector.get_short_signal(ohlcv_data, context=context)

            if signal["signal"] == "NO_PATTERN":
                return []

            # Process detected patterns
            patterns_detail = signal.get("patterns_detail", [])
            bearish_patterns = []
            bullish_patterns = []

            for pattern in patterns_detail:
                pattern_name = pattern.get("pattern_name", "unknown")
                pattern_type_str = pattern.get("pattern_type", "neutral")
                strength = pattern.get("strength", "WEAK")
                confidence = pattern.get("confidence", 0.5)
                signal_for_short = pattern.get("signal_for_short", "WAIT")

                # Only count bearish patterns for SHORT confluence
                # (bullish patterns = warning, not confluence)
                if pattern_type_str == "bearish_reversal":
                    bearish_patterns.append({
                        "name": pattern_name,
                        "strength": strength,
                        "confidence": confidence,
                        "signal": signal_for_short,
                    })

            # Classify by strength and add as confluence
            # Only add the STRONGEST pattern as confluence (avoid double counting)
            if bearish_patterns:
                # Sort by strength: STRONG > MODERATE > WEAK
                strength_order = {"STRONG": 3, "MODERATE": 2, "WEAK": 1}
                bearish_patterns.sort(
                    key=lambda p: (strength_order.get(p["strength"], 0), p["confidence"]),
                    reverse=True
                )

                best_pattern = bearish_patterns[0]
                pattern_name_display = best_pattern["name"].replace("_", " ").title()

                if best_pattern["strength"] == "STRONG":
                    # 3-candle patterns: Evening Star, etc.
                    detected.append((
                        ConfluenceType.CHART_PATTERN_STRONG,
                        {
                            "description": f"⭐ {pattern_name_display} (3-candle reversal)",
                            "strength": "STRONG",
                            "confidence": best_pattern["confidence"],
                        }
                    ))
                    logger.info(
                        f"[PATTERN] STRONG bearish pattern: {pattern_name_display} "
                        f"(confidence: {best_pattern['confidence']:.0%})"
                    )
                elif best_pattern["strength"] == "MODERATE":
                    # 2-candle patterns: Engulfing
                    detected.append((
                        ConfluenceType.CHART_PATTERN_MODERATE,
                        {
                            "description": f"📊 {pattern_name_display} (2-candle reversal)",
                            "strength": "MODERATE",
                            "confidence": best_pattern["confidence"],
                        }
                    ))
                    logger.info(
                        f"[PATTERN] MODERATE bearish pattern: {pattern_name_display} "
                        f"(confidence: {best_pattern['confidence']:.0%})"
                    )
                else:
                    # 1-candle patterns: Doji, Shooting Star
                    detected.append((
                        ConfluenceType.CHART_PATTERN_WEAK,
                        {
                            "description": f"🕯️ {pattern_name_display} (1-candle signal)",
                            "strength": "WEAK",
                            "confidence": best_pattern["confidence"],
                        }
                    ))
                    logger.debug(
                        f"[PATTERN] WEAK bearish pattern: {pattern_name_display} "
                        f"(confidence: {best_pattern['confidence']:.0%})"
                    )

        except Exception as e:
            logger.warning(f"[PATTERN] Error detecting Sherlock patterns: {e}")

        return detected


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    counter = ConfluenceCounter()

    # Example 1: QUAD confluence (4 factors)
    print("=" * 70)
    print("TEST 1: QUAD Confluence (4 factors)")
    print("=" * 70)

    result = counter.count_confluence(
        ema_data={"dual_ema": {"alignment": "bearish"}},
        vwap_data={"qvwap_distance_pct": 0.5},  # <1% from QVWAP
        sr_levels={"near_resistance": True, "retest_number": 1},
        patterns=["inverse_h_and_s"],
        volume_data={"volume_spike": True, "volume_ratio": 2.3},
        funding_rate=-0.08,  # Extremely negative
    )

    print(f"\nScore: {result.score}/4")
    print(f"Rating: {result.rating}")
    print(f"Conviction Modifier: {result.conviction_modifier:+.1f}")
    print(f"Factors: {', '.join([f.value for f in result.factors])}")
    print()

    # Example 2: DOUBLE confluence (2 factors)
    print("=" * 70)
    print("TEST 2: DOUBLE Confluence (2 factors)")
    print("=" * 70)

    result = counter.count_confluence(
        ema_data={"dual_ema": {"alignment": "bearish"}},
        vwap_data={"qvwap_distance_pct": 5.0},  # >1% from QVWAP (no confluence)
        sr_levels={"near_resistance": False},
        patterns=[],
        volume_data={"volume_spike": False},
        tvem_data={"signal": "bearish"},
    )

    print(f"\nScore: {result.score}/4")
    print(f"Rating: {result.rating}")
    print(f"Conviction Modifier: {result.conviction_modifier:+.1f}")
    print()
