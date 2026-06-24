"""
Bottom Detector - Multi-Signal Confirmation for LONG Entry Detection.

Session 278: Phase 2 Week 3 - LONG System MVP Implementation

Detects bottom formation using multiple signals:
1. CAPITULATION_VOLUME: 2x average volume on dump day
2. RSI_DIVERGENCE: Price lower low, RSI higher low (bullish divergence)
3. SUPPORT_HOLD: 3+ retests at same level without break
4. WHALE_ACCUMULATION: Large buy orders detected (deferred to Phase 2)
5. SENTIMENT_SHIFT: Negative -> neutral sentiment

Session 278 Backtest Evidence:
- RSI <20 at bottom: +263% avg recovery
- Both RSI + Volume signals: +239.9% avg recovery (+108% vs neither)
- Volume spike on dump: 30% of genuine bottoms
- Genuine recoveries: 85.7% had RSI oversold (vs 0% for dead cats)

Usage:
    from dacle_core.analysis.bottom_detector import BottomDetector

    detector = BottomDetector()
    signal = detector.detect_bottom("TOKEN", price_history, volume_history)
    if signal.confidence >= 0.6:
        print("HIGH confidence bottom detected")
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# Data Structures
# ============================================================================


@dataclass
class BottomSignal:
    """Result of bottom detection analysis."""

    token: str
    confidence: float  # 0.0 to 1.0 (0.6+ = HIGH confidence)
    signals: List[str]  # Active signals
    recommendation: str  # "READY", "WAIT", "SKIP"

    # Individual signal scores
    capitulation_volume: bool = False
    rsi_divergence: bool = False
    support_hold: bool = False
    whale_accumulation: bool = False
    sentiment_shift: bool = False

    # Phase 1C new signals
    long_lower_wick: bool = False
    fear_greed_extreme: bool = False
    power_law_zone: Optional[str] = None

    # Supporting data
    support_level: Optional[float] = None
    support_touches: int = 0
    rsi_current: Optional[float] = None
    rsi_at_bottom: Optional[float] = None
    volume_ratio: float = 1.0  # Current vs average

    # Dead cat detection
    is_dead_cat: bool = False
    dead_cat_score: int = 0  # 0-5 (higher = more likely dead cat)
    dead_cat_reasons: List[str] = field(default_factory=list)

    # Metadata
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ============================================================================
# Configuration
# ============================================================================


# Volume thresholds
CAPITULATION_VOLUME_MULTIPLIER = 2.0  # 2x average = capitulation
HIGH_VOLUME_MULTIPLIER = 1.5  # 1.5x average = significant

# RSI thresholds (Session 278 backtest)
RSI_EXTREME_OVERSOLD = 20  # Optimal entry zone (+263% recovery)
RSI_OVERSOLD = 30  # Standard oversold
RSI_NEUTRAL_LOW = 40  # Lower neutral

# Fear & Greed thresholds (Phase 1C)
FEAR_GREED_EXTREME_MAX = 25  # Extreme fear zone
POWER_LAW_DEEP_BOOST = 1.3  # Confidence multiplier when in DEEP zone
_TOTAL_SIGNALS = 7  # Total number of bottom detection signals

# Support level thresholds
MIN_SUPPORT_TOUCHES = 3  # Minimum touches for "holding"
SUPPORT_TOLERANCE_PCT = 2.0  # Within 2% = same level

# Dead cat bounce thresholds (L076)
DEAD_CAT_RECOVERY_PCT = 38.2  # 0.382 Fib = typical dead cat
DEAD_CAT_MIN_DAYS = 3  # Recoveries < 3 days = suspicious
DEAD_CAT_VOLUME_THRESHOLD = 1.0  # Volume declining on bounce = dead cat

# Minimum signals for recommendations
MIN_SIGNALS_HIGH = 3  # 3/5 = HIGH confidence
MIN_SIGNALS_MODERATE = 2  # 2/5 = MODERATE confidence


# ============================================================================
# BottomDetector Class
# ============================================================================


class BottomDetector:
    """
    Multi-signal confirmation for bottom detection.

    Session 278 Phase 2: Core analysis component for LONG system.

    Signals detected:
    1. CAPITULATION_VOLUME: Volume spike on dump (2x average)
    2. RSI_DIVERGENCE: Bullish divergence (price LL, RSI HL)
    3. SUPPORT_HOLD: Multiple touches without break (3+)
    4. WHALE_ACCUMULATION: Large wallet accumulation (Phase 2)
    5. SENTIMENT_SHIFT: Fear -> neutral shift

    Confidence levels:
    - 0.6+ (3/5 signals): HIGH confidence - READY for entry
    - 0.4-0.6 (2/5 signals): MODERATE - Wait for confirmation
    - <0.4: LOW - Not at bottom

    Dead Cat Detection (L076):
    - Volume declining on bounce
    - Recovery < 0.382 Fib
    - Duration < 3 days
    - No higher low formation
    """

    def __init__(self):
        """Initialize BottomDetector."""
        pass

    def detect_bottom(
        self,
        token: str,
        price_history: Optional[List[float]] = None,
        volume_history: Optional[List[float]] = None,
        ta_data: Optional[Dict] = None,
        fear_greed_index: Optional[int] = None,
        power_law_zone: Optional[str] = None,
    ) -> BottomSignal:
        """
        Detect bottom formation using multiple signals.

        Args:
            token: Token symbol
            price_history: List of recent prices (newest last)
            volume_history: List of recent volumes (newest last)
            ta_data: Technical analysis data from TADataAggregator

        Returns:
            BottomSignal with confidence and active signals
        """
        logger.info(f"Detecting bottom for {token}")

        signals = []
        signal_details = {}

        # Initialize empty histories if not provided
        price_history = price_history or []
        volume_history = volume_history or []
        ta_data = ta_data or {}

        # 1. Check capitulation volume
        cap_volume = self._check_capitulation_volume(volume_history, ta_data)
        signal_details["capitulation_volume"] = cap_volume["detected"]
        if cap_volume["detected"]:
            signals.append("CAPITULATION_VOLUME")

        # 2. Check RSI divergence
        rsi_div = self._check_rsi_divergence(price_history, ta_data)
        signal_details["rsi_divergence"] = rsi_div["detected"]
        if rsi_div["detected"]:
            signals.append("RSI_DIVERGENCE")

        # 3. Check support holding
        support = self._check_support_holding(price_history, ta_data)
        signal_details["support_hold"] = support["detected"]
        if support["detected"]:
            signals.append("SUPPORT_HOLD")

        # 4. Check whale accumulation (deferred to Phase 2)
        whale = self._check_whale_accumulation(token, ta_data)
        signal_details["whale_accumulation"] = whale["detected"]
        if whale["detected"]:
            signals.append("WHALE_ACCUMULATION")

        # 5. Check sentiment shift
        sentiment = self._check_sentiment_shift(ta_data)
        signal_details["sentiment_shift"] = sentiment["detected"]
        if sentiment["detected"]:
            signals.append("SENTIMENT_SHIFT")

        # 6. Check long lower wick (Phase 1C)
        wick = self._check_long_lower_wick(price_history, ta_data)
        signal_details["long_lower_wick"] = wick["detected"]
        if wick["detected"]:
            signals.append("LONG_LOWER_WICK")

        # 7. Check Fear & Greed extreme (Phase 1C)
        fg = self._check_fear_greed_extreme(fear_greed_index, ta_data)
        signal_details["fear_greed_extreme"] = fg["detected"]
        if fg["detected"]:
            signals.append("FEAR_GREED_EXTREME")

        # Calculate confidence (Phase 1C: 7-signal)
        confidence = len(signals) / float(_TOTAL_SIGNALS)

        # Zone context boost (Phase 1C)
        if power_law_zone == "DEEP" and confidence > 0:
            confidence = min(1.0, confidence * POWER_LAW_DEEP_BOOST)

        # Determine recommendation
        if confidence >= 0.6:
            recommendation = "READY"
        elif confidence >= 0.4:
            recommendation = "WAIT"
        else:
            recommendation = "SKIP"

        # Check for dead cat bounce (L076)
        dead_cat = self._check_dead_cat_bounce(
            price_history, volume_history, ta_data, signals
        )

        # Downgrade if dead cat detected
        if dead_cat["is_dead_cat"]:
            recommendation = "SKIP"
            confidence = min(confidence, 0.2)  # Cap at 20%

        return BottomSignal(
            token=token,
            confidence=confidence,
            signals=signals,
            recommendation=recommendation,
            capitulation_volume=signal_details["capitulation_volume"],
            rsi_divergence=signal_details["rsi_divergence"],
            support_hold=signal_details["support_hold"],
            whale_accumulation=signal_details["whale_accumulation"],
            sentiment_shift=signal_details["sentiment_shift"],
            long_lower_wick=signal_details.get("long_lower_wick", False),
            fear_greed_extreme=signal_details.get("fear_greed_extreme", False),
            power_law_zone=power_law_zone,
            support_level=support.get("level"),
            support_touches=support.get("touches", 0),
            rsi_current=ta_data.get("rsi_4h") or ta_data.get("rsi"),
            rsi_at_bottom=rsi_div.get("rsi_at_low"),
            volume_ratio=cap_volume.get("ratio", 1.0),
            is_dead_cat=dead_cat["is_dead_cat"],
            dead_cat_score=dead_cat["score"],
            dead_cat_reasons=dead_cat["reasons"],
        )

    def _check_long_lower_wick(
        self, price_history: List[float], ta_data: Dict
    ) -> Dict:
        """
        Check for long lower wick signal.

        Lower wick = min(open,close) - low.
        Long lower wick = (lower_wick/candle_range) > 0.6 AND wick >= 0.5% of range.
        """
        result = {"detected": False}

        # 1. Explicit flag
        if ta_data.get("long_lower_wick"):
            result["detected"] = True
            return result

        # 2. Candle dict
        candle = ta_data.get("candle")
        if isinstance(candle, dict):
            h = float(candle.get("high", 0) or 0)
            l = float(candle.get("low", 0) or 0)
            o = float(candle.get("open", 0) or 0)
            c_val = float(candle.get("close", 0) or o or 0)
            lower_body = min(o, c_val)
            if h > l and h > 0:
                rng = h - l
                wick_pct = (lower_body - l) / rng if rng > 0 else 0.0
                wick_size = lower_body - l
                if wick_pct > 0.6 and wick_size >= rng * 0.005:
                    result["detected"] = True
            return result

        # 3. Daily OHLCV dict
        ohlcv = ta_data.get("daily_ohlcv") or ta_data.get("ohlcv_1d")
        if isinstance(ohlcv, dict):
            h = float(ohlcv.get("high", 0) or 0)
            l = float(ohlcv.get("low", 0) or 0)
            o = float(ohlcv.get("open", 0) or 0)
            c_val = float(ohlcv.get("close", 0) or 0)
            lower_body = min(o, c_val)
            if h > l and h > 0:
                rng = h - l
                wick_pct = (lower_body - l) / rng if rng > 0 else 0.0
                wick_size = lower_body - l
                if wick_pct > 0.6 and wick_size >= rng * 0.005:
                    result["detected"] = True
            return result

        # 4. Price history fallback
        if len(price_history) >= 3:
            recent = price_history[-3:]
            h, l_val, c_val = max(recent), min(recent), recent[-1]
            lower_body = min(recent[-1], recent[-2])
            if h > l_val and h > 0:
                rng = h - l_val
                wick_pct = (lower_body - l_val) / rng if rng > 0 else 0.0
                wick_size = lower_body - l_val
                if wick_pct > 0.6 and wick_size >= rng * 0.005:
                    result["detected"] = True
        return result

    def _check_fear_greed_extreme(
        self, fear_greed_index: Optional[int] = None, ta_data: Optional[Dict] = None
    ) -> Dict:
        """
        Check for extreme Fear & Greed reading.
        F&G <= FEAR_GREED_EXTREME_MAX (25) = extreme fear.
        """
        result = {"detected": False}
        fg = fear_greed_index
        if fg is None and ta_data:
            fg = ta_data.get("fear_greed_index")
        if fg is not None:
            try:
                fg = int(fg)
                result["detected"] = fg <= FEAR_GREED_EXTREME_MAX
            except (ValueError, TypeError):
                pass
        return result

    def _check_capitulation_volume(
        self, volume_history: List[float], ta_data: Dict
    ) -> Dict:
        """
        Check for volume capitulation signal.

        Capitulation = 2x average volume on dump day.
        This indicates panic selling has peaked.

        Session 278: 30% of genuine bottoms had volume spike.
        """
        result = {"detected": False, "ratio": 1.0}

        # Try TA data first
        dump_volume_ratio = ta_data.get("dump_volume_ratio")
        if dump_volume_ratio:
            result["ratio"] = dump_volume_ratio
            result["detected"] = dump_volume_ratio >= CAPITULATION_VOLUME_MULTIPLIER
            return result

        # Calculate from history
        if len(volume_history) < 5:
            return result

        # Average volume (excluding last day)
        avg_volume = sum(volume_history[:-1]) / (len(volume_history) - 1)
        current_volume = volume_history[-1]

        if avg_volume > 0:
            ratio = current_volume / avg_volume
            result["ratio"] = ratio
            result["detected"] = ratio >= CAPITULATION_VOLUME_MULTIPLIER

        return result

    def _check_rsi_divergence(
        self, price_history: List[float], ta_data: Dict
    ) -> Dict:
        """
        Check for bullish RSI divergence.

        Bullish divergence: Price makes lower low, RSI makes higher low.
        This indicates selling pressure weakening despite price drop.

        Session 278: RSI <20 = +263% avg recovery.
        """
        result = {"detected": False, "rsi_at_low": None}

        # Check if divergence explicitly detected
        if ta_data.get("rsi_bullish_divergence"):
            result["detected"] = True
            result["rsi_at_low"] = ta_data.get("rsi_4h") or ta_data.get("rsi_1d")
            return result

        # Check for oversold RSI (proxy for potential divergence)
        rsi = ta_data.get("rsi_4h") or ta_data.get("rsi_1d") or ta_data.get("rsi", 50)
        result["rsi_at_low"] = rsi

        if rsi < RSI_EXTREME_OVERSOLD:
            # Extreme oversold = high probability of divergence forming
            result["detected"] = True
        elif rsi < RSI_OVERSOLD:
            # Need price confirmation for standard oversold
            if len(price_history) >= 10:
                # Check if price is at or near low
                recent_low = min(price_history[-10:])
                current_price = price_history[-1] if price_history else 0
                if current_price <= recent_low * 1.02:  # Within 2% of low
                    result["detected"] = True

        return result

    def _check_support_holding(
        self, price_history: List[float], ta_data: Dict
    ) -> Dict:
        """
        Check if support level is holding.

        Support holding = 3+ touches at same level without break.
        This indicates buyers defending the level.
        """
        result = {"detected": False, "level": None, "touches": 0}

        # Check TA data for support info
        support_level = ta_data.get("support_level") or ta_data.get("horizontal_support")
        support_touches = ta_data.get("support_touches", 0)

        if support_level:
            result["level"] = support_level
            result["touches"] = support_touches
            result["detected"] = support_touches >= MIN_SUPPORT_TOUCHES
            return result

        # Calculate from price history
        if len(price_history) < 20:
            return result

        # Find potential support level (recent low zone)
        lows = []
        for i in range(len(price_history) - 5):
            window = price_history[i : i + 5]
            if price_history[i + 2] == min(window):
                lows.append((i + 2, price_history[i + 2]))

        if not lows:
            return result

        # Group lows by proximity (within tolerance)
        support_zones = []
        for idx, low_price in lows:
            placed = False
            for zone in support_zones:
                zone_avg = sum(p for _, p in zone) / len(zone)
                if abs(low_price - zone_avg) / zone_avg < SUPPORT_TOLERANCE_PCT / 100:
                    zone.append((idx, low_price))
                    placed = True
                    break
            if not placed:
                support_zones.append([(idx, low_price)])

        # Find zone with most touches
        if support_zones:
            best_zone = max(support_zones, key=len)
            result["touches"] = len(best_zone)
            result["level"] = sum(p for _, p in best_zone) / len(best_zone)
            result["detected"] = result["touches"] >= MIN_SUPPORT_TOUCHES

        return result

    def _check_whale_accumulation(self, token: str, ta_data: Dict) -> Dict:
        """
        Check for whale accumulation signal.

        DEFERRED to Phase 2 - requires on-chain data.

        Would check:
        - Large wallet buying activity
        - Exchange outflows
        - Order book depth
        """
        result = {"detected": False}

        # Check if whale accumulation data provided
        if ta_data.get("whale_accumulation"):
            result["detected"] = True

        # Phase 2: Implement Nansen/Arkham integration

        return result

    def _check_sentiment_shift(self, ta_data: Dict) -> Dict:
        """
        Check for sentiment shift from negative to neutral.

        Uses Fear & Greed index and social sentiment.
        Shift from extreme fear to neutral = capitulation complete.
        """
        result = {"detected": False}

        # Check explicit sentiment shift flag
        if ta_data.get("sentiment_shift"):
            result["detected"] = True
            return result

        # Check Fear & Greed index
        fear_greed = ta_data.get("fear_greed_index", 50)

        # Extreme fear (<20) = capitulation phase
        if fear_greed < 20:
            result["detected"] = True
        elif fear_greed < 30:
            # Fear zone - check if improving
            prev_fear_greed = ta_data.get("prev_fear_greed", fear_greed)
            if fear_greed > prev_fear_greed:
                result["detected"] = True

        return result

    def _check_dead_cat_bounce(
        self,
        price_history: List[float],
        volume_history: List[float],
        ta_data: Dict,
        active_signals: List[str],
    ) -> Dict:
        """
        Detect dead cat bounce pattern (L076).

        Dead Cat Indicators (AVOID LONG):
        - Volume declining on bounce (no accumulation)
        - Bounce to 0.382 Fib or less
        - Duration < 3 days
        - No higher low formation

        Session 278: Genuine recoveries = 85.7% RSI oversold (vs 0% for dead cats).
        """
        result = {"is_dead_cat": False, "score": 0, "reasons": []}

        score = 0

        # Check 1: Volume declining on bounce
        if len(volume_history) >= 5:
            recent_volumes = volume_history[-5:]
            if all(recent_volumes[i] >= recent_volumes[i + 1] for i in range(len(recent_volumes) - 1)):
                score += 1
                result["reasons"].append("Volume declining on bounce")

        # Check 2: Weak recovery (< 0.382 Fib)
        if len(price_history) >= 10:
            recent_low = min(price_history[-20:]) if len(price_history) >= 20 else min(price_history)
            current_price = price_history[-1]
            ath = max(price_history) if price_history else current_price

            if ath > recent_low:
                recovery_pct = (current_price - recent_low) / (ath - recent_low) * 100
                if recovery_pct < DEAD_CAT_RECOVERY_PCT:
                    score += 1
                    result["reasons"].append(f"Weak recovery ({recovery_pct:.1f}% < 38.2% Fib)")

        # Check 3: No RSI oversold at bottom (Session 278 key finding)
        rsi = ta_data.get("rsi_4h") or ta_data.get("rsi_1d") or 50
        if rsi >= RSI_OVERSOLD:
            score += 1
            result["reasons"].append(f"RSI not oversold ({rsi:.0f} >= 30)")

        # Check 4: Lack of bottom signals
        if len(active_signals) < 2:
            score += 1
            result["reasons"].append(f"Insufficient bottom signals ({len(active_signals)}/5)")

        # Check 5: Short duration recovery (would need time data)
        recovery_days = ta_data.get("recovery_days", 0)
        if recovery_days > 0 and recovery_days < DEAD_CAT_MIN_DAYS:
            score += 1
            result["reasons"].append(f"Short recovery duration ({recovery_days} days < 3)")

        result["score"] = score
        result["is_dead_cat"] = score >= 3  # 3/5 indicators = dead cat

        return result

    def get_entry_zone(
        self, token: str, bottom_signal: BottomSignal, current_price: float
    ) -> Dict:
        """
        Calculate entry zone based on bottom detection.

        Returns entry range and stop loss level.
        """
        if not bottom_signal.support_level:
            return {
                "entry_low": current_price * 0.98,
                "entry_high": current_price * 1.02,
                "stop_loss": current_price * 0.85,
            }

        support = bottom_signal.support_level

        # Entry zone: At or slightly above support
        entry_low = support
        entry_high = support * 1.02  # 2% above support

        # Stop loss: 5% below support (confirmed break)
        stop_loss = support * 0.95

        return {
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop_loss": stop_loss,
            "support_level": support,
            "support_touches": bottom_signal.support_touches,
        }

    def get_confidence_breakdown(self, signal: BottomSignal) -> Dict:
        """
        Get detailed breakdown of confidence scoring.

        Returns weights and contributions of each signal.
        """
        weights = {
            "capitulation_volume": 0.20,
            "rsi_divergence": 0.20,
            "support_hold": 0.20,
            "whale_accumulation": 0.05,
            "sentiment_shift": 0.15,
            "long_lower_wick": 0.10,
            "fear_greed_extreme": 0.10,
        }

        breakdown = {}
        total = 0

        for signal_name, weight in weights.items():
            active = getattr(signal, signal_name, False)
            contribution = weight if active else 0
            total += contribution
            breakdown[signal_name] = {
                "active": active,
                "weight": weight,
                "contribution": contribution,
            }

        breakdown["total_confidence"] = total
        breakdown["recommendation"] = signal.recommendation

        return breakdown


# ============================================================================
# Convenience Functions
# ============================================================================


def detect_bottom(
    token: str,
    price_history: Optional[List[float]] = None,
    volume_history: Optional[List[float]] = None,
    ta_data: Optional[Dict] = None,
) -> BottomSignal:
    """
    Convenience function for bottom detection.

    Args:
        token: Token symbol
        price_history: List of recent prices
        volume_history: List of recent volumes
        ta_data: Technical analysis data

    Returns:
        BottomSignal with confidence and recommendations
    """
    detector = BottomDetector()
    return detector.detect_bottom(token, price_history, volume_history, ta_data)


def is_dead_cat(
    token: str,
    ta_data: Optional[Dict] = None,
) -> Tuple[bool, int, List[str]]:
    """
    Quick check for dead cat bounce (L076).

    Returns:
        (is_dead_cat, score, reasons)
    """
    detector = BottomDetector()
    signal = detector.detect_bottom(token, ta_data=ta_data)
    return signal.is_dead_cat, signal.dead_cat_score, signal.dead_cat_reasons


# ============================================================================
# Example Usage
# ============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test with sample data
    test_prices = [1.0, 0.9, 0.8, 0.7, 0.6, 0.55, 0.52, 0.50, 0.48, 0.50, 0.52]
    test_volumes = [100, 150, 200, 300, 500, 800, 600, 400, 300, 350, 400]
    test_ta = {
        "rsi_4h": 22,
        "rsi_bullish_divergence": True,
        "fear_greed_index": 18,
        "dump_volume_ratio": 2.5,
    }

    detector = BottomDetector()
    result = detector.detect_bottom(
        "TEST",
        price_history=test_prices,
        volume_history=test_volumes,
        ta_data=test_ta,
    )

    print(f"\n{'='*60}")
    print(f"BOTTOM DETECTION: {result.token}")
    print(f"{'='*60}")
    print(f"\nConfidence: {result.confidence:.0%}")
    print(f"Recommendation: {result.recommendation}")
    print(f"\nActive Signals ({len(result.signals)}/5):")
    for signal in result.signals:
        print(f"  ✓ {signal}")

    print(f"\nSignal Details:")
    print(f"  Capitulation Volume: {'✓' if result.capitulation_volume else '✗'} (ratio: {result.volume_ratio:.1f}x)")
    print(f"  RSI Divergence: {'✓' if result.rsi_divergence else '✗'} (RSI: {result.rsi_current})")
    print(f"  Support Hold: {'✓' if result.support_hold else '✗'} (touches: {result.support_touches})")
    print(f"  Whale Accumulation: {'✓' if result.whale_accumulation else '✗'}")
    print(f"  Sentiment Shift: {'✓' if result.sentiment_shift else '✗'}")

    if result.is_dead_cat:
        print(f"\n⚠️ DEAD CAT BOUNCE DETECTED (score: {result.dead_cat_score}/5)")
        for reason in result.dead_cat_reasons:
            print(f"  • {reason}")

    print(f"\n{'='*60}\n")
