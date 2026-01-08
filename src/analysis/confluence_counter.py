#!/usr/bin/env python3
"""
Automated Confluence Counter - Phase 1 Session 268 / Phase C Session 280 / Session 278 LONG / Session 302

Counts technical confluence factors from multiple indicators to classify
setup quality as SINGLE/DOUBLE/TRIPLE/QUAD.

Purpose:
    Replace hardcoded confluence_count=4 default with automated scoring
    based on actual technical alignment across 13 confluence types.

Integration:
    - Pipeline: integrated_pipeline.py (replaces hardcoded count)
    - Alerts: alert_generator.py (displays confluence breakdown)
    - Sherlock Risk: Used in position sizing calculations
    - LONG Scorer: src/conviction/long_scorer.py (Session 278)

Session 280 F5: Added Sherlock chart pattern integration
    - Integrated CandlestickDetector for automatic pattern detection
    - Added CHART_PATTERN confluence type for detected candlestick patterns
    - Patterns: DOJI, Shooting Star, Engulfing, Evening Star, etc.

Session 278 LONG: Added direction-aware confluence counting
    - New `direction` parameter: "SHORT" (default) or "LONG"
    - For LONG: filters for bullish_reversal patterns instead of bearish_reversal
    - For LONG: EMA alignment looks for "bullish" instead of "bearish"
    - For LONG: SUPPORT_RETEST is positive confluence (vs RESISTANCE_RETEST for SHORT)
    - For LONG: Funding rate uses inverted thresholds (L077)

Session 302: David's MP-VWAP and MTF EMA Average indicators
    - Added MP_VWAP_ZONE (L085): Market Profile + VWAP confluence zones
    - Added MTF_EMA_AVG (L086): Multi-Timeframe EMA Average alignment
    - For SHORT: STRONG_BEARISH zone, ALL_BEARISH EMA = confluence
    - For LONG: STRONG_BULLISH zone, ALL_BULLISH EMA = confluence

Author: Claude Code (Session 268, 280, 278, 302)
Date: 2026-01-01, Updated: 2026-01-08
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
    # Session 302: David's indicators (L085, L086)
    MP_VWAP_ZONE = "mp_vwap_zone"  # L085: Price at POC/VAH/VAL confluence zone
    MTF_EMA_AVG = "mtf_ema_avg"  # L086: MTF EMA Average alignment


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

    Counts active confluence factors across 13 technical categories:
    1. EMA Alignment (12+24 EMA bearish/bullish)
    2. EMA 200 Position (above/below 200 EMA)
    3. QVWAP Retest (price within 1% of QVWAP)
    4. YVWAP Support (price within 1% of YVWAP)
    5. Support/Resistance Retest (price near S/R level)
    6. Chart Patterns (inverse H&S, cup & handle, etc.)
    7. Volume Spike (>1.5x average volume)
    8. Funding Rate (extreme negative for SHORT)
    9. TVEM Band (bearish signal from TVEM indicator)
    10. MP-VWAP Zone (L085 - Market Profile + VWAP confluence)
    11. MTF EMA Average (L086 - Multi-Timeframe smoothed EMA)

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
    FUNDING_RATE_EXTREME_SHORT = -0.05  # <-0.05% = squeeze risk for SHORTs (L051)
    FUNDING_RATE_EXTREME_LONG = 0.05  # >+0.05% = squeeze risk for LONGs (L077)
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
        direction: str = "SHORT",
        mp_vwap_data: Optional[Dict] = None,  # Session 302: L085 MP-VWAP
        mtf_ema_avg_data: Optional[Dict] = None,  # Session 302: L086 MTF EMA Average
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
            funding_rate: Funding rate (optional)
                - For SHORT: <-0.05% = squeeze risk (L051)
                - For LONG: >+0.05% = squeeze risk (L077)
            tvem_data: TVEM band data (optional)
                - signal: "bearish"/"bullish"
            ohlcv_data: OHLCV candlestick data for automatic pattern detection
                - List of [timestamp, open, high, low, close, volume]
                - Session 280 F5: Enables Sherlock pattern detection
            timeframe: Chart timeframe for pattern detection (default "4h")
            direction: Trade direction "SHORT" (default) or "LONG"
                - SHORT: looks for bearish EMA alignment, resistance retests, bearish patterns
                - LONG: looks for bullish EMA alignment, support retests, bullish patterns

        Returns:
            ConfluenceResult with score 1-4, rating, factors, and conviction modifier
        """
        factors = []
        descriptions = []

        # Normalize direction
        direction = direction.upper()
        is_long = direction == "LONG"

        # 1. EMA Alignment (L046: 1D 24 EMA)
        # For SHORT: bearish alignment = confluence
        # For LONG: bullish alignment = confluence (recovery confirmation)
        alignment = ema_data.get("dual_ema", {}).get("alignment")
        if is_long:
            if alignment == "bullish":
                factors.append(ConfluenceType.EMA_ALIGNMENT)
                descriptions.append("12+24 EMA bullish alignment (recovery)")
        else:
            if alignment == "bearish":
                factors.append(ConfluenceType.EMA_ALIGNMENT)
                descriptions.append("12+24 EMA bearish alignment")

        # 2. EMA 200 Position (L047: MTF 200 EMA)
        # For SHORT: below 200 EMA = downtrend (good)
        # For LONG: below 200 EMA = deep value (good), above = recovery confirmed
        position_vs_ema = ema_data.get("mtf_ema_200", {}).get("position_vs_ema")
        if is_long:
            # For LONG, we want to see price recovering toward or above 200 EMA
            if position_vs_ema == "above":
                factors.append(ConfluenceType.EMA_200_POSITION)
                descriptions.append("Price above 200 EMA (recovery confirmed)")
            elif position_vs_ema == "below":
                # Below 200 EMA can still be confluence for LONG if price is bouncing
                # Check if there's an EMA bounce signal
                if ema_data.get("mtf_ema_200", {}).get("bouncing_off_ema"):
                    factors.append(ConfluenceType.EMA_200_POSITION)
                    descriptions.append("Price bouncing off 200 EMA (support)")
        else:
            if position_vs_ema == "below":
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
        # For SHORT: resistance retest = good (rejection zone)
        # For LONG: support retest = good (bounce zone)
        if is_long:
            # For LONG trades, support holding is confluence
            if sr_levels.get("near_support", False):
                factors.append(ConfluenceType.SUPPORT_RETEST)
                retest_num = sr_levels.get("retest_number", 1)
                descriptions.append(f"Support retest #{retest_num} (bounce zone)")
        else:
            # For SHORT trades, resistance retest is confluence
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

        # 8. Funding Rate (L051 for SHORT, L077 for LONG)
        # For SHORT: extremely negative funding = crowded shorts = squeeze risk (L051)
        # For LONG: neutral/negative funding = safe for longs (L077)
        if funding_rate is not None:
            if is_long:
                # L077: For LONGs, negative or neutral funding is good (shorts crowded)
                # High positive funding = crowded longs = risk
                if funding_rate < self.FUNDING_RATE_EXTREME_LONG:
                    factors.append(ConfluenceType.FUNDING_RATE)
                    if funding_rate < 0:
                        descriptions.append(f"Funding rate negative: {funding_rate:.3f}% (shorts crowded)")
                    else:
                        descriptions.append(f"Funding rate neutral: {funding_rate:.3f}% (safe for LONG)")
            else:
                # L051: For SHORTs, extremely negative = squeeze risk
                if funding_rate < self.FUNDING_RATE_EXTREME_SHORT:
                    factors.append(ConfluenceType.FUNDING_RATE)
                    descriptions.append(f"Extreme funding rate: {funding_rate:.3f}%")

        # 9. TVEM Band (L058)
        # For SHORT: bearish signal = good
        # For LONG: bullish signal = good
        if tvem_data:
            tvem_signal = tvem_data.get("signal")
            if is_long:
                if tvem_signal == "bullish":
                    factors.append(ConfluenceType.TVEM_BAND)
                    descriptions.append("TVEM band bullish signal (recovery)")
            else:
                if tvem_signal == "bearish":
                    factors.append(ConfluenceType.TVEM_BAND)
                    descriptions.append("TVEM band bearish signal")

        # 10. Session 302: MP-VWAP Zone (L085)
        # For SHORT: STRONG_BEARISH or SHORT_ZONE = good (price at VAH resistance)
        # For LONG: STRONG_BULLISH or LONG_ZONE = good (price at VAL support)
        if mp_vwap_data:
            mp_zone = mp_vwap_data.get("zone", "UNKNOWN")
            mp_signal = mp_vwap_data.get("signal", "NEUTRAL")
            mp_confluence = mp_vwap_data.get("confluence", False)
            mp_confluence_strength = mp_vwap_data.get("confluence_strength", "NONE")

            if is_long:
                # LONG: Price at VAL support or STRONG_BULLISH zone
                if mp_signal == "LONG_ZONE" or mp_zone == "STRONG_BULLISH":
                    factors.append(ConfluenceType.MP_VWAP_ZONE)
                    if mp_confluence and mp_confluence_strength == "STRONG":
                        descriptions.append(f"📊 MP-VWAP: {mp_zone} zone + VWAP/POC confluence")
                    else:
                        descriptions.append(f"📊 MP-VWAP: {mp_zone} zone (VAL support)")
            else:
                # SHORT: Price at VAH resistance or STRONG_BEARISH zone
                if mp_signal == "SHORT_ZONE" or mp_zone == "STRONG_BEARISH":
                    factors.append(ConfluenceType.MP_VWAP_ZONE)
                    if mp_confluence and mp_confluence_strength == "STRONG":
                        descriptions.append(f"📊 MP-VWAP: {mp_zone} zone + VWAP/POC confluence")
                    else:
                        descriptions.append(f"📊 MP-VWAP: {mp_zone} zone (VAH resistance)")

        # 11. Session 302: MTF EMA Average (L086)
        # For SHORT: ALL_BEARISH alignment = strong bearish trend confirmation
        # For LONG: ALL_BULLISH alignment = strong recovery confirmation
        if mtf_ema_avg_data:
            ema_alignment = mtf_ema_avg_data.get("ema_alignment", "UNKNOWN")
            ema_signal = mtf_ema_avg_data.get("signal", "NO_BIAS")
            trend_bias = mtf_ema_avg_data.get("trend_bias", "NEUTRAL")
            distance_pct = mtf_ema_avg_data.get("distance_pct", 0)

            if is_long:
                # LONG: ALL_BULLISH alignment or LONG_BIAS signal
                if ema_alignment == "ALL_BULLISH" or ema_signal == "LONG_BIAS":
                    factors.append(ConfluenceType.MTF_EMA_AVG)
                    if trend_bias == "STRONG_BULLISH":
                        descriptions.append(f"📈 MTF EMA Avg: {trend_bias} ({distance_pct:+.1f}% above)")
                    else:
                        descriptions.append(f"📈 MTF EMA Avg: {ema_alignment} (recovery trend)")
            else:
                # SHORT: ALL_BEARISH alignment or SHORT_BIAS signal
                if ema_alignment == "ALL_BEARISH" or ema_signal == "SHORT_BIAS":
                    factors.append(ConfluenceType.MTF_EMA_AVG)
                    if trend_bias == "STRONG_BEARISH":
                        descriptions.append(f"📉 MTF EMA Avg: {trend_bias} ({distance_pct:+.1f}% below)")
                    else:
                        descriptions.append(f"📉 MTF EMA Avg: {ema_alignment} (downtrend)")

        # 12. Session 280 F5: Sherlock Chart Pattern Detection
        # Uses CandlestickDetector for automatic pattern recognition
        # Session 278: Now direction-aware (bullish patterns for LONG)
        if ohlcv_data and CANDLESTICK_DETECTOR_AVAILABLE:
            detected_patterns = self._detect_sherlock_patterns(
                ohlcv_data, timeframe, sr_levels, direction=direction
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
        direction: str = "SHORT",
    ) -> List[tuple]:
        """
        Session 280 F5 + Session 278 LONG: Detect Sherlock candlestick patterns.

        Uses CandlestickDetector to identify reversal patterns:
        - STRONG (3-candle): Evening Star, Morning Star (L032)
        - MODERATE (2-candle): Bearish/Bullish Engulfing
        - WEAK (1-candle): Doji, Shooting Star, Hammer

        Session 278 LONG: Added direction-aware pattern filtering:
        - For SHORT: filters for bearish_reversal patterns
        - For LONG: filters for bullish_reversal patterns (L075 confirmation signals)

        Args:
            ohlcv_data: OHLCV candlestick data
            timeframe: Chart timeframe (e.g., "4h")
            sr_levels: S/R context for pattern strength
            direction: "SHORT" or "LONG" - determines pattern type filtering

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

            # Get signal with all detected patterns
            # Note: get_short_signal still returns ALL patterns, we filter by direction below
            signal = detector.get_short_signal(ohlcv_data, context=context)

            if signal["signal"] == "NO_PATTERN":
                return []

            # Determine which pattern type to filter for based on direction
            is_long = direction.upper() == "LONG"
            target_pattern_type = "bullish_reversal" if is_long else "bearish_reversal"
            pattern_label = "bullish" if is_long else "bearish"

            # Process detected patterns
            patterns_detail = signal.get("patterns_detail", [])
            relevant_patterns = []

            for pattern in patterns_detail:
                pattern_name = pattern.get("pattern_name", "unknown")
                pattern_type_str = pattern.get("pattern_type", "neutral")
                strength = pattern.get("strength", "WEAK")
                confidence = pattern.get("confidence", 0.5)
                signal_for_short = pattern.get("signal_for_short", "WAIT")

                # Session 278: Direction-aware pattern filtering
                # For SHORT: bearish_reversal patterns (rejection patterns)
                # For LONG: bullish_reversal patterns (L075 recovery confirmation signals)
                if pattern_type_str == target_pattern_type:
                    relevant_patterns.append({
                        "name": pattern_name,
                        "strength": strength,
                        "confidence": confidence,
                        "signal": signal_for_short,
                    })

            # Classify by strength and add as confluence
            # Only add the STRONGEST pattern as confluence (avoid double counting)
            if relevant_patterns:
                # Sort by strength: STRONG > MODERATE > WEAK
                strength_order = {"STRONG": 3, "MODERATE": 2, "WEAK": 1}
                relevant_patterns.sort(
                    key=lambda p: (strength_order.get(p["strength"], 0), p["confidence"]),
                    reverse=True
                )

                best_pattern = relevant_patterns[0]
                pattern_name_display = best_pattern["name"].replace("_", " ").title()

                # Description suffix based on direction
                if is_long:
                    reversal_desc = "recovery signal"
                else:
                    reversal_desc = "reversal"

                if best_pattern["strength"] == "STRONG":
                    # 3-candle patterns: Evening Star (SHORT), Morning Star (LONG)
                    detected.append((
                        ConfluenceType.CHART_PATTERN_STRONG,
                        {
                            "description": f"⭐ {pattern_name_display} (3-candle {reversal_desc})",
                            "strength": "STRONG",
                            "confidence": best_pattern["confidence"],
                        }
                    ))
                    logger.info(
                        f"[PATTERN] STRONG {pattern_label} pattern: {pattern_name_display} "
                        f"(confidence: {best_pattern['confidence']:.0%})"
                    )
                elif best_pattern["strength"] == "MODERATE":
                    # 2-candle patterns: Bearish/Bullish Engulfing
                    detected.append((
                        ConfluenceType.CHART_PATTERN_MODERATE,
                        {
                            "description": f"📊 {pattern_name_display} (2-candle {reversal_desc})",
                            "strength": "MODERATE",
                            "confidence": best_pattern["confidence"],
                        }
                    ))
                    logger.info(
                        f"[PATTERN] MODERATE {pattern_label} pattern: {pattern_name_display} "
                        f"(confidence: {best_pattern['confidence']:.0%})"
                    )
                else:
                    # 1-candle patterns: Doji, Shooting Star (SHORT), Hammer (LONG)
                    detected.append((
                        ConfluenceType.CHART_PATTERN_WEAK,
                        {
                            "description": f"🕯️ {pattern_name_display} (1-candle signal)",
                            "strength": "WEAK",
                            "confidence": best_pattern["confidence"],
                        }
                    ))
                    logger.debug(
                        f"[PATTERN] WEAK {pattern_label} pattern: {pattern_name_display} "
                        f"(confidence: {best_pattern['confidence']:.0%})"
                    )

        except Exception as e:
            logger.warning(f"[PATTERN] Error detecting Sherlock patterns: {e}")

        return detected


# =============================================================================
# L059: Breakout Confluence Convenience Function
# =============================================================================


def calculate_breakout_confluence(
    trendline_break: bool,
    resistance_break: bool,
    above_12_ema: bool,
    above_qvwap: bool,
    above_24_ema: bool = False,
    fib_confluence: bool = False,
) -> Dict:
    """
    L059: Calculate overall breakout confluence score.

    Per Sherlock's methodology, breakout signals are rated by confluence:
    - SINGLE: 1 component (max rating 6/10)
    - DOUBLE: 2 components (max rating 7/10)
    - TRIPLE: 3 components (max rating 8/10)
    - QUAD: 4+ components (max rating 9-10/10)

    Args:
        trendline_break: Price broke above descending trendline (LONG) or below ascending (SHORT)
        resistance_break: Price broke above resistance (LONG) or below support (SHORT)
        above_12_ema: Price above 12 EMA (LONG) or below (SHORT)
        above_qvwap: Price above Quarterly VWAP
        above_24_ema: Optional - Price above 24 EMA (additional confluence)
        fib_confluence: Optional - Price at key Fib level (0.618, 0.65, 0.786)

    Returns:
        Dict with:
            confluence_count: int (1-6)
            confluence_level: str ("SINGLE", "DOUBLE", "TRIPLE", "QUAD")
            rating_max: int (max Sherlock rating this confluence supports)
            components: List[str] (active components)
            conviction_modifier: float (±0.5 to ±1.0)

    Example:
        result = calculate_breakout_confluence(
            trendline_break=True,
            resistance_break=True,
            above_12_ema=True,
            above_qvwap=True
        )
        # result = {
        #     "confluence_count": 4,
        #     "confluence_level": "QUAD",
        #     "rating_max": 10,
        #     "components": ["TRENDLINE", "RESISTANCE", "12_EMA", "QVWAP"],
        #     "conviction_modifier": +1.0
        # }
    """
    components = []

    # Core 4 components per L059
    if trendline_break:
        components.append("TRENDLINE")
    if resistance_break:
        components.append("RESISTANCE")
    if above_12_ema:
        components.append("12_EMA")
    if above_qvwap:
        components.append("QVWAP")

    # Optional additional confluence
    if above_24_ema:
        components.append("24_EMA")
    if fib_confluence:
        components.append("FIB_LEVEL")

    count = len(components)

    # Level mapping (capped at QUAD even if 5-6 components)
    if count >= 4:
        level = "QUAD"
        max_rating = 10
        modifier = +1.0
    elif count == 3:
        level = "TRIPLE"
        max_rating = 8
        modifier = +0.5
    elif count == 2:
        level = "DOUBLE"
        max_rating = 7
        modifier = 0.0
    elif count == 1:
        level = "SINGLE"
        max_rating = 6
        modifier = -0.5
    else:
        level = "NONE"
        max_rating = 5
        modifier = -1.0

    return {
        "confluence_count": count,
        "confluence_level": level,
        "rating_max": max_rating,
        "components": components,
        "conviction_modifier": modifier,
    }


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

    # Example 3: LONG confluence (Session 278 - Recovery trade)
    print("=" * 70)
    print("TEST 3: LONG Confluence (Recovery setup)")
    print("=" * 70)

    result = counter.count_confluence(
        ema_data={
            "dual_ema": {"alignment": "bullish"},  # Bullish for LONG
            "mtf_ema_200": {"position_vs_ema": "above"},  # Recovery confirmed
        },
        vwap_data={"qvwap_distance_pct": 0.8},  # <1% from QVWAP
        sr_levels={"near_support": True, "retest_number": 3},  # Support holding
        patterns=[],
        volume_data={"volume_spike": True, "volume_ratio": 2.5},  # Accumulation volume
        funding_rate=-0.02,  # Negative funding = safe for longs (L077)
        tvem_data={"signal": "bullish"},  # Bullish TVEM for LONG
        direction="LONG",  # NEW: Specify LONG direction
    )

    print(f"\nScore: {result.score}/4")
    print(f"Rating: {result.rating}")
    print(f"Conviction Modifier: {result.conviction_modifier:+.1f}")
    print(f"Factors: {', '.join([f.value for f in result.factors])}")
    print(f"Descriptions: {result.factor_descriptions}")
    print()
