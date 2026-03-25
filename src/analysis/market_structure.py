#!/usr/bin/env python3
"""
Market Structure Analyzer - CHoCH/BOS Detection for Smart Money Concepts

Implements David's market structure analysis approach:
1. CHoCH (Change of Character) - First signal of potential trend reversal
2. BOS (Break of Structure) - Confirmation of market structure shift
3. FIB Retracement - Entry zones at 0.618/0.66 level
4. Dynamic SL - Based on swing high/4H body close, not fixed %

Key Concepts:
- HH = Higher High, HL = Higher Low (bullish structure)
- LH = Lower High, LL = Lower Low (bearish structure)
- CHoCH = First break against trend (e.g., first LH in uptrend)
- BOS = Continuation break confirming new trend

Usage:
    from src.analysis.market_structure import MarketStructureAnalyzer

    analyzer = MarketStructureAnalyzer()
    result = analyzer.analyze("TOKEN", timeframe="4h")

Author: Claude Code
Date: 2025-12-06
Session: David's Market Structure Integration
Session 256: Migrated to src/analysis/market_structure.py
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import ccxt

logger = logging.getLogger(__name__)


@dataclass
class SwingPoint:
    """Represents a swing high or low."""
    type: str  # 'high' or 'low'
    price: float
    index: int
    timestamp: datetime


@dataclass
class StructureBreak:
    """Represents a CHoCH or BOS event."""
    type: str  # 'CHoCH' or 'BOS'
    direction: str  # 'bullish' or 'bearish'
    price: float
    level_broken: float
    timestamp: datetime
    confirmed: bool
    metadata: dict = None  # Session 125: Optional metadata (e.g., trendline_break source)


@dataclass
class FairValueGap:
    """
    Represents a Fair Value Gap (FVG) - David's SMC approach.

    FVG = Imbalance zone where price moved too fast, leaving a gap.
    Price tends to "fill" this gap before continuing the trend.

    Bearish FVG: Created by 3 candles where candle 1's low > candle 3's high
    Bullish FVG: Created by 3 candles where candle 1's high < candle 3's low
    """
    direction: str  # 'bullish' or 'bearish'
    top: float      # Top of FVG zone
    bottom: float   # Bottom of FVG zone
    midpoint: float # 50% of FVG (optimal entry)
    index: int      # Candle index where FVG was created (middle candle)
    timestamp: datetime
    filled: bool    # Whether price has returned to fill the gap
    strength: str   # 'strong', 'moderate', 'weak' based on gap size


@dataclass
class TrendlineAnalysis:
    """
    Represents trendline analysis for shorts - Session 120 (US/Talus Learning).

    David's approach: Draw trendline through swing highs (descending resistance).
    When price has 3+ touches on the trendline and breaks it with volume = strong dump signal.

    Combined with USDT.D↑ + TOTAL3↓ = high conviction short entry.
    """
    detected: bool                    # Whether a valid trendline was found
    direction: str                    # 'descending' (bearish) or 'ascending' (bullish)
    touch_count: int                  # Number of swing points touching the trendline
    slope: float                      # Slope of trendline (negative = descending)
    slope_pct_per_candle: float       # Slope as % per candle (for display)
    current_price: float              # Current price
    trendline_price: float            # Trendline price at current candle
    distance_pct: float               # Distance from current price to trendline (%)
    breakout_imminent: bool           # Price within 2% of trendline
    broken: bool                      # Price has broken through trendline
    strength: str                     # 'strong' (3+), 'moderate' (2), 'weak' (1)
    trendline_points: List[Tuple]     # [(timestamp, price), ...] for David to draw


@dataclass
class LiquiditySweep:
    """
    Represents a Liquidity Sweep - Session 121 (SMC Series Integration).

    From Kaizen SMC Series:
    "Liquidity sweeps are when institutional traders intentionally move the market to
    trigger stop-loss orders or activate pending orders at important price levels
    generally beneath lows or above highs."

    Key insight: After a liquidity sweep, price often reverses sharply. This is where
    institutions fill their orders using retail stop losses as liquidity.
    """
    direction: str          # 'bullish' (sweep below support) or 'bearish' (sweep above resistance)
    sweep_price: float      # The price that swept the liquidity
    liquidity_level: float  # The support/resistance level that was swept
    recovery_price: float   # Price after recovery (confirms sweep vs breakdown)
    sweep_depth_pct: float  # How far price went beyond the level (%)
    timestamp: datetime
    confirmed: bool         # True if price recovered back inside the level
    strength: str           # 'strong' (>2% sweep + full recovery), 'moderate', 'weak'
    candle_index: int       # Index of the sweep candle
    obi_confirmed: bool = False # Session 460: OBI alignment confirmation


@dataclass
class OrderBlock:
    """
    Represents an Order Block (OB) - Session 121 (SMC Series Integration).

    From Kaizen SMC Series:
    "Order Blocks are areas on the price chart where institutional traders have placed
    large orders. These blocks represent significant levels of support and resistance."

    Key characteristics:
    - Sharp price movement following consolidation OR liquidity sweep
    - Engulfing candle pattern (bullish or bearish)
    - Price tends to return to OB before continuing trend
    """
    direction: str          # 'bullish' or 'bearish'
    top: float              # Top of Order Block zone (candle high)
    bottom: float           # Bottom of Order Block zone (candle low)
    midpoint: float         # 50% of OB (optimal entry)
    body_top: float         # Top of candle body (for tighter entry)
    body_bottom: float      # Bottom of candle body
    timestamp: datetime
    candle_index: int       # Index of the OB candle
    strength: str           # 'strong' (engulfing + sweep), 'moderate' (engulfing), 'weak'
    mitigated: bool         # True if price has returned and "used" the OB
    preceded_by_sweep: bool # True if OB formed after liquidity sweep (higher quality)


@dataclass
class EqualLevel:
    """
    Represents Equal Highs (EQH) or Equal Lows (EQL) - Session 121 (SMC Series Integration).

    From Kaizen SMC Series:
    "A sideways trend by relatively equal highs (EQH) and lows (EQL)."

    Key insight: EQH/EQL are LIQUIDITY TARGETS. Institutions will hunt these levels
    because many traders place stops just beyond them.

    For shorts: EQH above current price = potential sweep target before dump
    For longs: EQL below current price = potential sweep target before pump

    Session 122 (PIEVERSE Learning): EQH can indicate CHoCH probability.
    When the last swing high FAILS to make a new high (forms EQH instead),
    this is a potential CHoCH signal - the uptrend is weakening.
    David: "Last swing high failed to making a new high = Equal High = potential CHoCH"
    """
    type: str               # 'EQH' (equal highs) or 'EQL' (equal lows)
    price: float            # The approximate equal level
    touch_count: int        # Number of times price touched this level (2+ = valid)
    touch_prices: List[float]  # Individual touch prices
    tolerance_pct: float    # How close touches are (e.g., within 0.5%)
    timestamp_first: datetime
    timestamp_last: datetime
    is_liquidity_target: bool  # True if this is likely a stop-hunt target
    implies_choch: bool = False  # Session 122: True if EQH at recent high suggests CHoCH


@dataclass
class Equilibrium:
    """
    Represents Equilibrium (50% retracement) - Session 121 (SMC Series Integration).

    From Kaizen SMC Series:
    "Equilibrium is the midpoint (50%) between the low and high in an uptrend or
    the high and low in a downtrend. This 50% level is important, because anything
    in an uptrend below it is considered a discount and anything above is premium."

    Key insight: Institutions buy in discount and sell at premium.
    - Downtrend: Short entries at premium (above 50%)
    - Uptrend: Long entries at discount (below 50%)
    """
    swing_high: float       # The swing high used for calculation
    swing_low: float        # The swing low used for calculation
    equilibrium_price: float  # The 50% level
    current_price: float
    zone: str               # 'premium' (above 50%), 'discount' (below 50%), 'at_equilibrium'
    distance_to_eq_pct: float  # Distance from current price to equilibrium (%)
    direction: str          # 'bearish' (drawn high to low) or 'bullish' (drawn low to high)
    timestamp_high: datetime
    timestamp_low: datetime


@dataclass
class EntryTimingConfirmation:
    """
    Represents Entry Timing Confirmation - Session 125 (CYS Learning).

    David's Dec 14 Feedback: "Right structure (CHoCH) but need RIGHT TIMING."

    Don't enter on CHoCH alone. Wait for 3-step confirmation:
    1. Price retrace to FIB/trendline (0.618-0.786 zone)
    2. Break previous swing low (structure confirmation)
    3. Failure to make new HH (confirms structure shift)

    This prevents premature entries before the structure has fully confirmed.
    Applies to ALL tokens, especially FRESH/MATURE with CHoCH detected.
    """
    choch_detected: bool                    # Whether CHoCH was detected
    step_1_fib_retrace: bool               # Price in FIB 0.618-0.786 zone OR at trendline
    step_2_break_swing_low: bool           # Price broke below previous swing low
    step_3_failed_new_hh: bool             # Failed to make new HH (lower high formed)
    all_confirmed: bool                     # All 3 steps complete = READY TO ENTER
    confirmation_count: int                 # Number of confirmations (0-3)
    current_price: float
    fib_0618: Optional[float]              # FIB 0.618 level
    fib_0786: Optional[float]              # FIB 0.786 level
    previous_swing_low: Optional[float]    # Last swing low that needs to break
    trendline_price: Optional[float]       # Trendline price at current candle
    last_swing_high: Optional[float]       # Last swing high to compare against
    status_text: str                        # Human-readable status for playbook
    next_action: str                        # What to wait for next


class MarketStructureAnalyzer:
    """
    Analyzes market structure using Smart Money Concepts.

    David's Trading Approach:
    1. Wait for CHoCH (first sign of reversal)
    2. Wait for BOS (confirmation)
    3. Enter on FIB retracement (0.618 level)
    4. SL above last swing high (4H body close)
    5. Calculate R:R ratio - must be >= 2:1

    Session 92+ Enhancements (Gemini Review):
    - ATR-based stop loss buffering for TGE volatility
    - Multi-timeframe alignment check
    - Volume/liquidity gating
    """

    MIN_RR_RATIO = 2.0  # Minimum Risk:Reward ratio to enter
    FIB_ENTRY_LEVEL = 0.618  # David uses 0.66, we use 0.618

    # Session 92+: Category-specific volatility multipliers for SL buffer
    # Used when ATR is not available (insufficient data)
    CATEGORY_VOLATILITY = {
        'meme': 0.10,      # 10% buffer - extremely volatile
        'gaming': 0.08,    # 8% buffer - high volatility TGEs
        'ai': 0.07,        # 7% buffer
        'defi': 0.05,      # 5% buffer
        'layer_1': 0.04,   # 4% buffer - more established
        'default': 0.06    # 6% default buffer
    }

    def __init__(self, exchange_id: str = "mexc"):
        """Initialize with exchange for OHLCV data."""
        exchanges = {
            'binance': ccxt.binance,
            'mexc': ccxt.mexc,
            'gate': ccxt.gateio,
            'bybit': ccxt.bybit,
        }
        exchange_class = exchanges.get(exchange_id, ccxt.mexc)
        self.exchange = exchange_class({'enableRateLimit': True})

    def analyze(self, token_symbol: str, timeframe: str = "4h", category: str = None) -> Dict:
        """
        Full market structure analysis.

        Args:
            token_symbol: Token to analyze (e.g., "POWER")
            timeframe: Candle timeframe (default 4h for David's approach)
            category: Token category for R:R target calculation (Session 116)
                      e.g., "Gaming", "Layer_1", "L2", "DeFi", "AI", "Meme"

        Returns:
            Dict with:
                - current_structure: 'bullish', 'bearish', or 'ranging'
                - swing_highs: List of recent swing high prices
                - swing_lows: List of recent swing low prices
                - choch_detected: True if CHoCH occurred
                - bos_detected: True if BOS confirmed
                - fib_levels: Dict with 0.382, 0.5, 0.618, 0.786 levels
                - entry_zone: Tuple (low, high) for 0.618 retracement
                - dynamic_sl: Stop loss based on swing structure
                - rr_ratio: Risk:Reward ratio at current price
                - ready_for_entry: True if all conditions met
        """
        logger.info(f"Analyzing market structure for {token_symbol} on {timeframe}...")

        try:
            # Fetch OHLCV data
            ohlcv = self._fetch_ohlcv(token_symbol, timeframe, limit=100)

            if not ohlcv or len(ohlcv) < 20:
                return self._error_result(token_symbol, "Insufficient OHLCV data")

            return self.analyze_from_ohlcv(ohlcv, timeframe=timeframe, category=category,
                                           token_symbol=token_symbol)

        except Exception as e:
            logger.error(f"Market structure analysis failed: {e}")
            return self._error_result(token_symbol, str(e))

    def analyze_from_ohlcv(self, ohlcv: list, timeframe: str = "4h",
                           category: str = None, token_symbol: str = "UNKNOWN",
                           obi: float = None) -> Dict:
        """
        Full market structure analysis from pre-fetched OHLCV data.

        Runs the same analysis pipeline as analyze() but skips the OHLCV fetch,
        allowing callers that already have candle data to avoid redundant API calls.

        Args:
            ohlcv: Pre-fetched OHLCV data in CCXT format: list of
                   [timestamp, open, high, low, close, volume]
            timeframe: Candle timeframe (default 4h)
            category: Token category for R:R target calculation (Session 116)
            token_symbol: Token symbol for labeling (default "UNKNOWN")
            obi: Optional Order Book Imbalance (-1.0 to +1.0) (Session 460)

        Returns:
            Same dict structure as analyze().
        """
        try:
            if not ohlcv or len(ohlcv) < 20:
                return self._error_result(token_symbol, "Insufficient OHLCV data")

            # 1. Identify swing points
            swing_highs, swing_lows = self._find_swing_points(ohlcv)

            if len(swing_highs) < 2 or len(swing_lows) < 2:
                return self._error_result(token_symbol, "Not enough swing points for structure analysis")

            # 2. Determine current structure (HH/HL vs LH/LL)
            structure, structure_sequence = self._determine_structure(swing_highs, swing_lows)

            # 3a. Session 125 (RAVE Learning): Analyze trendline BEFORE CHoCH detection
            # David's pattern: Trendline break (3+ touches) can indicate CHoCH
            current_price = ohlcv[-1][4]
            trendline = self._analyze_trendline(ohlcv, swing_highs, swing_lows, current_price)

            # 3b. Calculate FIB levels BEFORE CHoCH (needed for trendline+FIB confluence)
            # Learning 011: Pass ohlcv for TGE detection (uses ATH→ATL if < 7 days old)
            fib_levels = self._calculate_fib_levels(swing_highs, swing_lows, ohlcv=ohlcv)

            # 3c. Detect CHoCH and BOS (improved with structure sequence - Learning 008)
            # Session 125: Pass trendline and fib_levels for trendline break detection
            choch = self._detect_choch(ohlcv, swing_highs, swing_lows, structure, structure_sequence,
                                      trendline=trendline, fib_levels=fib_levels)
            bos = self._detect_bos(ohlcv, swing_highs, swing_lows, structure)

            # 4. Detect Fair Value Gaps (David's SMC approach - Session 94)
            all_fvgs = self._find_fair_value_gaps(ohlcv, current_price)
            bearish_fvgs = [fvg for fvg in all_fvgs if fvg.direction == 'bearish']
            bullish_fvgs = [fvg for fvg in all_fvgs if fvg.direction == 'bullish']

            # Get nearest unfilled FVG for short entry (bearish FVG above price)
            nearest_bearish_fvg = self._get_nearest_unfilled_fvg(all_fvgs, 'bearish', current_price)

            if nearest_bearish_fvg:
                logger.info(f"   FVG Entry Zone: ${nearest_bearish_fvg.bottom:.4f}-${nearest_bearish_fvg.top:.4f} "
                           f"({nearest_bearish_fvg.strength})")

            # Session 297: Get nearest unfilled FVG for LONG entry (bullish FVG below price)
            nearest_bullish_fvg = self._get_nearest_unfilled_fvg(all_fvgs, 'bullish', current_price)

            if nearest_bullish_fvg:
                logger.info(f"   Bullish FVG Entry Zone: ${nearest_bullish_fvg.bottom:.4f}-${nearest_bullish_fvg.top:.4f} "
                           f"({nearest_bullish_fvg.strength})")

            # 5. Session 121 SMC Series - Liquidity Sweeps, Order Blocks, EQH/EQL, Equilibrium
            liquidity_sweeps = self._detect_liquidity_sweeps(ohlcv, swing_highs, swing_lows, obi=obi)
            
            # Phase 1: CISD detection after sweeps
            cisd = self._detect_cisd(ohlcv, liquidity_sweeps) if liquidity_sweeps else None
            
            order_blocks = self._detect_order_blocks(ohlcv, liquidity_sweeps, current_price)
            equal_levels = self._detect_equal_levels(swing_highs, swing_lows)
            equilibrium = self._calculate_equilibrium(swing_highs, swing_lows, current_price, structure)

            # 5b. Session 125: Check Entry Timing Confirmation (CYS Learning)
            # David's 3-step confirmation: FIB retrace + break swing low + fail to make new HH
            entry_timing = self._check_entry_timing_confirmation(
                choch, fib_levels, swing_highs, swing_lows, current_price, trendline
            )

            # 5. Determine entry zone (0.618 retracement)
            entry_zone = self._calculate_entry_zone(fib_levels, structure)

            # 6. Calculate dynamic SL (above last swing high for shorts)
            # Session 92+: Pass ohlcv for ATR calculation
            dynamic_sl, sl_metadata = self._calculate_dynamic_sl(
                swing_highs, swing_lows, structure, current_price, ohlcv=ohlcv
            )

            # Session 92+: Calculate ATR for volatility reference
            atr = self._calculate_atr(ohlcv)

            # Session 92+: Calculate 24h volume for liquidity gate
            volume_24h = self._calculate_24h_volume(ohlcv, timeframe)

            # 7. Calculate R:R ratio
            # Session 116: Pass category for David's methodology (category-based targets)
            target_price = self._calculate_target(current_price, structure, fib_levels, category)
            rr_ratio = self._calculate_rr_ratio(current_price, dynamic_sl, target_price, structure)

            # 8. Check if ready for entry
            ready_for_entry = self._check_entry_conditions(
                choch, bos, current_price, entry_zone, rr_ratio, structure
            )

            # 9. Last candle analysis
            last_candle = self._analyze_last_candle(ohlcv)

            # Session 92+: Liquidity check for entry gate
            min_volume_threshold = 500_000  # $500k minimum (Gemini recommendation)
            liquidity_healthy = volume_24h and volume_24h >= min_volume_threshold

            result = {
                'token_symbol': token_symbol,
                'timeframe': timeframe,
                'timestamp': datetime.utcnow().isoformat(),
                'current_price': current_price,

                # Structure Analysis
                'current_structure': structure,
                'structure_sequence': structure_sequence,
                'swing_highs': [sh.price for sh in swing_highs[-3:]],
                'swing_lows': [sl.price for sl in swing_lows[-3:]],

                # CHoCH/BOS Detection
                'choch_detected': choch is not None,
                'choch_details': {
                    'type': choch.type if choch else None,
                    'direction': choch.direction if choch else None,
                    'price': choch.price if choch else None,
                    'level_broken': choch.level_broken if choch else None,
                } if choch else None,
                'bos_detected': bos is not None,
                'bos_details': {
                    'type': bos.type if bos else None,
                    'direction': bos.direction if bos else None,
                    'price': bos.price if bos else None,
                } if bos else None,
                'cisd_detected': cisd is not None,
                'cisd_details': {
                    'type': cisd.type if cisd else None,
                    'direction': cisd.direction if cisd else None,
                    'price': cisd.price if cisd else None,
                    'level_broken': cisd.level_broken if cisd else None,
                    'timestamp': cisd.timestamp.isoformat() if cisd and cisd.timestamp else None,
                    'metadata': cisd.metadata if cisd else None,
                } if cisd else None,

                # Entry Levels
                'fib_levels': fib_levels,
                'entry_zone': entry_zone,
                'in_entry_zone': entry_zone[0] <= current_price <= entry_zone[1] if entry_zone else False,

                # Fair Value Gaps (David's SMC approach - Session 94)
                'fvg_count': len(all_fvgs),
                'bearish_fvg_count': len(bearish_fvgs),
                'bullish_fvg_count': len(bullish_fvgs),
                'nearest_bearish_fvg': {
                    'top': nearest_bearish_fvg.top,
                    'bottom': nearest_bearish_fvg.bottom,
                    'midpoint': nearest_bearish_fvg.midpoint,
                    'strength': nearest_bearish_fvg.strength,
                    'filled': nearest_bearish_fvg.filled,
                    'timestamp': nearest_bearish_fvg.timestamp.isoformat(),
                } if nearest_bearish_fvg else None,
                'fvg_entry_zone': (nearest_bearish_fvg.bottom, nearest_bearish_fvg.top) if nearest_bearish_fvg else None,
                'in_fvg_zone': (nearest_bearish_fvg.bottom <= current_price <= nearest_bearish_fvg.top) if nearest_bearish_fvg else False,

                # Session 297: Bullish FVG for LONG entries (P1.1A - LONG System Parity)
                'nearest_bullish_fvg': {
                    'top': nearest_bullish_fvg.top,
                    'bottom': nearest_bullish_fvg.bottom,
                    'midpoint': nearest_bullish_fvg.midpoint,
                    'strength': nearest_bullish_fvg.strength,
                    'filled': nearest_bullish_fvg.filled,
                    'timestamp': nearest_bullish_fvg.timestamp.isoformat(),
                } if nearest_bullish_fvg else None,
                'bullish_fvg_entry_zone': (nearest_bullish_fvg.bottom, nearest_bullish_fvg.top) if nearest_bullish_fvg else None,
                'in_bullish_fvg_zone': (nearest_bullish_fvg.bottom <= current_price <= nearest_bullish_fvg.top) if nearest_bullish_fvg else False,

                # Trendline Analysis (Session 120 - US/Talus Learning)
                'trendline_detected': trendline.detected if trendline else False,
                'trendline': {
                    'detected': trendline.detected,
                    'direction': trendline.direction,
                    'touch_count': trendline.touch_count,
                    'strength': trendline.strength,
                    'slope_pct_per_candle': trendline.slope_pct_per_candle,
                    'trendline_price': trendline.trendline_price,
                    'distance_pct': trendline.distance_pct,
                    'breakout_imminent': trendline.breakout_imminent,
                    'broken': trendline.broken,
                    'trendline_points': trendline.trendline_points,
                } if trendline else None,

                # Session 121: SMC Series - Liquidity Sweeps, Order Blocks, EQH/EQL, Equilibrium
                'liquidity_sweeps': [
                    {
                        'direction': s.direction,
                        'sweep_price': s.sweep_price,
                        'liquidity_level': s.liquidity_level,
                        'recovery_price': s.recovery_price,
                        'sweep_depth_pct': s.sweep_depth_pct,
                        'strength': s.strength,
                        'confirmed': s.confirmed,
                        'obi_confirmed': s.obi_confirmed,
                        'timestamp': s.timestamp.isoformat(),
                    } for s in liquidity_sweeps[:3]  # Top 3 most recent
                ] if liquidity_sweeps else [],
                'bearish_sweeps_count': len([s for s in liquidity_sweeps if s.direction == 'bearish']),
                'bullish_sweeps_count': len([s for s in liquidity_sweeps if s.direction == 'bullish']),
                'recent_bearish_sweep': next(
                    (s for s in liquidity_sweeps if s.direction == 'bearish'), None
                ) is not None,  # True if there's a recent bearish sweep (good for shorts)

                # Session 297: Bullish sweep for LONG entries (P1.1A - LONG System Parity)
                'recent_bullish_sweep': next(
                    (s for s in liquidity_sweeps if s.direction == 'bullish'), None
                ) is not None,  # True if there's a recent bullish sweep (good for longs - swept lows)

                'order_blocks': [
                    {
                        'direction': ob.direction,
                        'top': ob.top,
                        'bottom': ob.bottom,
                        'midpoint': ob.midpoint,
                        'body_top': ob.body_top,
                        'body_bottom': ob.body_bottom,
                        'strength': ob.strength,
                        'mitigated': ob.mitigated,
                        'preceded_by_sweep': ob.preceded_by_sweep,
                        'timestamp': ob.timestamp.isoformat(),
                    } for ob in order_blocks[:3]  # Top 3 most recent
                ] if order_blocks else [],
                'bearish_ob_count': len([ob for ob in order_blocks if ob.direction == 'bearish']),
                'bullish_ob_count': len([ob for ob in order_blocks if ob.direction == 'bullish']),
                'unmitigated_bearish_ob': (lambda ob: {
                    'top': ob.top,
                    'bottom': ob.bottom,
                    'midpoint': ob.midpoint,
                    'strength': ob.strength,
                    'preceded_by_sweep': ob.preceded_by_sweep,
                } if ob else None)(next(
                    (ob for ob in order_blocks if ob.direction == 'bearish' and not ob.mitigated), None
                )),  # Entry zone for shorts

                # Session 297: Bullish order block for LONG entries (P1.1A - LONG System Parity)
                'unmitigated_bullish_ob': (lambda ob: {
                    'top': ob.top,
                    'bottom': ob.bottom,
                    'midpoint': ob.midpoint,
                    'strength': ob.strength,
                    'preceded_by_sweep': ob.preceded_by_sweep,
                } if ob else None)(next(
                    (ob for ob in order_blocks if ob.direction == 'bullish' and not ob.mitigated), None
                )),  # Entry zone for longs

                'equal_levels': [
                    {
                        'type': el.type,
                        'price': el.price,
                        'touch_count': el.touch_count,
                        'is_liquidity_target': el.is_liquidity_target,
                        'implies_choch': el.implies_choch,  # Session 122: EQH can signal CHoCH
                    } for el in equal_levels
                ] if equal_levels else [],
                'eqh_count': len([el for el in equal_levels if el.type == 'EQH']),
                'eql_count': len([el for el in equal_levels if el.type == 'EQL']),
                'eqh_above_price': any(
                    el.type == 'EQH' and el.price > current_price for el in equal_levels
                ),  # True = liquidity target above for shorts

                # Session 297: EQL below price for LONG entries (P1.1A - LONG System Parity)
                'eql_below_price': any(
                    el.type == 'EQL' and el.price < current_price for el in equal_levels
                ),  # True = liquidity target below (swept lows = bullish setup)

                # Session 122 (PIEVERSE Learning): EQH implies CHoCH when last swing high fails new high
                'eqh_implies_choch': any(
                    el.type == 'EQH' and el.implies_choch for el in equal_levels
                ),  # True = EQH at recent high suggests bearish CHoCH

                # Session 297: EQL implies bullish CHoCH (P1.1A - LONG System Parity)
                'eql_implies_bullish_choch': any(
                    el.type == 'EQL' and el.implies_choch for el in equal_levels
                ),  # True = EQL at recent low suggests bullish CHoCH (swept lows + reclaim)

                'equilibrium': {
                    'swing_high': equilibrium.swing_high,
                    'swing_low': equilibrium.swing_low,
                    'equilibrium_price': equilibrium.equilibrium_price,
                    'zone': equilibrium.zone,
                    'distance_to_eq_pct': equilibrium.distance_to_eq_pct,
                    'direction': equilibrium.direction,
                } if equilibrium else None,
                'in_premium_zone': equilibrium.zone == 'premium' if equilibrium else False,  # Good for shorts
                'in_discount_zone': equilibrium.zone == 'discount' if equilibrium else False,  # Good for longs

                # Session 440: Volume-based order blocks (Phase 5 derivatives intelligence)
                'volume_order_blocks': self.analyze_order_blocks(ohlcv),

                # Session 125: Entry Timing Confirmation (CYS Learning)
                'entry_timing': {
                    'choch_detected': entry_timing.choch_detected,
                    'step_1_fib_retrace': entry_timing.step_1_fib_retrace,
                    'step_2_break_swing_low': entry_timing.step_2_break_swing_low,
                    'step_3_failed_new_hh': entry_timing.step_3_failed_new_hh,
                    'all_confirmed': entry_timing.all_confirmed,
                    'confirmation_count': entry_timing.confirmation_count,
                    'status_text': entry_timing.status_text,
                    'next_action': entry_timing.next_action,
                    'fib_0618': entry_timing.fib_0618,
                    'fib_0786': entry_timing.fib_0786,
                    'previous_swing_low': entry_timing.previous_swing_low,
                    'trendline_price': entry_timing.trendline_price,
                    'last_swing_high': entry_timing.last_swing_high,
                } if entry_timing else None,

                # Risk Management
                'dynamic_sl': dynamic_sl,
                'sl_metadata': sl_metadata,  # Session 92+: SL calculation details
                'target_price': target_price,
                'rr_ratio': rr_ratio,
                'rr_meets_minimum': rr_ratio >= self.MIN_RR_RATIO if rr_ratio else False,

                # Session 92+: Volatility & Liquidity (Gemini enhancements)
                'atr': atr,
                'atr_pct': round((atr / current_price) * 100, 2) if atr and current_price > 0 else None,
                'volume_24h_usd': volume_24h,
                'liquidity_healthy': liquidity_healthy,
                'liquidity_warning': not liquidity_healthy if volume_24h else 'NO_DATA',

                # Candle Analysis
                'last_candle_color': last_candle['color'],
                'last_candle_body_close': last_candle['body_close'],

                # Entry Decision
                'ready_for_entry': ready_for_entry,
                'entry_conditions_met': self._list_conditions_met(
                    choch, bos, current_price, entry_zone, rr_ratio, structure
                ),
            }

            logger.info(f"Structure: {structure}, CHoCH: {choch is not None}, BOS: {bos is not None}, "
                       f"R:R: {rr_ratio:.2f}:1, Ready: {ready_for_entry}")

            return result

        except Exception as e:
            logger.error(f"Market structure analysis failed: {e}")
            return self._error_result(token_symbol, str(e))

    def _fetch_ohlcv(self, token_symbol: str, timeframe: str, limit: int = 100) -> Optional[List]:
        """Fetch OHLCV data from exchange.

        Session 121: Prioritize perpetual markets (TOKEN/USDT:USDT) for shorting,
        fall back to spot if perp not available.
        """
        try:
            # Session 121: Try perpetual first (what we actually trade for shorts)
            # then spot as fallback
            pairs = [
                f"{token_symbol}/USDT:USDT",  # Perpetual (priority for shorts)
                f"{token_symbol}/USDT",        # Spot
                f"{token_symbol}/USD",         # Alternative spot
            ]

            for pair in pairs:
                try:
                    ohlcv = self.exchange.fetch_ohlcv(pair, timeframe, limit=limit)
                    if ohlcv:
                        market_type = "PERP" if ":USDT" in pair else "SPOT"
                        logger.info(f"   Fetched {len(ohlcv)} candles for {pair} ({timeframe}) [{market_type}]")
                        return ohlcv
                except Exception:
                    continue

            logger.warning(f"   No OHLCV data found for {token_symbol}")
            return None

        except Exception as e:
            logger.error(f"   OHLCV fetch error: {e}")
            return None

    def _find_fair_value_gaps(self, ohlcv: List, current_price: float) -> List[FairValueGap]:
        """
        Find Fair Value Gaps (FVG) - David's SMC approach.

        David's STABLE Playbook (Dec 8, 2025):
        "I drew the bearish fair value gap on 4 hours... usually, we can target more likely
        the bottom... FVG going be like a magnet, so this is what we can trade from this situation."

        FVG Detection Rules:
        - Bearish FVG: 3-candle pattern where candle 1's low > candle 3's high
          (gap down - unfilled orders above, price will retrace to fill)
        - Bullish FVG: 3-candle pattern where candle 1's high < candle 3's low
          (gap up - unfilled orders below, price will retrace to fill)

        Args:
            ohlcv: List of [timestamp, open, high, low, close, volume]
            current_price: Current price to check if FVG is filled

        Returns:
            List of FairValueGap objects, sorted by recency (most recent first)
        """
        fvgs = []

        if not ohlcv or len(ohlcv) < 3:
            return fvgs

        for i in range(2, len(ohlcv)):
            candle_1 = ohlcv[i - 2]  # First candle
            candle_2 = ohlcv[i - 1]  # Middle candle (FVG reference)
            candle_3 = ohlcv[i]      # Third candle

            # Extract OHLC: [timestamp, open, high, low, close, volume]
            c1_low = candle_1[3]
            c1_high = candle_1[2]
            c3_high = candle_3[2]
            c3_low = candle_3[3]

            # Check for Bearish FVG (gap down)
            # Candle 1's low > Candle 3's high = gap between them
            if c1_low > c3_high:
                fvg_top = c1_low
                fvg_bottom = c3_high
                gap_size = fvg_top - fvg_bottom
                midpoint = (fvg_top + fvg_bottom) / 2

                # Check if filled (price has returned to the gap)
                filled = current_price >= fvg_bottom

                # Determine strength based on gap size relative to price
                gap_pct = (gap_size / midpoint) * 100
                if gap_pct >= 3.0:
                    strength = 'strong'
                elif gap_pct >= 1.5:
                    strength = 'moderate'
                else:
                    strength = 'weak'

                fvgs.append(FairValueGap(
                    direction='bearish',
                    top=round(fvg_top, 6),
                    bottom=round(fvg_bottom, 6),
                    midpoint=round(midpoint, 6),
                    index=i - 1,  # Middle candle index
                    timestamp=datetime.fromtimestamp(candle_2[0] / 1000),
                    filled=filled,
                    strength=strength
                ))

            # Check for Bullish FVG (gap up)
            # Candle 1's high < Candle 3's low = gap between them
            elif c1_high < c3_low:
                fvg_top = c3_low
                fvg_bottom = c1_high
                gap_size = fvg_top - fvg_bottom
                midpoint = (fvg_top + fvg_bottom) / 2

                # Check if filled (price has returned to the gap)
                filled = current_price <= fvg_top

                # Determine strength
                gap_pct = (gap_size / midpoint) * 100
                if gap_pct >= 3.0:
                    strength = 'strong'
                elif gap_pct >= 1.5:
                    strength = 'moderate'
                else:
                    strength = 'weak'

                fvgs.append(FairValueGap(
                    direction='bullish',
                    top=round(fvg_top, 6),
                    bottom=round(fvg_bottom, 6),
                    midpoint=round(midpoint, 6),
                    index=i - 1,
                    timestamp=datetime.fromtimestamp(candle_2[0] / 1000),
                    filled=filled,
                    strength=strength
                ))

        # Sort by recency (most recent first)
        fvgs.sort(key=lambda x: x.index, reverse=True)

        return fvgs

    def _get_nearest_unfilled_fvg(
        self, fvgs: List[FairValueGap], direction: str, current_price: float
    ) -> Optional[FairValueGap]:
        """
        Get the nearest unfilled FVG in the specified direction.

        David's approach: Price acts like a magnet to FVG zones.
        For shorts, we want bearish FVGs above current price (price will retrace up to fill).

        Args:
            fvgs: List of FairValueGap objects
            direction: 'bearish' or 'bullish'
            current_price: Current price

        Returns:
            Nearest unfilled FVG or None
        """
        matching_fvgs = [
            fvg for fvg in fvgs
            if fvg.direction == direction and not fvg.filled
        ]

        if not matching_fvgs:
            return None

        # For bearish FVGs (shorts), find the one closest above current price
        if direction == 'bearish':
            above_price = [fvg for fvg in matching_fvgs if fvg.bottom > current_price]
            if above_price:
                # Return the lowest one (nearest to current price)
                return min(above_price, key=lambda x: x.bottom)

        # For bullish FVGs (longs), find the one closest below current price
        elif direction == 'bullish':
            below_price = [fvg for fvg in matching_fvgs if fvg.top < current_price]
            if below_price:
                # Return the highest one (nearest to current price)
                return max(below_price, key=lambda x: x.top)

        return None

    def _find_swing_points(self, ohlcv: List, lookback: int = 3) -> Tuple[List[SwingPoint], List[SwingPoint]]:
        """
        Find swing highs and lows using left/right confirmation.

        A swing high requires higher prices on both sides.
        A swing low requires lower prices on both sides.
        """
        swing_highs = []
        swing_lows = []

        for i in range(lookback, len(ohlcv) - lookback):
            high = ohlcv[i][2]
            low = ohlcv[i][3]

            # Check for swing high
            is_swing_high = True
            for j in range(1, lookback + 1):
                if ohlcv[i - j][2] >= high or ohlcv[i + j][2] >= high:
                    is_swing_high = False
                    break

            if is_swing_high:
                swing_highs.append(SwingPoint(
                    type='high',
                    price=high,
                    index=i,
                    timestamp=datetime.fromtimestamp(ohlcv[i][0] / 1000)
                ))

            # Check for swing low
            is_swing_low = True
            for j in range(1, lookback + 1):
                if ohlcv[i - j][3] <= low or ohlcv[i + j][3] <= low:
                    is_swing_low = False
                    break

            if is_swing_low:
                swing_lows.append(SwingPoint(
                    type='low',
                    price=low,
                    index=i,
                    timestamp=datetime.fromtimestamp(ohlcv[i][0] / 1000)
                ))

        return swing_highs, swing_lows

    def _analyze_trendline(
        self, ohlcv: List, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
        current_price: float, tolerance_pct: float = 2.0
    ) -> Optional[TrendlineAnalysis]:
        """
        Analyze trendline for shorts - Session 120 (US/Talus Learning).

        David's approach: Draw descending resistance trendline through swing highs.
        When price has 3+ touches and approaches/breaks the trendline = strong dump signal.

        Algorithm:
        1. Take last 5-10 swing highs
        2. Fit linear regression line through them
        3. Count points within tolerance_pct of the line
        4. 3+ touches = strong trendline
        5. Check if price is approaching/breaking trendline

        Args:
            ohlcv: OHLCV data
            swing_highs: List of swing high points
            swing_lows: List of swing low points (for bullish trendlines)
            current_price: Current price
            tolerance_pct: % tolerance for counting touches (default 2%)

        Returns:
            TrendlineAnalysis object or None if insufficient data
        """
        if not ohlcv or len(ohlcv) < 10:
            return None

        # For shorts, analyze descending resistance (swing highs)
        # Need at least 3 swing highs to fit a trendline
        if len(swing_highs) < 3:
            return None

        # Use last 7 swing highs for trendline analysis
        recent_highs = swing_highs[-7:]

        # Extract x (index) and y (price) for linear regression
        x_values = [sh.index for sh in recent_highs]
        y_values = [sh.price for sh in recent_highs]

        # Simple linear regression: y = mx + b
        n = len(x_values)
        sum_x = sum(x_values)
        sum_y = sum(y_values)
        sum_xy = sum(x * y for x, y in zip(x_values, y_values))
        sum_x2 = sum(x * x for x in x_values)

        # Calculate slope (m) and intercept (b)
        denominator = n * sum_x2 - sum_x * sum_x
        if denominator == 0:
            return None

        slope = (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / n

        # For shorts, we want descending trendline (negative slope)
        direction = 'descending' if slope < 0 else 'ascending'

        # Count touches within tolerance
        touch_count = 0
        touching_points = []

        for sh in recent_highs:
            trendline_at_point = slope * sh.index + intercept
            distance_pct = abs(sh.price - trendline_at_point) / trendline_at_point * 100

            if distance_pct <= tolerance_pct:
                touch_count += 1
                touching_points.append((sh.timestamp, sh.price))

        # Calculate current trendline price (at last candle index)
        current_index = len(ohlcv) - 1
        trendline_price = slope * current_index + intercept

        # Distance from current price to trendline (as %)
        distance_pct = ((trendline_price - current_price) / trendline_price) * 100

        # Check if breakout is imminent (price within 2% of trendline)
        breakout_imminent = abs(distance_pct) <= 2.0

        # Check if trendline is broken
        # For descending resistance: broken if price > trendline
        broken = current_price > trendline_price if direction == 'descending' else current_price < trendline_price

        # Determine strength
        if touch_count >= 3:
            strength = 'strong'
        elif touch_count == 2:
            strength = 'moderate'
        else:
            strength = 'weak'

        # Calculate slope as % per candle for display
        avg_price = sum(y_values) / len(y_values)
        slope_pct_per_candle = (slope / avg_price) * 100 if avg_price > 0 else 0

        # Format trendline points for David to draw on TradingView
        trendline_points = []
        if touching_points:
            # Start point (first touch)
            first_touch = touching_points[0]
            trendline_points.append((first_touch[0].strftime('%Y-%m-%d %H:%M'), round(first_touch[1], 6)))

            # End point (extrapolate to current candle)
            current_ts = datetime.fromtimestamp(ohlcv[-1][0] / 1000)
            trendline_points.append((current_ts.strftime('%Y-%m-%d %H:%M'), round(trendline_price, 6)))

        # Only return if we have a valid descending trendline (for shorts)
        if direction != 'descending' or touch_count < 2:
            return TrendlineAnalysis(
                detected=False,
                direction=direction,
                touch_count=touch_count,
                slope=round(slope, 8),
                slope_pct_per_candle=round(slope_pct_per_candle, 4),
                current_price=current_price,
                trendline_price=round(trendline_price, 6),
                distance_pct=round(distance_pct, 2),
                breakout_imminent=breakout_imminent,
                broken=broken,
                strength=strength,
                trendline_points=trendline_points
            )

        logger.info(f"   Trendline: {direction} with {touch_count} touches ({strength}), "
                   f"distance: {distance_pct:.1f}%, breakout: {'IMMINENT' if breakout_imminent else 'NO'}")

        return TrendlineAnalysis(
            detected=True,
            direction=direction,
            touch_count=touch_count,
            slope=round(slope, 8),
            slope_pct_per_candle=round(slope_pct_per_candle, 4),
            current_price=current_price,
            trendline_price=round(trendline_price, 6),
            distance_pct=round(distance_pct, 2),
            breakout_imminent=breakout_imminent,
            broken=broken,
            strength=strength,
            trendline_points=trendline_points
        )

    def _detect_liquidity_sweeps(
        self, ohlcv: List, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
        lookback: int = 20, obi: float = None
    ) -> List[LiquiditySweep]:
        """
        Detect Liquidity Sweeps - Session 121 (SMC Series Integration).
        
        Session 460: OBI confirmation filters ICT sweeps.
        """
        sweeps = []

        if not ohlcv or len(ohlcv) < 10:
            return sweeps

        recent_candles = ohlcv[-lookback:] if len(ohlcv) >= lookback else ohlcv
        current_idx = len(ohlcv) - 1

        # Check for sweeps below swing lows (bullish sweeps)
        for sl in swing_lows[-5:]:  # Check last 5 swing lows
            for i, candle in enumerate(recent_candles):
                candle_global_idx = (len(ohlcv) - len(recent_candles)) + i
                # candle = [timestamp, open, high, low, close, volume]
                low = candle[3]
                close = candle[4]
                open_price = candle[1]

                # Check if wick went below swing low but body closed above
                if low < sl.price and min(open_price, close) > sl.price:
                    sweep_depth_pct = ((sl.price - low) / sl.price) * 100
                    recovery_price = close

                    # Determine strength
                    if sweep_depth_pct > 2.0:
                        strength = 'strong'
                    elif sweep_depth_pct > 1.0:
                        strength = 'moderate'
                    else:
                        strength = 'weak'
                        
                    # OBI Confirmation (Session 460)
                    # Bullish sweep confirmed if OBI is positive
                    obi_confirmed = False
                    if obi is not None and (current_idx - candle_global_idx) <= 3:
                        if obi >= 0.15:
                            obi_confirmed = True

                    sweeps.append(LiquiditySweep(
                        direction='bullish',
                        sweep_price=low,
                        liquidity_level=sl.price,
                        recovery_price=recovery_price,
                        sweep_depth_pct=round(sweep_depth_pct, 2),
                        timestamp=datetime.fromtimestamp(candle[0] / 1000),
                        confirmed=True,
                        strength=strength,
                        candle_index=candle_global_idx,
                        obi_confirmed=obi_confirmed
                    ))

        # Check for sweeps above swing highs (bearish sweeps)
        for sh in swing_highs[-5:]:  # Check last 5 swing highs
            for i, candle in enumerate(recent_candles):
                candle_global_idx = (len(ohlcv) - len(recent_candles)) + i
                high = candle[2]
                close = candle[4]
                open_price = candle[1]

                # Check if wick went above swing high but body closed below
                if high > sh.price and max(open_price, close) < sh.price:
                    sweep_depth_pct = ((high - sh.price) / sh.price) * 100
                    recovery_price = close

                    # Determine strength
                    if sweep_depth_pct > 2.0:
                        strength = 'strong'
                    elif sweep_depth_pct > 1.0:
                        strength = 'moderate'
                    else:
                        strength = 'weak'
                        
                    # OBI Confirmation (Session 460)
                    # Bearish sweep confirmed if OBI is negative
                    obi_confirmed = False
                    if obi is not None and (current_idx - candle_global_idx) <= 3:
                        if obi <= -0.15:
                            obi_confirmed = True

                    sweeps.append(LiquiditySweep(
                        direction='bearish',
                        sweep_price=high,
                        liquidity_level=sh.price,
                        recovery_price=recovery_price,
                        sweep_depth_pct=round(sweep_depth_pct, 2),
                        timestamp=datetime.fromtimestamp(candle[0] / 1000),
                        confirmed=True,
                        strength=strength,
                        candle_index=candle_global_idx,
                        obi_confirmed=obi_confirmed
                    ))

        # Sort by timestamp (most recent first)
        sweeps.sort(key=lambda x: x.timestamp, reverse=True)
        return sweeps

    def _detect_order_blocks(
        self, ohlcv: List, sweeps: List[LiquiditySweep], current_price: float,
        lookback: int = 30
    ) -> List[OrderBlock]:
        """
        Detect Order Blocks (OB) - Session 121 (SMC Series Integration).

        From Kaizen SMC Series:
        "Order Blocks are areas where institutional traders placed large orders.
        Key characteristics: sharp price movement following consolidation OR liquidity sweep."

        Algorithm:
        1. Look for engulfing candles (bullish or bearish)
        2. Check if preceded by liquidity sweep (higher quality OB)
        3. Mark zone from candle high to low
        4. Check if OB has been mitigated (price returned to it)

        Args:
            ohlcv: OHLCV data
            sweeps: List of detected liquidity sweeps
            current_price: Current price for mitigation check
            lookback: Number of candles to analyze

        Returns:
            List of OrderBlock objects
        """
        order_blocks = []

        if not ohlcv or len(ohlcv) < 5:
            return order_blocks

        recent_candles = ohlcv[-lookback:] if len(ohlcv) >= lookback else ohlcv

        # Get sweep timestamps for checking if OB was preceded by sweep
        sweep_indices = {s.candle_index for s in sweeps}

        for i in range(1, len(recent_candles)):
            curr = recent_candles[i]
            prev = recent_candles[i - 1]

            # curr = [timestamp, open, high, low, close, volume]
            curr_open, curr_high, curr_low, curr_close = curr[1], curr[2], curr[3], curr[4]
            prev_open, prev_high, prev_low, prev_close = prev[1], prev[2], prev[3], prev[4]

            # Check for bearish engulfing (potential bearish OB for short entries)
            is_bearish_engulfing = (
                curr_close < curr_open and  # Current is red
                prev_close > prev_open and  # Previous was green
                curr_open >= prev_close and  # Current opens at/above prev close
                curr_close <= prev_open      # Current closes at/below prev open
            )

            # Check for bullish engulfing (potential bullish OB for long entries)
            is_bullish_engulfing = (
                curr_close > curr_open and  # Current is green
                prev_close < prev_open and  # Previous was red
                curr_open <= prev_close and  # Current opens at/below prev close
                curr_close >= prev_open      # Current closes at/above prev open
            )

            if is_bearish_engulfing or is_bullish_engulfing:
                direction = 'bearish' if is_bearish_engulfing else 'bullish'
                actual_index = len(ohlcv) - lookback + i

                # Check if preceded by liquidity sweep (within 3 candles)
                preceded_by_sweep = any(
                    actual_index - 3 <= idx <= actual_index
                    for idx in sweep_indices
                )

                # Calculate body boundaries
                body_top = max(curr_open, curr_close)
                body_bottom = min(curr_open, curr_close)
                midpoint = (curr_high + curr_low) / 2

                # Check if OB has been mitigated (price returned to it)
                if direction == 'bearish':
                    mitigated = current_price >= body_bottom
                else:
                    mitigated = current_price <= body_top

                # Determine strength
                if preceded_by_sweep:
                    strength = 'strong'
                elif is_bearish_engulfing or is_bullish_engulfing:
                    strength = 'moderate'
                else:
                    strength = 'weak'

                order_blocks.append(OrderBlock(
                    direction=direction,
                    top=curr_high,
                    bottom=curr_low,
                    midpoint=round(midpoint, 6),
                    body_top=body_top,
                    body_bottom=body_bottom,
                    timestamp=datetime.fromtimestamp(curr[0] / 1000),
                    candle_index=actual_index,
                    strength=strength,
                    mitigated=mitigated,
                    preceded_by_sweep=preceded_by_sweep
                ))

        # Sort by timestamp (most recent first)
        order_blocks.sort(key=lambda x: x.timestamp, reverse=True)
        return order_blocks

    def analyze_order_blocks(self, ohlcv: list, lookback: int = 50) -> list:
        """
        Detect institutional order blocks from OHLCV data using volume analysis.

        Session 440: Phase 5 -- volume-based order block detection complementing
        the existing engulfing-based ``_detect_order_blocks`` method.

        An order block is a candle (or cluster) with:
        1. Higher-than-average volume (>1.5x 20-period average)
        2. Followed by a sharp move (>2% in next 3 candles)
        3. The order block zone = body of the high-volume candle

        Args:
            ohlcv: CCXT-format candle data [[ts, o, h, l, c, v], ...]
            lookback: Number of candles to scan (default 50).

        Returns:
            List of dicts:
            [{"type": "bullish"|"bearish", "zone_high": float, "zone_low": float,
              "volume_ratio": float, "candles_ago": int, "strength": str}]
        """
        if not ohlcv or len(ohlcv) < 25:
            return []

        results: list = []
        candles = ohlcv[-lookback:] if len(ohlcv) >= lookback else ohlcv
        total = len(candles)

        for i in range(total - 3):
            # Volume average: 20-period window centered on the candle (or as much as available)
            vol_start = max(0, i - 10)
            vol_end = min(total, i + 10)
            surrounding_volumes = [c[5] for c in candles[vol_start:vol_end] if c[5] > 0]
            if not surrounding_volumes:
                continue
            avg_volume = sum(surrounding_volumes) / len(surrounding_volumes)

            candle_volume = candles[i][5]
            if avg_volume <= 0 or candle_volume <= 0:
                continue

            volume_ratio = candle_volume / avg_volume
            if volume_ratio < 1.5:
                continue

            # High-volume candle found; check for sharp move in next 3 candles
            ob_close = candles[i][4]
            max_move_pct = 0.0
            move_direction = None

            for j in range(1, min(4, total - i)):
                future_close = candles[i + j][4]
                if ob_close == 0:
                    continue
                change_pct = ((future_close - ob_close) / ob_close) * 100

                if abs(change_pct) > abs(max_move_pct):
                    max_move_pct = change_pct
                    move_direction = "up" if change_pct > 0 else "down"

            if abs(max_move_pct) < 2.0:
                continue

            # Classify: high-vol down candle + up move = bullish OB (institutional buy)
            ob_open = candles[i][1]
            candle_is_red = ob_close < ob_open

            if candle_is_red and move_direction == "up":
                ob_type = "bullish"
            elif not candle_is_red and move_direction == "down":
                ob_type = "bearish"
            else:
                # Candle color doesn't contradict the follow-through -- still valid
                ob_type = "bullish" if move_direction == "up" else "bearish"

            zone_high = max(ob_open, ob_close)
            zone_low = min(ob_open, ob_close)
            candles_ago = total - 1 - i

            strength = "strong" if volume_ratio >= 2.5 else "moderate"

            results.append({
                "type": ob_type,
                "zone_high": round(zone_high, 6),
                "zone_low": round(zone_low, 6),
                "volume_ratio": round(volume_ratio, 2),
                "candles_ago": candles_ago,
                "strength": strength,
            })

        # Most recent first
        results.sort(key=lambda x: x["candles_ago"])
        return results

    def _detect_equal_levels(
        self, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
        tolerance_pct: float = 0.5
    ) -> List[EqualLevel]:
        """
        Detect Equal Highs (EQH) and Equal Lows (EQL) - Session 121 (SMC Series).

        From Kaizen SMC Series:
        "A sideways trend by relatively equal highs (EQH) and lows (EQL)."
        These are LIQUIDITY TARGETS - institutions will hunt these levels.

        Session 122 (PIEVERSE Learning): EQH at recent highs implies CHoCH.
        David: "Last swing high failed to make a new high = Equal High = potential CHoCH"
        When the last 2 swing highs are at the same level (EQH), it means the uptrend
        failed to continue - this is a Change of Character signal for shorts.

        Algorithm:
        1. Group swing highs/lows that are within tolerance_pct of each other
        2. 2+ touches at same level = EQH/EQL
        3. More touches = stronger liquidity target
        4. Session 122: Check if EQH involves the most recent swing high → implies CHoCH

        Args:
            swing_highs: List of swing high points
            swing_lows: List of swing low points
            tolerance_pct: How close prices need to be to count as "equal"

        Returns:
            List of EqualLevel objects
        """
        equal_levels = []

        # Find EQH (Equal Highs)
        if len(swing_highs) >= 2:
            # Group highs by price proximity
            processed = set()
            for i, sh1 in enumerate(swing_highs):
                if i in processed:
                    continue

                group = [sh1]
                for j, sh2 in enumerate(swing_highs):
                    if i != j and j not in processed:
                        price_diff_pct = abs(sh1.price - sh2.price) / sh1.price * 100
                        if price_diff_pct <= tolerance_pct:
                            group.append(sh2)
                            processed.add(j)

                if len(group) >= 2:
                    processed.add(i)
                    avg_price = sum(g.price for g in group) / len(group)
                    touch_prices = [g.price for g in group]

                    # Session 122 (PIEVERSE Learning): Check if EQH implies CHoCH
                    # If the most recent swing high is part of this EQH group,
                    # it means price failed to make a new high = potential CHoCH
                    most_recent_high = swing_highs[-1]
                    second_most_recent = swing_highs[-2] if len(swing_highs) >= 2 else None

                    implies_choch = False
                    if most_recent_high in group:
                        # The last swing high is part of an EQH = failed new high
                        implies_choch = True
                        logger.info(f"   EQH implies CHoCH: Last swing high ${most_recent_high.price:.4f} "
                                   f"failed to make new high (forms EQH at ${avg_price:.4f})")

                    equal_levels.append(EqualLevel(
                        type='EQH',
                        price=round(avg_price, 6),
                        touch_count=len(group),
                        touch_prices=touch_prices,
                        tolerance_pct=tolerance_pct,
                        timestamp_first=min(g.timestamp for g in group),
                        timestamp_last=max(g.timestamp for g in group),
                        is_liquidity_target=len(group) >= 2,
                        implies_choch=implies_choch
                    ))

        # Find EQL (Equal Lows)
        if len(swing_lows) >= 2:
            processed = set()
            for i, sl1 in enumerate(swing_lows):
                if i in processed:
                    continue

                group = [sl1]
                for j, sl2 in enumerate(swing_lows):
                    if i != j and j not in processed:
                        price_diff_pct = abs(sl1.price - sl2.price) / sl1.price * 100
                        if price_diff_pct <= tolerance_pct:
                            group.append(sl2)
                            processed.add(j)

                if len(group) >= 2:
                    processed.add(i)
                    avg_price = sum(g.price for g in group) / len(group)
                    touch_prices = [g.price for g in group]

                    equal_levels.append(EqualLevel(
                        type='EQL',
                        price=round(avg_price, 6),
                        touch_count=len(group),
                        touch_prices=touch_prices,
                        tolerance_pct=tolerance_pct,
                        timestamp_first=min(g.timestamp for g in group),
                        timestamp_last=max(g.timestamp for g in group),
                        is_liquidity_target=len(group) >= 2,
                        implies_choch=False  # EQL doesn't imply bearish CHoCH
                    ))

        return equal_levels

    def _calculate_equilibrium(
        self, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
        current_price: float, structure: str
    ) -> Optional[Equilibrium]:
        """
        Calculate Equilibrium (50% level) - Session 121 (SMC Series Integration).

        From Kaizen SMC Series:
        "Equilibrium is the midpoint (50%) between the low and high.
        Anything below is discount, anything above is premium.
        Institutions buy in discount and sell at premium."

        For shorts (bearish structure):
        - Draw from swing high to swing low
        - Enter at PREMIUM (above 50%) - institutions are selling here

        For longs (bullish structure):
        - Draw from swing low to swing high
        - Enter at DISCOUNT (below 50%) - institutions are buying here

        Args:
            swing_highs: List of swing high points
            swing_lows: List of swing low points
            current_price: Current price
            structure: 'bullish', 'bearish', or 'sideways'

        Returns:
            Equilibrium object or None
        """
        if not swing_highs or not swing_lows:
            return None

        # Use most recent significant swings
        recent_high = max(swing_highs[-3:], key=lambda x: x.price)
        recent_low = min(swing_lows[-3:], key=lambda x: x.price)

        swing_high = recent_high.price
        swing_low = recent_low.price
        equilibrium_price = (swing_high + swing_low) / 2

        # Calculate distance from current price to equilibrium
        distance_to_eq_pct = ((current_price - equilibrium_price) / equilibrium_price) * 100

        # Determine zone
        if abs(distance_to_eq_pct) < 1.0:
            zone = 'at_equilibrium'
        elif current_price > equilibrium_price:
            zone = 'premium'
        else:
            zone = 'discount'

        # Direction based on structure
        if structure == 'bearish':
            direction = 'bearish'  # Draw high to low for shorts
        else:
            direction = 'bullish'  # Draw low to high for longs

        return Equilibrium(
            swing_high=swing_high,
            swing_low=swing_low,
            equilibrium_price=round(equilibrium_price, 6),
            current_price=current_price,
            zone=zone,
            distance_to_eq_pct=round(distance_to_eq_pct, 2),
            direction=direction,
            timestamp_high=recent_high.timestamp,
            timestamp_low=recent_low.timestamp
        )

    def _determine_structure(
        self, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint]
    ) -> Tuple[str, List[str]]:
        """
        Determine current market structure based on swing points.

        Bullish: HH-HL-HH-HL (Higher Highs, Higher Lows)
        Bearish: LH-LL-LH-LL (Lower Highs, Lower Lows)
        """
        sequence = []

        # Analyze last 3 swing highs
        if len(swing_highs) >= 2:
            for i in range(1, min(3, len(swing_highs))):
                if swing_highs[-i].price > swing_highs[-i-1].price:
                    sequence.append('HH')
                else:
                    sequence.append('LH')

        # Analyze last 3 swing lows
        if len(swing_lows) >= 2:
            for i in range(1, min(3, len(swing_lows))):
                if swing_lows[-i].price > swing_lows[-i-1].price:
                    sequence.append('HL')
                else:
                    sequence.append('LL')

        # Determine overall structure
        bullish_count = sequence.count('HH') + sequence.count('HL')
        bearish_count = sequence.count('LH') + sequence.count('LL')

        if bullish_count > bearish_count:
            structure = 'bullish'
        elif bearish_count > bullish_count:
            structure = 'bearish'
        else:
            structure = 'ranging'

        return structure, sequence

    def _detect_choch(
        self, ohlcv: List, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
        current_structure: str, structure_sequence: List[str] = None,
        trendline: 'Trendline' = None, fib_levels: Dict = None
    ) -> Optional[StructureBreak]:
        """
        Detect Change of Character (CHoCH).

        CHoCH = First break against the current trend:
        - In uptrend: Price breaks below a swing low (first LL)
        - In downtrend: Price breaks above a swing high (first HH)

        IMPROVED (Learning 008): Use structure sequence for historical CHoCH detection.
        CHoCH remains valid until structure is invalidated, not just point-in-time.

        SESSION 125 (RAVE Learning): Trendline break detection.
        David's pattern: Trendline break (3+ touches) + FIB retest (0.618-0.786) = CHoCH signal.
        This catches structure shifts that swing point detection misses.

        David's approach:
        1. Look for FIRST break of structure against trend
        2. If structure sequence contains LL (in prior uptrend) = CHoCH happened
        3. CHoCH remains valid until price invalidates structure (new HH above swing high)
        4. SESSION 125: Trendline break (3+ touches) = CHoCH even without swing break
        """
        if not swing_highs or not swing_lows:
            return None

        current_price = ohlcv[-1][4]

        # Method 1: SESSION 125 - Trendline Break Detection (RAVE Learning)
        # David detected CHoCH on RAVE/US/CYS/PIEVERSE via trendline breaks
        if trendline and trendline.detected and trendline.broken:
            # Require strong trendline (3+ touches) for CHoCH signal
            if trendline.touch_count >= 3:
                # Check if price is in FIB retest zone (0.618-0.786) for confluence
                in_fib_zone = False
                if fib_levels:
                    fib_0618 = fib_levels.get('0.618')
                    fib_0786 = fib_levels.get('0.786')
                    if fib_0618 and fib_0786:
                        # For shorts: price should be in retest zone (between 0.618 and 0.786)
                        in_fib_zone = fib_0786 <= current_price <= fib_0618

                # Determine break direction
                broken_downward = current_price < trendline.trendline_price
                broken_upward = current_price > trendline.trendline_price

                # CASE 1: Descending trendline (resistance) broken DOWNWARD
                # This means price failed to reach the declining resistance and dropped = bearish CHoCH
                # RAVE, US, CYS, PIEVERSE pattern: Descending resistance + price drops = structure shift
                if trendline.direction == 'descending' and broken_downward:
                    return StructureBreak(
                        type='CHoCH',
                        direction='bearish',
                        price=current_price,
                        level_broken=trendline.trendline_price,
                        timestamp=datetime.utcnow(),
                        confirmed=True,
                        metadata={
                            'source': 'trendline_break',
                            'trendline_type': 'descending_resistance',
                            'trendline_touches': trendline.touch_count,
                            'break_direction': 'downward',
                            'in_fib_zone': in_fib_zone,
                            'fib_confluence': f"Price at {current_price:.4f} in 0.618-0.786 zone" if in_fib_zone else "No FIB confluence"
                        }
                    )

                # CASE 2: Ascending trendline (support) broken DOWNWARD
                # This means uptrend support was broken = bearish CHoCH (trend reversal)
                elif trendline.direction == 'ascending' and broken_downward:
                    return StructureBreak(
                        type='CHoCH',
                        direction='bearish',
                        price=current_price,
                        level_broken=trendline.trendline_price,
                        timestamp=datetime.utcnow(),
                        confirmed=True,
                        metadata={
                            'source': 'trendline_break',
                            'trendline_type': 'ascending_support',
                            'trendline_touches': trendline.touch_count,
                            'break_direction': 'downward',
                            'in_fib_zone': in_fib_zone,
                            'fib_confluence': f"Price at {current_price:.4f} in 0.618-0.786 zone" if in_fib_zone else "No FIB confluence"
                        }
                    )

                # CASE 3: Descending trendline broken UPWARD = bullish CHoCH (ignore for shorts)
                # CASE 4: Ascending trendline broken UPWARD = bullish continuation (ignore for shorts)

        # Method 2: Structure sequence analysis (David's approach - Learning 008)
        # Check if structure sequence indicates a CHoCH occurred historically
        if structure_sequence:
            choch_from_sequence = self._detect_choch_from_sequence(
                structure_sequence, swing_highs, swing_lows, ohlcv
            )
            if choch_from_sequence:
                return choch_from_sequence

        # Method 3: Point-in-time detection (original - fallback)
        if current_structure == 'bullish':
            # Look for bearish CHoCH (break below swing low)
            for sl in reversed(swing_lows[-3:]):
                if current_price < sl.price:
                    return StructureBreak(
                        type='CHoCH',
                        direction='bearish',
                        price=current_price,
                        level_broken=sl.price,
                        timestamp=datetime.utcnow(),
                        confirmed=True
                    )

        elif current_structure == 'bearish':
            # Look for bullish CHoCH (break above swing high)
            for sh in reversed(swing_highs[-3:]):
                if current_price > sh.price:
                    return StructureBreak(
                        type='CHoCH',
                        direction='bullish',
                        price=current_price,
                        level_broken=sh.price,
                        timestamp=datetime.utcnow(),
                        confirmed=True
                    )

        return None

    def _detect_choch_from_sequence(
        self, sequence: List[str], swing_highs: List[SwingPoint],
        swing_lows: List[SwingPoint], ohlcv: List
    ) -> Optional[StructureBreak]:
        """
        Detect CHoCH from structure sequence (Learning 008 improvement).

        David's method: A CHoCH is detected when the structure sequence shows
        a transition from bullish to bearish (LL after HH/HL) or bearish to bullish
        (HH after LH/LL).

        The CHoCH remains valid as long as price hasn't invalidated it:
        - Bearish CHoCH invalid if price makes new HH above last swing high
        - Bullish CHoCH invalid if price makes new LL below last swing low
        """
        if not sequence or len(sequence) < 2:
            return None

        current_price = ohlcv[-1][4]

        # Look for bearish CHoCH: LL appearing after bullish structure
        # Pattern: HH/HL followed by LL indicates bearish CHoCH
        for i in range(1, len(sequence)):
            if sequence[i] == 'LL' and sequence[i-1] in ['HH', 'HL']:
                # Found bearish CHoCH pattern

                # Check if CHoCH is still valid (price hasn't made new HH)
                if swing_highs:
                    last_swing_high = max(sh.price for sh in swing_highs[-3:])
                    # CHoCH remains valid if current price below swing high
                    # (hasn't invalidated the structure shift)
                    if current_price < last_swing_high:
                        ll_price = min(sl.price for sl in swing_lows[-3:]) if swing_lows else current_price
                        return StructureBreak(
                            type='CHoCH',
                            direction='bearish',
                            price=ll_price,
                            level_broken=swing_lows[-2].price if len(swing_lows) >= 2 else ll_price,
                            timestamp=swing_lows[-1].timestamp if swing_lows else datetime.utcnow(),
                            confirmed=True
                        )

        # Look for bullish CHoCH: HH appearing after bearish structure
        # Pattern: LH/LL followed by HH indicates bullish CHoCH
        for i in range(1, len(sequence)):
            if sequence[i] == 'HH' and sequence[i-1] in ['LH', 'LL']:
                # Found bullish CHoCH pattern

                # Check if CHoCH is still valid (price hasn't made new LL)
                if swing_lows:
                    last_swing_low = min(sl.price for sl in swing_lows[-3:])
                    # CHoCH remains valid if current price above swing low
                    if current_price > last_swing_low:
                        hh_price = max(sh.price for sh in swing_highs[-3:]) if swing_highs else current_price
                        return StructureBreak(
                            type='CHoCH',
                            direction='bullish',
                            price=hh_price,
                            level_broken=swing_highs[-2].price if len(swing_highs) >= 2 else hh_price,
                            timestamp=swing_highs[-1].timestamp if swing_highs else datetime.utcnow(),
                            confirmed=True
                        )

        return None

    def _detect_bos(
        self, ohlcv: List, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
        current_structure: str
    ) -> Optional[StructureBreak]:
        """
        Detect Break of Structure (BOS).

        BOS = Continuation break confirming the new trend:
        - In new downtrend: Price makes a new lower low
        - In new uptrend: Price makes a new higher high
        """
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return None

        current_price = ohlcv[-1][4]

        # For shorts, we want bearish BOS (new lower low)
        if current_structure in ['bearish', 'ranging']:
            # Check if current price is below the second-to-last swing low
            if current_price < swing_lows[-2].price:
                return StructureBreak(
                    type='BOS',
                    direction='bearish',
                    price=current_price,
                    level_broken=swing_lows[-2].price,
                    timestamp=datetime.utcnow(),
                    confirmed=True
                )

        return None

    def _detect_cisd(
        self, ohlcv: List, sweeps: List[LiquiditySweep], lookback: int = 3
    ) -> Optional[StructureBreak]:
        """
        Detect Change in State of Delivery (CISD) - micro-CHoCH.
        A CISD occurs when price breaks internal structure following a liquidity sweep.
        Victor's 'High Conviction' trigger: prioritize the candle body close above/below
        the sweep candle's range.
        """
        if not ohlcv or not sweeps:
            return None

        # Look at the most recent sweep (must be reasonably fresh, e.g. last 10 candles)
        recent_sweep = sweeps[0]
        current_idx = len(ohlcv) - 1
        
        # If sweep is too old, ignore
        if current_idx - recent_sweep.candle_index > 10:
            return None

        sweep_candle = ohlcv[recent_sweep.candle_index]
        sweep_high = sweep_candle[2]
        sweep_low = sweep_candle[3]

        # Scan candles after the sweep
        for i in range(recent_sweep.candle_index + 1, len(ohlcv)):
            candle = ohlcv[i]
            # candle = [timestamp, open, high, low, close, volume]
            close = candle[4]
            timestamp = datetime.fromtimestamp(candle[0] / 1000)

            if recent_sweep.direction == 'bearish':
                # Sweep of highs (bearish setup). We want a body close below the sweep candle's low.
                if close < sweep_low:
                    return StructureBreak(
                        type='CISD',
                        direction='bearish',
                        price=close,
                        level_broken=sweep_low,
                        timestamp=timestamp,
                        confirmed=True,
                        metadata={'sweep_preceded': True, 'trigger': 'body_close_below_sweep_low'}
                    )
            else:
                # Sweep of lows (bullish setup). We want a body close above the sweep candle's high.
                if close > sweep_high:
                    return StructureBreak(
                        type='CISD',
                        direction='bullish',
                        price=close,
                        level_broken=sweep_high,
                        timestamp=timestamp,
                        confirmed=True,
                        metadata={'sweep_preceded': True, 'trigger': 'body_close_above_sweep_high'}
                    )

        return None

    def _calculate_fib_levels(
        self, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
        ohlcv: List = None, is_tge: bool = False
    ) -> Dict[str, float]:
        """
        Calculate Fibonacci retracement levels from recent swing.

        For shorts: Measure from recent low to high

        Learning 011: For TGE tokens (< 7 days old), use ATH → ATL (full range)
        instead of recent swings. This matches David's approach of drawing FIB
        from the entire TGE pump-dump cycle.

        Args:
            swing_highs: List of swing high points
            swing_lows: List of swing low points
            ohlcv: Raw OHLCV data (used for TGE ATH/ATL calculation)
            is_tge: Whether this is a TGE token (auto-detected if ohlcv provided)
        """
        if not swing_highs or not swing_lows:
            return {}

        # TGE Detection: Check actual age of token from first candle timestamp
        use_full_range = is_tge
        token_age_days = None

        if ohlcv and len(ohlcv) > 0:
            # Calculate actual token age from first candle
            first_candle_ts = ohlcv[0][0]  # Timestamp in ms
            first_candle_dt = datetime.fromtimestamp(first_candle_ts / 1000)
            token_age_days = (datetime.now() - first_candle_dt).days

            # Use full range for tokens < 14 days old
            if token_age_days < 14:
                use_full_range = True
                logger.info(f"TGE detected (token is {token_age_days} days old) - using ATH → ATL for FIB")

        if use_full_range and ohlcv:
            # Use ATH and ATL from all available data
            all_highs = [candle[2] for candle in ohlcv]  # High prices
            all_lows = [candle[3] for candle in ohlcv]   # Low prices
            swing_high = max(all_highs)
            swing_low = min(all_lows)
            logger.info(f"TGE FIB range: ATH ${swing_high:.4f} → ATL ${swing_low:.4f}")
        else:
            # Use most recent significant swing (original behavior)
            swing_high = max(swing_highs[-3:], key=lambda x: x.price).price
            swing_low = min(swing_lows[-3:], key=lambda x: x.price).price

        diff = swing_high - swing_low

        return {
            'swing_high': swing_high,
            'swing_low': swing_low,
            'is_tge_range': use_full_range,  # Track if we used TGE calculation
            'token_age_days': token_age_days,
            '0.236': round(swing_low + (diff * 0.236), 6),
            '0.382': round(swing_low + (diff * 0.382), 6),
            '0.5': round(swing_low + (diff * 0.5), 6),
            '0.618': round(swing_low + (diff * 0.618), 6),  # David's preferred entry
            '0.786': round(swing_low + (diff * 0.786), 6),
        }

    def _calculate_entry_zone(
        self, fib_levels: Dict, structure: str
    ) -> Optional[Tuple[float, float]]:
        """
        Calculate entry zone around 0.618 FIB level.

        For shorts: Entry zone between 0.618 and 0.786 (retrace into)
        """
        if not fib_levels or '0.618' not in fib_levels:
            return None

        # Entry zone: 0.618 to 0.786 (where price retraces to)
        return (fib_levels['0.618'], fib_levels['0.786'])

    def _calculate_atr(self, ohlcv: List, period: int = 14) -> Optional[float]:
        """
        Calculate Average True Range (ATR) for volatility-based SL buffering.

        Session 92+ Enhancement (Gemini Review):
        ATR provides a volatility-adjusted buffer that adapts to TGE price action.
        Standard 1-3% buffers fail on TGE tokens that can wick 10-15% in a single candle.

        Args:
            ohlcv: List of [timestamp, open, high, low, close, volume]
            period: ATR period (default 14 candles)

        Returns:
            ATR value or None if insufficient data
        """
        if not ohlcv or len(ohlcv) < period + 1:
            return None

        true_ranges = []
        for i in range(1, len(ohlcv)):
            high = ohlcv[i][2]
            low = ohlcv[i][3]
            prev_close = ohlcv[i-1][4]

            # True Range = max of:
            # 1. Current High - Current Low
            # 2. abs(Current High - Previous Close)
            # 3. abs(Current Low - Previous Close)
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)

        # Calculate ATR as simple moving average of last N true ranges
        if len(true_ranges) >= period:
            atr = sum(true_ranges[-period:]) / period
            return round(atr, 6)

        return None

    def _calculate_dynamic_sl(
        self, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
        structure: str, current_price: float, ohlcv: List = None, category: str = None
    ) -> Tuple[float, Dict]:
        """
        Calculate dynamic stop loss based on swing structure with ATR-based buffering.

        Session 92+ Enhancement (Gemini Review):
        - Primary: ATR × 1.0 buffer (adapts to volatility)
        - Fallback: Category-based buffer if ATR unavailable
        - Emergency: Fixed 30% if no data

        Session 120: US/Talus Learning (Dec 12, 2025 - David's Feedback)
        - David uses tighter SL (21.568% vs our 27.2%)
        - Changed ATR multiplier from 2.0 to 1.0 for tighter stops
        - Added cap: SL from entry should be max 25% to maintain 2:1+ R:R
        - David's approach: SL just above swing high, not 2x ATR above it

        David's approach: SL above last swing high (4H body close).
        Gemini enhancement: Use ATR instead of fixed % to handle TGE wicks.

        Returns:
            Tuple of (sl_price, sl_metadata)
        """
        sl_metadata = {
            'method': 'unknown',
            'buffer_pct': 0,
            'atr': None,
            'category_used': category
        }

        if not swing_highs:
            # Fallback to fixed 30% if no swing data
            sl_metadata['method'] = 'fixed_30pct'
            sl_metadata['buffer_pct'] = 30.0
            return current_price * 1.30, sl_metadata

        # For shorts: SL above the most recent swing high
        last_swing_high = swing_highs[-1].price

        # Try ATR-based buffer first (Gemini recommendation)
        atr = self._calculate_atr(ohlcv) if ohlcv else None

        if atr and atr > 0:
            # Session 120: Changed from ATR × 2.0 to ATR × 1.0 for tighter stops
            # David's US trade had SL at 21.568%, ours was 27.2% - too wide
            atr_buffer = atr * 1.0  # Was 2.0, now 1.0 per David's feedback
            sl_with_buffer = last_swing_high + atr_buffer
            buffer_pct = (atr_buffer / last_swing_high) * 100

            # Session 120: Cap buffer at 5% above swing high (David's method)
            # This ensures we don't have excessively wide stops that kill R:R
            max_buffer_pct = 5.0  # David uses ~5% above swing high
            if buffer_pct > max_buffer_pct:
                sl_with_buffer = last_swing_high * (1 + max_buffer_pct / 100)
                buffer_pct = max_buffer_pct
                sl_metadata['capped'] = True

            sl_metadata['method'] = 'atr_based'
            sl_metadata['atr'] = atr
            sl_metadata['buffer_pct'] = round(buffer_pct, 2)
            logger.info(f"   SL buffer: ATR-based {buffer_pct:.1f}% (ATR=${atr:.4f})")

        elif category:
            # Fallback to category-based buffer
            cat_lower = category.lower().replace(' ', '_')
            buffer_mult = self.CATEGORY_VOLATILITY.get(cat_lower, self.CATEGORY_VOLATILITY['default'])
            # Session 120: Cap category buffer at 5%
            buffer_mult = min(buffer_mult, 0.05)
            sl_with_buffer = last_swing_high * (1 + buffer_mult)

            sl_metadata['method'] = 'category_based'
            sl_metadata['buffer_pct'] = buffer_mult * 100
            logger.info(f"   SL buffer: Category-based {buffer_mult*100:.1f}% ({category})")

        else:
            # Default 5% buffer (Session 120: reduced from 6%)
            sl_with_buffer = last_swing_high * 1.05
            sl_metadata['method'] = 'default_5pct'
            sl_metadata['buffer_pct'] = 5.0

        return round(sl_with_buffer, 6), sl_metadata

    def _calculate_24h_volume(self, ohlcv: List, timeframe: str) -> Optional[float]:
        """
        Calculate 24-hour volume from OHLCV data.

        Session 92+ Enhancement (Gemini Review):
        Volume is critical for liquidity gating. A valid CHoCH/BOS on a chart
        with $50k volume is a trap - you won't get exit without massive slippage.

        Args:
            ohlcv: List of [timestamp, open, high, low, close, volume]
            timeframe: Candle timeframe (4h, 1h, etc.)

        Returns:
            24h volume in USD or None if insufficient data
        """
        if not ohlcv:
            return None

        # Calculate how many candles make 24 hours
        tf_hours = {
            '15m': 0.25, '30m': 0.5, '1h': 1, '2h': 2, '4h': 4,
            '6h': 6, '8h': 8, '12h': 12, '1d': 24
        }
        hours_per_candle = tf_hours.get(timeframe, 4)
        candles_per_24h = int(24 / hours_per_candle)

        # Sum volume from last N candles
        recent_candles = ohlcv[-candles_per_24h:] if len(ohlcv) >= candles_per_24h else ohlcv

        # Volume is in quote currency (USDT) for most exchanges
        # OHLCV format: [timestamp, open, high, low, close, volume]
        # For some exchanges, volume is in base currency, so we approximate
        total_volume = 0
        for candle in recent_candles:
            volume = candle[5]  # Volume
            avg_price = (candle[2] + candle[3]) / 2  # (High + Low) / 2
            # Assume volume is in base currency, convert to USDT
            total_volume += volume * avg_price

        return round(total_volume, 2)

    def _calculate_target(
        self, current_price: float, structure: str, fib_levels: Dict, category: str = None
    ) -> float:
        """
        Calculate target price for R:R calculation.

        Session 116: Use David's methodology with category-based expected dumps.
        For shorts: Target at swing low, category dump, or -50% from entry (whichever is lower).

        Category-based expected dumps (from historical data):
        - Gaming/GameFi: -60% (target = 40% of ATH)
        - Layer_1/L1: -60% (target = 40% of ATH)
        - L2: -70% (target = 30% of ATH)
        - DeFi: -50% (target = 50% of ATH)
        - AI: -55% (target = 45% of ATH)
        - Meme: -70% (target = 30% of ATH)
        """
        # Category-based dump factors (target = ATH * factor)
        category_targets = {
            'gaming': 0.40,      # -60% dump expected
            'gamefi': 0.40,      # -60% dump expected
            'layer_1': 0.40,     # -60% dump expected
            'l1': 0.40,          # -60% dump expected
            'l2': 0.30,          # -70% dump expected
            'defi': 0.50,        # -50% dump expected
            'ai': 0.45,          # -55% dump expected
            'meme': 0.30,        # -70% dump expected
        }

        targets = []

        # Option 1: Swing low (if available)
        if fib_levels and 'swing_low' in fib_levels:
            targets.append(fib_levels['swing_low'])

        # Option 2: Category-based target (Session 116: David's methodology)
        if category and fib_levels and 'swing_high' in fib_levels:
            cat_key = category.lower().replace(' ', '_')
            dump_factor = category_targets.get(cat_key, 0.50)
            category_target = fib_levels['swing_high'] * dump_factor
            targets.append(category_target)

        # Option 3: Fallback -50% from current price
        targets.append(current_price * 0.50)

        # Use the most aggressive (lowest) target for better R:R
        return min(targets) if targets else current_price * 0.50

    def _calculate_rr_ratio(
        self, entry_price: float, stop_loss: float, target: float, structure: str
    ) -> float:
        """
        Calculate Risk:Reward ratio.

        R:R = (Entry - Target) / (Stop Loss - Entry) for shorts
        """
        if structure in ['bearish', 'ranging']:
            # Short trade
            risk = abs(stop_loss - entry_price)
            reward = abs(entry_price - target)
        else:
            # Long trade (not our focus)
            risk = abs(entry_price - stop_loss)
            reward = abs(target - entry_price)

        if risk == 0:
            return 0.0

        return round(reward / risk, 2)

    def _analyze_last_candle(self, ohlcv: List) -> Dict:
        """Analyze the last candle (color and body close)."""
        if not ohlcv:
            return {'color': 'unknown', 'body_close': None}

        last = ohlcv[-1]
        open_price = last[1]
        close_price = last[4]

        color = 'green' if close_price >= open_price else 'red'
        body_close = close_price  # 4H body close (not wick)

        return {'color': color, 'body_close': body_close}

    def _check_entry_conditions(
        self, choch: Optional[StructureBreak], bos: Optional[StructureBreak],
        current_price: float, entry_zone: Optional[Tuple[float, float]],
        rr_ratio: float, structure: str
    ) -> bool:
        """
        Check if all entry conditions are met for a SHORT.

        David's requirements:
        1. CHoCH detected (first sign of reversal)
        2. BOS confirmed (structure shift)
        3. Price in 0.618 entry zone (retracement)
        4. R:R ratio >= 2:1
        5. 4H candle closed red (momentum confirmation)
        """
        # For shorts, we need bearish conditions
        if structure == 'bullish':
            return False

        # Check each condition
        has_choch = choch is not None and choch.direction == 'bearish'
        has_bos = bos is not None and bos.direction == 'bearish'
        in_zone = entry_zone and entry_zone[0] <= current_price <= entry_zone[1]
        good_rr = rr_ratio >= self.MIN_RR_RATIO

        # For entry, we need at least CHoCH + R:R minimum
        # BOS is confirmation, can enter on CHoCH alone if R:R is good
        return has_choch and good_rr and (in_zone or has_bos)

    def _check_entry_timing_confirmation(
        self,
        choch: Optional[StructureBreak],
        fib_levels: Dict,
        swing_highs: List[SwingPoint],
        swing_lows: List[SwingPoint],
        current_price: float,
        trendline: Optional[TrendlineAnalysis] = None
    ) -> EntryTimingConfirmation:
        """
        SESSION 125: Check David's 3-step entry timing confirmation (CYS Learning).

        David's Dec 14 Feedback: "Right structure (CHoCH) but need RIGHT TIMING."

        Don't enter on CHoCH alone. Wait for 3-step confirmation:
        1. Price retrace to FIB (0.618-0.786 zone) OR trendline
        2. Break previous swing low (structure confirmation)
        3. Failure to make new HH (lower high forms)

        This prevents premature entries and improves timing precision.
        """
        choch_detected = choch is not None and choch.direction == 'bearish'

        # Extract levels
        fib_0618 = fib_levels.get('0.618')
        fib_0786 = fib_levels.get('0.786')
        trendline_price = trendline.trendline_price if trendline and trendline.detected else None

        # Get previous swing points
        previous_swing_low = swing_lows[-1].price if swing_lows else None
        last_swing_high = swing_highs[-1].price if swing_highs else None

        # Step 1: Price retrace to FIB zone (0.618-0.786) OR near trendline
        step_1_fib_retrace = False
        if fib_0618 and fib_0786:
            step_1_fib_retrace = fib_0786 <= current_price <= fib_0618
        elif trendline_price:
            # Within 2% of trendline = retrace confirmation
            distance_pct = abs((current_price - trendline_price) / trendline_price * 100)
            step_1_fib_retrace = distance_pct <= 2.0

        # Step 2: Break previous swing low
        step_2_break_swing_low = False
        if previous_swing_low:
            step_2_break_swing_low = current_price < previous_swing_low

        # Step 3: Failure to make new HH (lower high formed)
        # This requires comparing recent swing highs
        step_3_failed_new_hh = False
        if len(swing_highs) >= 2:
            last_high = swing_highs[-1].price
            second_last_high = swing_highs[-2].price
            # If last high is LOWER than previous high = failed to make new HH
            step_3_failed_new_hh = last_high < second_last_high

        # Count confirmations
        confirmation_count = sum([step_1_fib_retrace, step_2_break_swing_low, step_3_failed_new_hh])
        all_confirmed = confirmation_count == 3

        # Generate status text
        if not choch_detected:
            status_text = "❌ No CHoCH detected - entry timing N/A"
            next_action = "Wait for CHoCH (structure shift)"
        elif all_confirmed:
            status_text = "✅ ALL 3 CONFIRMATIONS MET - READY TO ENTER"
            next_action = "Execute entry at current levels"
        else:
            missing = []
            if not step_1_fib_retrace:
                missing.append("FIB/trendline retrace")
            if not step_2_break_swing_low:
                missing.append("break swing low")
            if not step_3_failed_new_hh:
                missing.append("confirm lower high")
            status_text = f"⏳ {confirmation_count}/3 confirmed - waiting for: {', '.join(missing)}"
            next_action = f"Wait for: {missing[0]}" if missing else "Monitor price action"

        return EntryTimingConfirmation(
            choch_detected=choch_detected,
            step_1_fib_retrace=step_1_fib_retrace,
            step_2_break_swing_low=step_2_break_swing_low,
            step_3_failed_new_hh=step_3_failed_new_hh,
            all_confirmed=all_confirmed,
            confirmation_count=confirmation_count,
            current_price=current_price,
            fib_0618=fib_0618,
            fib_0786=fib_0786,
            previous_swing_low=previous_swing_low,
            trendline_price=trendline_price,
            last_swing_high=last_swing_high,
            status_text=status_text,
            next_action=next_action
        )

    def _list_conditions_met(
        self, choch: Optional[StructureBreak], bos: Optional[StructureBreak],
        current_price: float, entry_zone: Optional[Tuple[float, float]],
        rr_ratio: float, structure: str
    ) -> Dict[str, bool]:
        """List all entry conditions and their status."""
        return {
            'structure_bearish': structure in ['bearish', 'ranging'],
            'choch_bearish': choch is not None and choch.direction == 'bearish',
            'bos_confirmed': bos is not None and bos.direction == 'bearish',
            'in_fib_zone': entry_zone and entry_zone[0] <= current_price <= entry_zone[1] if entry_zone else False,
            'rr_minimum_met': rr_ratio >= self.MIN_RR_RATIO if rr_ratio else False,
        }

    def _error_result(self, token_symbol: str, error_msg: str) -> Dict:
        """Return error result."""
        return {
            'token_symbol': token_symbol,
            'error': error_msg,
            'current_structure': 'unknown',
            'choch_detected': False,
            'bos_detected': False,
            'ready_for_entry': False,
            'timestamp': datetime.utcnow().isoformat()
        }

    def detect_choch_for_tge(
        self,
        token_symbol: str,
        token_age_hours: float,
        direction: str = 'SHORT'
    ) -> Dict:
        """
        SESSION 300: Learning 043 Multi-Timeframe Fractal CHoCH Detection

        Detect CHoCH (Change of Character) using lifecycle-appropriate timeframe.

        Problem (from L043):
            4H timeframe authority (L013) creates timing gap for TGE tokens.
            Most TGE dumps complete 60-80% in first 4-8 hours.
            Waiting for 4H candle close loses optimal R:R entries.

        Solution:
            Use faster timeframes for TGE-Zero phase, then transition to 4H:
            - TGE-Zero (0-24h): 15m for fast CHoCH, 1h confirmation, 2 swing touches
            - Fresh (24-72h): 30m for balanced speed, 1h confirmation, 2 swing touches
            - Standard (72h+): 4H per L013, 3 swing touches

        Args:
            token_symbol: Token to analyze (e.g., "POWER")
            token_age_hours: Hours since TGE launch
            direction: 'SHORT' or 'LONG' (default SHORT for TGE shorts)

        Returns:
            Dict with:
                - choch_detected: bool
                - choch_details: CHoCH metadata
                - entry_timeframe: Timeframe context from L043
                - lifecycle_mode: TGE_ZERO, FRESH, or STANDARD
                - min_touches_required: 2 or 3 based on mode
                - position_multiplier: 0.5/0.75/1.0 based on mode
                - recommendation: Entry recommendation
        """
        # Get lifecycle-based timeframe settings (L043)
        entry_context = self._get_entry_timeframe_context(token_age_hours)
        entry_tf = entry_context['entry_tf']
        confirm_tf = entry_context['confirm_tf']
        min_touches = entry_context['choch_min_touches']
        mode = entry_context['mode']
        position_multiplier = entry_context['position_multiplier']

        logger.info(
            f"L043 TGE CHoCH Detection: {token_symbol} | Age: {token_age_hours:.1f}h | "
            f"Mode: {mode} | Entry TF: {entry_tf} | Confirm TF: {confirm_tf}"
        )

        try:
            # Analyze on entry timeframe
            entry_result = self.analyze(token_symbol, timeframe=entry_tf)

            # Analyze on confirmation timeframe if different
            confirm_result = None
            if confirm_tf != entry_tf:
                confirm_result = self.analyze(token_symbol, timeframe=confirm_tf)

            # Extract CHoCH from entry timeframe
            entry_choch = entry_result.get('choch_detected', False)
            entry_choch_details = entry_result.get('choch_details')

            # Confirmation check (L043: 15m CHoCH needs 1h confirmation)
            confirm_aligned = True
            if confirm_result:
                confirm_structure = confirm_result.get('current_structure', 'unknown')
                if direction == 'SHORT':
                    # For shorts, confirmation TF should not be bullish
                    confirm_aligned = confirm_structure in ['bearish', 'ranging']
                else:
                    # For longs, confirmation TF should not be bearish
                    confirm_aligned = confirm_structure in ['bullish', 'ranging']

            # Validate swing touches (L043: TGE-Zero needs 2, Standard needs 3)
            touches_valid = True
            if entry_choch_details and entry_choch_details.get('metadata'):
                metadata = entry_choch_details['metadata']
                if metadata.get('source') == 'trendline_break':
                    trendline_touches = metadata.get('trendline_touches', 0)
                    touches_valid = trendline_touches >= min_touches

            # Determine CHoCH validity
            choch_valid = entry_choch and confirm_aligned and touches_valid

            # Build recommendation
            if choch_valid:
                if mode == 'TGE_ZERO':
                    recommendation = f"FAST ENTRY: 15m CHoCH valid. Reduce position to {int(position_multiplier*100)}%"
                elif mode == 'FRESH':
                    recommendation = f"BALANCED ENTRY: 30m CHoCH valid. Position at {int(position_multiplier*100)}%"
                else:
                    recommendation = "STANDARD ENTRY: 4H CHoCH confirmed per L013"
            else:
                reasons = []
                if not entry_choch:
                    reasons.append(f"No CHoCH on {entry_tf}")
                if not confirm_aligned:
                    reasons.append(f"{confirm_tf} not aligned")
                if not touches_valid:
                    reasons.append(f"Need {min_touches}+ swing touches")
                recommendation = f"WAIT: {', '.join(reasons)}"

            return {
                'token_symbol': token_symbol,
                'token_age_hours': token_age_hours,
                'direction': direction,

                # CHoCH Detection
                'choch_detected': choch_valid,
                'choch_details': entry_choch_details if choch_valid else None,
                'entry_timeframe_choch': entry_choch,
                'confirmation_aligned': confirm_aligned,
                'touches_valid': touches_valid,

                # L043 Timeframe Context
                'lifecycle_mode': mode,
                'entry_timeframe': entry_tf,
                'confirmation_timeframe': confirm_tf,
                'min_touches_required': min_touches,
                'position_multiplier': position_multiplier,
                'stop_loss_pct': entry_context.get('stop_loss_pct', 30.0),

                # Entry Recommendation
                'recommendation': recommendation,
                'ready_for_entry': choch_valid,

                # Structure from entry TF
                'current_structure': entry_result.get('current_structure'),
                'entry_zone': entry_result.get('entry_zone'),
                'fib_levels': entry_result.get('fib_levels'),
                'dynamic_sl': entry_result.get('dynamic_sl'),

                # Metadata
                'timestamp': datetime.utcnow().isoformat(),
                'note': entry_context.get('note', ''),
                'warning': entry_context.get('warning', '')
            }

        except Exception as e:
            logger.error(f"L043 CHoCH detection failed for {token_symbol}: {e}")
            return {
                'token_symbol': token_symbol,
                'token_age_hours': token_age_hours,
                'error': str(e),
                'choch_detected': False,
                'lifecycle_mode': mode,
                'entry_timeframe': entry_tf,
                'recommendation': f"ERROR: {str(e)}",
                'ready_for_entry': False,
                'timestamp': datetime.utcnow().isoformat()
            }

    def _get_entry_timeframe_context(self, token_age_hours: float) -> Dict:
        """
        SESSION 300: Get lifecycle-based timeframe context (L043).

        Returns the appropriate entry timeframe based on token age.
        TGE tokens complete 60-80% of dumps in first 4-8 hours.

        Timeframe Hierarchy:
        - TGE-Zero (0-24h): 15m for fast CHoCH detection
        - Fresh (24-72h): 30m for balanced speed
        - Standard (72h+): 4H per L013
        """
        if token_age_hours <= 24:
            return {
                'mode': 'TGE_ZERO',
                'entry_tf': '15m',
                'confirm_tf': '1h',
                'choch_min_touches': 2,
                'position_multiplier': 0.50,
                'stop_loss_pct': 15.0,
                'note': 'Fast entry mode - 15m CHoCH valid',
                'warning': 'High volatility expected - position reduced 50%'
            }
        elif token_age_hours <= 72:
            return {
                'mode': 'FRESH',
                'entry_tf': '30m',
                'confirm_tf': '1h',
                'choch_min_touches': 2,
                'position_multiplier': 0.75,
                'stop_loss_pct': 20.0,
                'note': 'Balanced entry mode - 30m CHoCH valid'
            }
        else:
            return {
                'mode': 'STANDARD',
                'entry_tf': '4h',
                'confirm_tf': '4h',
                'choch_min_touches': 3,
                'position_multiplier': 1.0,
                'stop_loss_pct': 30.0,
                'note': 'Standard mode per L013'
            }


# Convenience function
def analyze_structure(token_symbol: str, exchange: str = "mexc", timeframe: str = "4h") -> Dict:
    """Analyze market structure for a token."""
    analyzer = MarketStructureAnalyzer(exchange_id=exchange)
    return analyzer.analyze(token_symbol, timeframe)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    if len(sys.argv) < 2:
        print("Usage: python market_structure_analyzer.py <TOKEN>")
        print("Example: python market_structure_analyzer.py POWER")
        sys.exit(1)

    token = sys.argv[1].upper()
    print(f"\n{'='*60}")
    print(f"MARKET STRUCTURE ANALYSIS: {token}")
    print(f"{'='*60}\n")

    result = analyze_structure(token)

    print(f"Structure: {result.get('current_structure')}")
    print(f"Sequence: {result.get('structure_sequence')}")
    print(f"\nCHoCH Detected: {result.get('choch_detected')}")
    if result.get('choch_details'):
        print(f"  - Direction: {result['choch_details']['direction']}")
        print(f"  - Level Broken: ${result['choch_details']['level_broken']:.4f}")

    print(f"\nBOS Detected: {result.get('bos_detected')}")

    print(f"\nFIB Levels:")
    for level, price in result.get('fib_levels', {}).items():
        print(f"  {level}: ${price:.4f}")

    print(f"\nEntry Zone: {result.get('entry_zone')}")
    print(f"In Entry Zone: {result.get('in_entry_zone')}")

    print(f"\nRisk Management:")
    print(f"  Dynamic SL: ${result.get('dynamic_sl', 0):.4f}")
    print(f"  Target: ${result.get('target_price', 0):.4f}")
    print(f"  R:R Ratio: {result.get('rr_ratio', 0):.2f}:1")
    print(f"  R:R Meets Minimum: {result.get('rr_meets_minimum')}")

    print(f"\n{'='*60}")
    print(f"READY FOR ENTRY: {'YES' if result.get('ready_for_entry') else 'NO'}")
    print(f"{'='*60}")

    print("\nConditions Met:")
    for cond, met in result.get('entry_conditions_met', {}).items():
        status = "[x]" if met else "[ ]"
        print(f"  {status} {cond}")
