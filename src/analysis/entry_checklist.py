#!/usr/bin/env python3
"""
L036: David's Entry Methodology Checklist
=========================================
Session 300: Automated entry validation based on David's 7-step methodology.

David's Checklist:
1. Trendline Break - 4H body close required
2. Entry Zone ID - Identify potential entry area
3. Market Structure Shift (MSS) - LH, EQH, or reversal candle
4. Break of Structure (BOS) - Swing point breakout
5. FIB Retrace - Price in 0.382-0.786 zone
6. Index Confluence - USDT.D, TOTAL3 aligned
7. Volume Check - Sufficient volume on move

Quote: "So if we fail to change structure, we're still on the bullish side.
       Obviously, you're going to get triggered our SL for sure."

References:
- LEARNING_036_ENTRY_METHODOLOGY_CHECKLIST.md
- David's Playbook (December 12, 2025)
- PIEVERSE Equal High pattern (Nov 14, 2025)
- POWER trade validation (Dec 2025)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class ChecklistStatus(Enum):
    """Status of a checklist item."""
    PASSED = "passed"       # Condition met
    PENDING = "pending"     # Waiting for condition
    FAILED = "failed"       # Condition invalidated
    SKIPPED = "skipped"     # Not applicable


class MSSSignalType(Enum):
    """Market Structure Shift signal types."""
    LOWER_HIGH = "lower_high"       # Price makes lower high than previous
    EQUAL_HIGH = "equal_high"       # Price fails to make new high
    DOJI_ATH = "doji_ath"           # Indecision at ATH (L032)
    ENGULFING = "engulfing"         # Bearish engulfing at resistance
    SHOOTING_STAR = "shooting_star" # Long upper wick rejection
    NONE = "none"


class EntryRecommendation(Enum):
    """Entry recommendation based on checklist."""
    EXECUTE = "EXECUTE"     # All conditions met
    READY = "READY"         # Most conditions met, minor pending
    WAIT = "WAIT"           # Key conditions pending
    SKIP = "SKIP"           # Conditions invalidated


@dataclass
class ChecklistItem:
    """Individual checklist item result."""
    name: str
    status: ChecklistStatus
    detail: str = ""
    weight: str = "REQUIRED"  # REQUIRED or CONFIRMATION
    value: Optional[float] = None


@dataclass
class TrendlineBreak:
    """Trendline break detection result."""
    detected: bool = False
    break_candle_time: Optional[str] = None
    break_price: Optional[float] = None
    trendline_slope: Optional[float] = None
    touches: int = 0
    confidence: float = 0.0


@dataclass
class MarketStructureShift:
    """MSS detection result."""
    detected: bool = False
    signal_type: MSSSignalType = MSSSignalType.NONE
    signal_candle_time: Optional[str] = None
    previous_swing: Optional[float] = None
    current_swing: Optional[float] = None
    confidence: float = 0.0


@dataclass
class BreakOfStructure:
    """BOS detection result."""
    detected: bool = False
    break_candle_time: Optional[str] = None
    swing_level_broken: Optional[float] = None
    direction: str = "bearish"  # bearish for shorts
    confidence: float = 0.0


@dataclass
class FibRetrace:
    """Fibonacci retracement analysis."""
    in_zone: bool = False
    current_level: Optional[float] = None  # 0.382, 0.5, 0.618, 0.786
    zone_name: str = ""  # "golden_pocket", "shallow", "deep"
    fib_levels: Dict[str, float] = field(default_factory=dict)
    entry_quality: str = ""  # "ideal", "good", "risky"


@dataclass
class EntryChecklistResult:
    """Complete entry checklist result."""
    token_symbol: str
    direction: str  # SHORT or LONG
    timestamp: str

    # Individual checks
    trendline_break: TrendlineBreak = field(default_factory=TrendlineBreak)
    mss: MarketStructureShift = field(default_factory=MarketStructureShift)
    bos: BreakOfStructure = field(default_factory=BreakOfStructure)
    fib_retrace: FibRetrace = field(default_factory=FibRetrace)
    indices_aligned: bool = False
    volume_confirmed: bool = False

    # Summary
    checklist_items: List[ChecklistItem] = field(default_factory=list)
    conditions_met: int = 0
    conditions_required: int = 5
    recommendation: EntryRecommendation = EntryRecommendation.WAIT
    missing_conditions: List[str] = field(default_factory=list)
    reasoning: List[str] = field(default_factory=list)

    # Scoring
    entry_score: float = 0.0  # 0-10


class EntryChecklistValidator:
    """
    L036: Validates entry conditions per David's methodology.

    Usage:
        validator = EntryChecklistValidator()
        result = validator.validate(ohlcv_4h, token_symbol="TALUS", direction="SHORT")

        if result.recommendation == EntryRecommendation.EXECUTE:
            # All conditions met, safe to enter
        elif result.recommendation == EntryRecommendation.WAIT:
            print(f"Missing: {result.missing_conditions}")
    """

    # FIB zones for entry quality assessment
    FIB_ZONES = {
        "deep": (0.786, 1.0),      # DCA zone, risky
        "golden_pocket": (0.618, 0.786),  # Ideal entry
        "midpoint": (0.5, 0.618),   # Good entry
        "shallow": (0.382, 0.5),    # Conservative entry
    }

    # Minimum touches required for valid trendline
    MIN_TRENDLINE_TOUCHES = 2

    # Equal high tolerance (within X% = equal)
    EQUAL_HIGH_TOLERANCE = 0.005  # 0.5%

    def __init__(self, indices_tracker=None):
        """
        Initialize entry checklist validator.

        Args:
            indices_tracker: Optional IndicesTracker instance for index confluence
        """
        self.indices_tracker = indices_tracker

    def validate(
        self,
        ohlcv: List[List],
        token_symbol: str,
        direction: str = "SHORT",
        current_price: Optional[float] = None,
        indices_data: Optional[Dict] = None
    ) -> EntryChecklistResult:
        """
        Validate all entry conditions per David's checklist.

        Args:
            ohlcv: OHLCV data [[timestamp, open, high, low, close, volume], ...]
            token_symbol: Token symbol (e.g., "TALUS")
            direction: "SHORT" or "LONG"
            current_price: Optional current price (uses last close if not provided)
            indices_data: Optional pre-fetched indices data

        Returns:
            EntryChecklistResult with all validation details
        """
        logger.info(f"L036: Validating entry checklist for {token_symbol} {direction}")

        result = EntryChecklistResult(
            token_symbol=token_symbol,
            direction=direction,
            timestamp=datetime.utcnow().isoformat()
        )

        if not ohlcv or len(ohlcv) < 20:
            result.reasoning.append("Insufficient OHLCV data (need 20+ candles)")
            return result

        # Extract price data
        closes = np.array([c[4] for c in ohlcv])
        highs = np.array([c[2] for c in ohlcv])
        lows = np.array([c[3] for c in ohlcv])
        volumes = np.array([c[5] for c in ohlcv])
        timestamps = [c[0] for c in ohlcv]

        if current_price is None:
            current_price = closes[-1]

        # 1. Trendline Break Detection
        result.trendline_break = self._detect_trendline_break(
            ohlcv, direction, closes, highs, lows, timestamps
        )

        # 2. Market Structure Shift (MSS)
        result.mss = self._detect_mss(
            ohlcv, direction, closes, highs, lows, timestamps
        )

        # 3. Break of Structure (BOS)
        result.bos = self._detect_bos(
            ohlcv, direction, closes, highs, lows, timestamps
        )

        # 4. FIB Retracement Position
        result.fib_retrace = self._analyze_fib_position(
            highs, lows, current_price, direction
        )

        # 5. Index Confluence
        result.indices_aligned = self._check_index_confluence(
            direction, indices_data
        )

        # 6. Volume Confirmation
        result.volume_confirmed = self._check_volume(volumes)

        # Build checklist items
        result.checklist_items = self._build_checklist(result)

        # Calculate score and recommendation
        result = self._calculate_score(result)

        return result

    def _detect_trendline_break(
        self,
        ohlcv: List[List],
        direction: str,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        timestamps: List
    ) -> TrendlineBreak:
        """
        Detect trendline break with 4H body close confirmation.

        For SHORT: Looking for break below ascending trendline
        For LONG: Looking for break above descending trendline
        """
        result = TrendlineBreak()

        try:
            lookback = min(50, len(closes))

            if direction == "SHORT":
                # Find ascending trendline (connecting swing lows)
                swing_lows = self._find_swing_points(lows[-lookback:], "low")

                if len(swing_lows) >= self.MIN_TRENDLINE_TOUCHES:
                    # Fit trendline through swing lows
                    x = np.array([p[0] for p in swing_lows])
                    y = np.array([p[1] for p in swing_lows])

                    if len(x) >= 2:
                        slope, intercept = np.polyfit(x, y, 1)

                        # Check if latest close broke below trendline
                        trendline_at_current = slope * (lookback - 1) + intercept
                        last_close = closes[-1]

                        if last_close < trendline_at_current:
                            result.detected = True
                            result.break_price = last_close
                            result.break_candle_time = str(timestamps[-1])
                            result.trendline_slope = slope
                            result.touches = len(swing_lows)
                            result.confidence = min(len(swing_lows) / 4.0, 1.0)

            else:  # LONG
                # Find descending trendline (connecting swing highs)
                swing_highs = self._find_swing_points(highs[-lookback:], "high")

                if len(swing_highs) >= self.MIN_TRENDLINE_TOUCHES:
                    x = np.array([p[0] for p in swing_highs])
                    y = np.array([p[1] for p in swing_highs])

                    if len(x) >= 2:
                        slope, intercept = np.polyfit(x, y, 1)
                        trendline_at_current = slope * (lookback - 1) + intercept
                        last_close = closes[-1]

                        if last_close > trendline_at_current:
                            result.detected = True
                            result.break_price = last_close
                            result.break_candle_time = str(timestamps[-1])
                            result.trendline_slope = slope
                            result.touches = len(swing_highs)
                            result.confidence = min(len(swing_highs) / 4.0, 1.0)

        except Exception as e:
            logger.warning(f"Trendline detection error: {e}")

        return result

    def _detect_mss(
        self,
        ohlcv: List[List],
        direction: str,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        timestamps: List
    ) -> MarketStructureShift:
        """
        Detect Market Structure Shift (MSS).

        For SHORT: Looking for bearish MSS (LH, EQH, reversal candles)
        For LONG: Looking for bullish MSS (HL, EQL, bullish reversal)

        David: "The last swing high failed to make a new high... that makes an EQUAL HIGH"
        """
        result = MarketStructureShift()

        try:
            lookback = min(30, len(closes))
            recent_highs = highs[-lookback:]
            recent_lows = lows[-lookback:]
            recent_closes = closes[-lookback:]
            recent_opens = np.array([c[1] for c in ohlcv[-lookback:]])

            if direction == "SHORT":
                # Find recent swing highs
                swing_highs = self._find_swing_points(recent_highs, "high", window=5)

                if len(swing_highs) >= 2:
                    prev_high = swing_highs[-2][1]
                    curr_high = swing_highs[-1][1]

                    # Check for Lower High
                    if curr_high < prev_high * 0.995:  # Clear lower high
                        result.detected = True
                        result.signal_type = MSSSignalType.LOWER_HIGH
                        result.previous_swing = prev_high
                        result.current_swing = curr_high
                        result.confidence = 0.9

                    # Check for Equal High (within tolerance)
                    elif abs(curr_high - prev_high) / prev_high < self.EQUAL_HIGH_TOLERANCE:
                        result.detected = True
                        result.signal_type = MSSSignalType.EQUAL_HIGH
                        result.previous_swing = prev_high
                        result.current_swing = curr_high
                        result.confidence = 0.75

                # Check for reversal candles at recent high
                if not result.detected:
                    candle_result = self._detect_reversal_candle(
                        ohlcv[-5:], "bearish"
                    )
                    if candle_result[0]:
                        result.detected = True
                        result.signal_type = candle_result[1]
                        result.confidence = candle_result[2]

            else:  # LONG
                # Find recent swing lows
                swing_lows = self._find_swing_points(recent_lows, "low", window=5)

                if len(swing_lows) >= 2:
                    prev_low = swing_lows[-2][1]
                    curr_low = swing_lows[-1][1]

                    # Check for Higher Low
                    if curr_low > prev_low * 1.005:
                        result.detected = True
                        result.signal_type = MSSSignalType.LOWER_HIGH  # Reuse enum
                        result.previous_swing = prev_low
                        result.current_swing = curr_low
                        result.confidence = 0.9

        except Exception as e:
            logger.warning(f"MSS detection error: {e}")

        return result

    def _detect_bos(
        self,
        ohlcv: List[List],
        direction: str,
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        timestamps: List
    ) -> BreakOfStructure:
        """
        Detect Break of Structure (BOS).

        BOS = Price breaks a significant swing point that confirms MSS.
        For SHORT: Break below recent swing low
        For LONG: Break above recent swing high
        """
        result = BreakOfStructure()

        try:
            lookback = min(30, len(closes))

            if direction == "SHORT":
                # Find recent swing lows
                swing_lows = self._find_swing_points(lows[-lookback:], "low", window=5)

                if len(swing_lows) >= 1:
                    # Check if current price broke below the most recent significant swing low
                    recent_swing_low = swing_lows[-1][1]
                    current_close = closes[-1]

                    if current_close < recent_swing_low:
                        result.detected = True
                        result.swing_level_broken = recent_swing_low
                        result.break_candle_time = str(timestamps[-1])
                        result.direction = "bearish"
                        result.confidence = 0.85

            else:  # LONG
                swing_highs = self._find_swing_points(highs[-lookback:], "high", window=5)

                if len(swing_highs) >= 1:
                    recent_swing_high = swing_highs[-1][1]
                    current_close = closes[-1]

                    if current_close > recent_swing_high:
                        result.detected = True
                        result.swing_level_broken = recent_swing_high
                        result.break_candle_time = str(timestamps[-1])
                        result.direction = "bullish"
                        result.confidence = 0.85

        except Exception as e:
            logger.warning(f"BOS detection error: {e}")

        return result

    def _analyze_fib_position(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        current_price: float,
        direction: str
    ) -> FibRetrace:
        """
        Analyze current price position within Fibonacci retracement zones.

        FIB drawn from swing low to swing high (for shorts) or vice versa.
        """
        result = FibRetrace()

        try:
            lookback = min(50, len(highs))
            recent_highs = highs[-lookback:]
            recent_lows = lows[-lookback:]

            swing_high = np.max(recent_highs)
            swing_low = np.min(recent_lows)

            if swing_high == swing_low:
                return result

            range_size = swing_high - swing_low

            # Calculate FIB levels
            fib_levels = {
                "0": swing_low,
                "0.236": swing_low + range_size * 0.236,
                "0.382": swing_low + range_size * 0.382,
                "0.5": swing_low + range_size * 0.5,
                "0.618": swing_low + range_size * 0.618,
                "0.786": swing_low + range_size * 0.786,
                "1": swing_high
            }
            result.fib_levels = fib_levels

            # Calculate current FIB level (0-1 scale)
            current_fib = (current_price - swing_low) / range_size
            result.current_level = round(current_fib, 3)

            # Check if in valid zone (0.382-0.786 for entry)
            if 0.382 <= current_fib <= 0.786:
                result.in_zone = True

                # Determine zone name and entry quality
                for zone_name, (low, high) in self.FIB_ZONES.items():
                    if low <= current_fib <= high:
                        result.zone_name = zone_name
                        break

                # Entry quality based on zone
                if result.zone_name == "golden_pocket":
                    result.entry_quality = "ideal"
                elif result.zone_name == "midpoint":
                    result.entry_quality = "good"
                elif result.zone_name == "shallow":
                    result.entry_quality = "conservative"
                else:
                    result.entry_quality = "risky"
            else:
                result.in_zone = False
                if current_fib < 0.382:
                    result.entry_quality = "too_extended"
                else:
                    result.entry_quality = "too_shallow"

        except Exception as e:
            logger.warning(f"FIB analysis error: {e}")

        return result

    def _check_index_confluence(
        self,
        direction: str,
        indices_data: Optional[Dict] = None
    ) -> bool:
        """
        Check if macro indices support the trade direction.

        For SHORT: USDT.D rising (risk-off) + TOTAL3 falling
        For LONG: USDT.D falling (risk-on) + TOTAL3 rising
        """
        if indices_data is None:
            # Try to fetch from IndicesTracker if available
            if self.indices_tracker:
                try:
                    indices_data = self.indices_tracker.get_current_state()
                except Exception:
                    return False
            else:
                return False

        try:
            usdt_d_trend = indices_data.get("usdt_d_trend", "neutral")
            total3_trend = indices_data.get("total3_trend", "neutral")

            if direction == "SHORT":
                # USDT.D rising + TOTAL3 falling = good for shorts
                usdt_aligned = usdt_d_trend in ["up", "rising", "bullish"]
                total3_aligned = total3_trend in ["down", "falling", "bearish"]
                return usdt_aligned or total3_aligned
            else:  # LONG
                usdt_aligned = usdt_d_trend in ["down", "falling", "bearish"]
                total3_aligned = total3_trend in ["up", "rising", "bullish"]
                return usdt_aligned or total3_aligned

        except Exception as e:
            logger.warning(f"Index confluence check error: {e}")
            return False

    def _check_volume(self, volumes: np.ndarray, threshold: float = 1.2) -> bool:
        """
        Check if recent volume confirms the move.

        Volume should be above average on structure breaks.
        """
        try:
            if len(volumes) < 10:
                return False

            avg_volume = np.mean(volumes[-20:])
            recent_volume = np.mean(volumes[-3:])

            return recent_volume > avg_volume * threshold

        except Exception:
            return False

    def _find_swing_points(
        self,
        data: np.ndarray,
        point_type: str,
        window: int = 3
    ) -> List[Tuple[int, float]]:
        """
        Find swing highs or swing lows in price data.

        Args:
            data: Price array (highs for swing highs, lows for swing lows)
            point_type: "high" or "low"
            window: Lookback window for swing detection

        Returns:
            List of (index, price) tuples
        """
        swing_points = []

        for i in range(window, len(data) - window):
            if point_type == "high":
                if all(data[i] >= data[i-j] for j in range(1, window+1)) and \
                   all(data[i] >= data[i+j] for j in range(1, window+1)):
                    swing_points.append((i, data[i]))
            else:  # low
                if all(data[i] <= data[i-j] for j in range(1, window+1)) and \
                   all(data[i] <= data[i+j] for j in range(1, window+1)):
                    swing_points.append((i, data[i]))

        return swing_points

    def _detect_reversal_candle(
        self,
        ohlcv: List[List],
        direction: str
    ) -> Tuple[bool, MSSSignalType, float]:
        """
        Detect reversal candle patterns (DOJI, Engulfing, Shooting Star).

        Returns:
            (detected, signal_type, confidence)
        """
        if len(ohlcv) < 2:
            return (False, MSSSignalType.NONE, 0.0)

        try:
            last_candle = ohlcv[-1]
            prev_candle = ohlcv[-2]

            o, h, l, c = last_candle[1], last_candle[2], last_candle[3], last_candle[4]
            body = abs(c - o)
            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - l
            candle_range = h - l

            if candle_range == 0:
                return (False, MSSSignalType.NONE, 0.0)

            if direction == "bearish":
                # DOJI at high: Small body, wicks on both sides
                if body / candle_range < 0.1:
                    return (True, MSSSignalType.DOJI_ATH, 0.7)

                # Shooting Star: Long upper wick, small body at bottom
                if upper_wick > body * 2 and lower_wick < body * 0.5:
                    return (True, MSSSignalType.SHOOTING_STAR, 0.75)

                # Bearish Engulfing
                prev_o, prev_c = prev_candle[1], prev_candle[4]
                if prev_c > prev_o and c < o:  # Prev green, current red
                    if c < prev_o and o > prev_c:  # Current engulfs previous
                        return (True, MSSSignalType.ENGULFING, 0.85)

            else:  # bullish
                # Hammer: Long lower wick, small body at top
                if lower_wick > body * 2 and upper_wick < body * 0.5:
                    return (True, MSSSignalType.SHOOTING_STAR, 0.75)  # Inverted

                # Bullish Engulfing
                prev_o, prev_c = prev_candle[1], prev_candle[4]
                if prev_c < prev_o and c > o:  # Prev red, current green
                    if c > prev_o and o < prev_c:
                        return (True, MSSSignalType.ENGULFING, 0.85)

        except Exception as e:
            logger.warning(f"Reversal candle detection error: {e}")

        return (False, MSSSignalType.NONE, 0.0)

    def _build_checklist(self, result: EntryChecklistResult) -> List[ChecklistItem]:
        """Build checklist items from validation results."""
        items = []

        # 1. Trendline Break
        items.append(ChecklistItem(
            name="Trendline Break",
            status=ChecklistStatus.PASSED if result.trendline_break.detected else ChecklistStatus.PENDING,
            detail=f"4H close {'confirmed' if result.trendline_break.detected else 'pending'} "
                   f"({result.trendline_break.touches} touches)" if result.trendline_break.touches else "",
            weight="REQUIRED"
        ))

        # 2. Market Structure Shift
        mss_detail = ""
        if result.mss.detected:
            mss_detail = f"{result.mss.signal_type.value} detected"
        items.append(ChecklistItem(
            name="Market Structure Shift",
            status=ChecklistStatus.PASSED if result.mss.detected else ChecklistStatus.PENDING,
            detail=mss_detail,
            weight="REQUIRED"
        ))

        # 3. Break of Structure
        items.append(ChecklistItem(
            name="Break of Structure",
            status=ChecklistStatus.PASSED if result.bos.detected else ChecklistStatus.PENDING,
            detail=f"Broke {result.bos.swing_level_broken:.4f}" if result.bos.detected else "",
            weight="REQUIRED"
        ))

        # 4. FIB Retrace Zone
        fib_detail = ""
        if result.fib_retrace.current_level is not None:
            fib_detail = f"Current: {result.fib_retrace.current_level:.3f} ({result.fib_retrace.entry_quality})"
        items.append(ChecklistItem(
            name="FIB Retrace Zone",
            status=ChecklistStatus.PASSED if result.fib_retrace.in_zone else ChecklistStatus.PENDING,
            detail=fib_detail,
            weight="REQUIRED",
            value=result.fib_retrace.current_level
        ))

        # 5. Index Confluence
        items.append(ChecklistItem(
            name="Index Confluence",
            status=ChecklistStatus.PASSED if result.indices_aligned else ChecklistStatus.PENDING,
            detail="USDT.D/TOTAL3 aligned" if result.indices_aligned else "Not aligned",
            weight="CONFIRMATION"
        ))

        # 6. Volume Confirmation
        items.append(ChecklistItem(
            name="Volume Check",
            status=ChecklistStatus.PASSED if result.volume_confirmed else ChecklistStatus.PENDING,
            detail="Above average" if result.volume_confirmed else "Below average",
            weight="CONFIRMATION"
        ))

        return items

    def _calculate_score(self, result: EntryChecklistResult) -> EntryChecklistResult:
        """Calculate entry score and recommendation based on checklist."""

        # Count passed conditions
        required_passed = sum(
            1 for item in result.checklist_items
            if item.weight == "REQUIRED" and item.status == ChecklistStatus.PASSED
        )
        confirmation_passed = sum(
            1 for item in result.checklist_items
            if item.weight == "CONFIRMATION" and item.status == ChecklistStatus.PASSED
        )

        required_count = sum(1 for item in result.checklist_items if item.weight == "REQUIRED")

        result.conditions_met = required_passed + confirmation_passed
        result.conditions_required = required_count

        # Find missing conditions
        result.missing_conditions = [
            item.name for item in result.checklist_items
            if item.status == ChecklistStatus.PENDING and item.weight == "REQUIRED"
        ]

        # Calculate score (0-10)
        # Required items: 7 points max (1.75 each)
        # Confirmation items: 3 points max (1.5 each)
        score = (required_passed / required_count * 7) + (confirmation_passed / 2 * 3)
        result.entry_score = round(score, 1)

        # Determine recommendation
        if required_passed == required_count:
            if confirmation_passed >= 1:
                result.recommendation = EntryRecommendation.EXECUTE
                result.reasoning.append("All required conditions met + confirmation aligned")
            else:
                result.recommendation = EntryRecommendation.READY
                result.reasoning.append("All required conditions met, confirmation pending")
        elif required_passed >= required_count - 1:
            result.recommendation = EntryRecommendation.WAIT
            result.reasoning.append(f"Missing: {', '.join(result.missing_conditions)}")
        else:
            result.recommendation = EntryRecommendation.SKIP
            result.reasoning.append("Multiple required conditions missing")

        return result

    def format_checklist_alert(self, result: EntryChecklistResult) -> str:
        """
        Format checklist result for Discord/Telegram alert.

        Returns formatted string per L036 alert format.
        """
        lines = [
            f"ENTRY CHECKLIST - ${result.token_symbol} {result.direction}",
            ""
        ]

        # Add FIB zone if available
        if result.fib_retrace.fib_levels:
            fib = result.fib_retrace
            lines.append(f"Entry Zone: FIB {fib.zone_name} ({fib.entry_quality})")
            lines.append("")

        lines.append("CHECKLIST STATUS:")

        for item in result.checklist_items:
            emoji = "[x]" if item.status == ChecklistStatus.PASSED else "[ ]"
            weight_marker = "" if item.weight == "REQUIRED" else " (confirmation)"
            detail = f" - {item.detail}" if item.detail else ""
            lines.append(f"{emoji} {item.name}{weight_marker}{detail}")

        lines.append("")
        lines.append(f"STATUS: {result.recommendation.value}")

        if result.missing_conditions:
            lines.append(f"Missing: {', '.join(result.missing_conditions)}")

        lines.append(f"Entry Score: {result.entry_score}/10")

        return "\n".join(lines)


# Convenience function for quick validation
def validate_entry(
    ohlcv: List[List],
    token_symbol: str,
    direction: str = "SHORT",
    current_price: Optional[float] = None
) -> EntryChecklistResult:
    """
    Quick entry validation using L036 checklist.

    Args:
        ohlcv: OHLCV data (4H recommended)
        token_symbol: Token symbol
        direction: "SHORT" or "LONG"
        current_price: Optional current price

    Returns:
        EntryChecklistResult
    """
    validator = EntryChecklistValidator()
    return validator.validate(ohlcv, token_symbol, direction, current_price)
