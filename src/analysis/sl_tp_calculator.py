"""
Stop-Loss and Take-Profit Calculator Module (L027)

Implements professional SL/TP placement strategies from Kaizen Trading methodology.
Core principle: SL should be at the "trade invalidation" level where the thesis fails.

Session: 300
Author: DACLE Agent 0
Learning: L027 Stop-Loss & Take-Profit Placement Strategies

Features:
- S/R-based SL/TP (via L023 support/resistance)
- MA-based dynamic SL (50/200 EMA)
- RSI-based warnings (oversold bounce risk)
- Bollinger Band SL/TP
- R:R ratio validation (minimum 1:2)
- Volatility-adjusted SL width
- TGE-specific lifecycle SL/TP
- Trailing stop calculation
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


class SLMethod(Enum):
    """Stop-loss calculation method."""
    SR_BASED = "S/R_BASED"
    MA_BASED = "MA_BASED"
    BB_BASED = "BOLLINGER_BASED"
    TGE_PERCENTAGE = "TGE_PERCENTAGE"
    TGE_24H_HIGH = "TGE_24H_HIGH"
    TRAILING = "TRAILING"
    STANDARD_TA = "STANDARD_TA"


class RSIWarningLevel(Enum):
    """RSI warning levels for shorts."""
    CRITICAL = "CRITICAL"      # RSI <= 30
    ELEVATED = "ELEVATED"      # RSI <= 40
    NORMAL = "NORMAL"          # RSI > 40


class RRValidation(Enum):
    """Risk-Reward ratio validation status."""
    VALID = "VALID"
    INVALID = "INVALID"
    MARGINAL = "MARGINAL"


@dataclass
class SLTPResult:
    """Result of SL/TP calculation."""
    method: SLMethod
    stop_loss_price: Optional[float]
    take_profit_prices: List[Dict[str, Any]] = field(default_factory=list)
    invalidation_reason: str = ""
    risk_pct: float = 0.0
    reward_pct: float = 0.0
    rr_ratio: float = 0.0
    rr_valid: bool = False
    warnings: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "method": self.method.value,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_prices": self.take_profit_prices,
            "invalidation_reason": self.invalidation_reason,
            "risk_pct": round(self.risk_pct, 2),
            "reward_pct": round(self.reward_pct, 2),
            "rr_ratio": round(self.rr_ratio, 2),
            "rr_valid": self.rr_valid,
            "warnings": self.warnings,
            "notes": self.notes
        }


class SLTPCalculator:
    """
    Professional Stop-Loss and Take-Profit calculator.

    Implements L027 methodology with multiple calculation methods
    and automatic R:R validation.
    """

    # R:R thresholds by conviction level
    MIN_RR_BY_CONVICTION = {
        "HIGH": 2.0,      # 8.0-10.0 conviction
        "MEDIUM": 2.5,    # 6.0-7.9 conviction
        "LOW": 3.0,       # < 6.0 conviction
    }

    # Volatility adjustment multipliers by regime
    REGIME_ADJUSTMENTS = {
        "BULL": 1.15,     # 15% wider for shorts in bull market
        "BEAR": 1.0,      # Standard for shorts in bear market
        "CHOP": 1.10,     # 10% wider for choppy conditions
    }

    def __init__(self):
        logger.info("SLTPCalculator initialized")

    def calculate_sr_based_sl(
        self,
        entry_price: float,
        nearest_resistance: float,
        buffer_pct: float = 2.0,
        position_type: str = "SHORT"
    ) -> SLTPResult:
        """
        S/R-based stop loss for short positions.

        For shorts: SL placed above resistance to protect against breakout.
        Buffer prevents getting stopped by wick noise.

        Integrates with: Learning 023 (S/R Detection)

        Args:
            entry_price: Entry price for the position
            nearest_resistance: Nearest resistance level above price
            buffer_pct: Buffer percentage above resistance (default 2%)
            position_type: SHORT or LONG

        Returns:
            SLTPResult with stop loss details
        """
        if position_type == "SHORT":
            sl_price = nearest_resistance * (1 + buffer_pct / 100)
            sl_distance_pct = ((sl_price - entry_price) / entry_price) * 100
            invalidation = f"Price breaks resistance at ${nearest_resistance:.4f}"
        else:
            # For longs, SL below support
            sl_price = nearest_resistance * (1 - buffer_pct / 100)
            sl_distance_pct = ((entry_price - sl_price) / entry_price) * 100
            invalidation = f"Price breaks support at ${nearest_resistance:.4f}"

        return SLTPResult(
            method=SLMethod.SR_BASED,
            stop_loss_price=sl_price,
            invalidation_reason=invalidation,
            risk_pct=sl_distance_pct,
            notes=f"Buffer: {buffer_pct}%"
        )

    def calculate_ma_based_sl(
        self,
        entry_price: float,
        ma_50: Optional[float],
        ma_200: Optional[float],
        position_type: str = "SHORT",
        buffer_pct: float = 2.0
    ) -> SLTPResult:
        """
        MA-based stop loss using dynamic S/R.

        For shorts: SL above nearest MA acting as resistance.
        MA acts as "invalidation line" - if price reclaims MA, thesis fails.

        Integrates with: ta_aggregator.py (SMA50, SMA200)

        Args:
            entry_price: Entry price for the position
            ma_50: 50-period moving average
            ma_200: 200-period moving average
            position_type: SHORT or LONG
            buffer_pct: Buffer percentage (default 2%)

        Returns:
            SLTPResult with MA-based stop loss
        """
        if position_type == "SHORT":
            # For shorts, find MAs above price (resistance)
            mas_above = [ma for ma in [ma_50, ma_200] if ma and ma > entry_price]

            if not mas_above:
                return SLTPResult(
                    method=SLMethod.MA_BASED,
                    stop_loss_price=None,
                    notes="No MA above entry for short SL reference"
                )

            nearest_ma = min(mas_above)
            sl_price = nearest_ma * (1 + buffer_pct / 100)
            sl_distance_pct = ((sl_price - entry_price) / entry_price) * 100

            return SLTPResult(
                method=SLMethod.MA_BASED,
                stop_loss_price=sl_price,
                invalidation_reason=f"Price reclaims MA at ${nearest_ma:.4f}",
                risk_pct=sl_distance_pct,
                notes=f"Reference MA: {nearest_ma:.4f}"
            )
        else:
            # For longs, find MAs below price (support)
            mas_below = [ma for ma in [ma_50, ma_200] if ma and ma < entry_price]

            if not mas_below:
                return SLTPResult(
                    method=SLMethod.MA_BASED,
                    stop_loss_price=None,
                    notes="No MA below entry for long SL reference"
                )

            nearest_ma = max(mas_below)
            sl_price = nearest_ma * (1 - buffer_pct / 100)
            sl_distance_pct = ((entry_price - sl_price) / entry_price) * 100

            return SLTPResult(
                method=SLMethod.MA_BASED,
                stop_loss_price=sl_price,
                invalidation_reason=f"Price loses MA at ${nearest_ma:.4f}",
                risk_pct=sl_distance_pct,
                notes=f"Reference MA: {nearest_ma:.4f}"
            )

    def get_rsi_warning(
        self,
        current_rsi: float,
        position_type: str = "SHORT"
    ) -> Dict[str, Any]:
        """
        RSI-based stop loss warning for shorts.

        When RSI enters oversold territory on a short, risk of bounce increases.
        This is a WARNING indicator, not automatic SL trigger.

        Integrates with: ta_aggregator.py (RSI_14)

        Args:
            current_rsi: Current RSI value (0-100)
            position_type: SHORT or LONG

        Returns:
            Dict with RSI warning info
        """
        if position_type == "SHORT":
            if current_rsi <= 30:
                return {
                    "rsi_warning": RSIWarningLevel.CRITICAL.value,
                    "rsi_value": current_rsi,
                    "message": "RSI oversold - High bounce risk. Consider tightening SL or taking partial profit.",
                    "action": "TIGHTEN_SL"
                }
            elif current_rsi <= 40:
                return {
                    "rsi_warning": RSIWarningLevel.ELEVATED.value,
                    "rsi_value": current_rsi,
                    "message": "RSI approaching oversold. Monitor for bounce signals.",
                    "action": "MONITOR"
                }
        else:  # LONG
            if current_rsi >= 70:
                return {
                    "rsi_warning": RSIWarningLevel.CRITICAL.value,
                    "rsi_value": current_rsi,
                    "message": "RSI overbought - High reversal risk. Consider tightening SL or taking partial profit.",
                    "action": "TIGHTEN_SL"
                }
            elif current_rsi >= 60:
                return {
                    "rsi_warning": RSIWarningLevel.ELEVATED.value,
                    "rsi_value": current_rsi,
                    "message": "RSI approaching overbought. Monitor for reversal signals.",
                    "action": "MONITOR"
                }

        return {
            "rsi_warning": RSIWarningLevel.NORMAL.value,
            "rsi_value": current_rsi,
            "action": "HOLD"
        }

    def calculate_bb_based_sl(
        self,
        entry_price: float,
        bb_upper: float,
        bb_lower: float,
        position_type: str = "SHORT",
        sl_buffer_pct: float = 1.0,
        tp_buffer_pct: float = 2.0
    ) -> SLTPResult:
        """
        Bollinger Band-based stop loss.

        For shorts: SL above upper band (volatility breakout protection).
        For shorts TP: Near lower band (mean reversion target).

        Integrates with: ta_aggregator.py (BB_UPPER, BB_LOWER)

        Args:
            entry_price: Entry price
            bb_upper: Upper Bollinger Band
            bb_lower: Lower Bollinger Band
            position_type: SHORT or LONG
            sl_buffer_pct: Buffer above upper band for SL
            tp_buffer_pct: Buffer above lower band for TP

        Returns:
            SLTPResult with BB-based SL/TP
        """
        if position_type == "SHORT":
            sl_price = bb_upper * (1 + sl_buffer_pct / 100)
            tp_price = bb_lower * (1 + tp_buffer_pct / 100)
            sl_distance_pct = ((sl_price - entry_price) / entry_price) * 100
            reward_pct = ((entry_price - tp_price) / entry_price) * 100

            return SLTPResult(
                method=SLMethod.BB_BASED,
                stop_loss_price=sl_price,
                take_profit_prices=[{"price": tp_price, "size_pct": 100, "reason": "Lower BB target"}],
                invalidation_reason=f"Price breaks upper BB at ${bb_upper:.4f}",
                risk_pct=sl_distance_pct,
                reward_pct=reward_pct,
                notes=f"BB Upper: {bb_upper:.4f}, BB Lower: {bb_lower:.4f}"
            )
        else:
            sl_price = bb_lower * (1 - sl_buffer_pct / 100)
            tp_price = bb_upper * (1 - tp_buffer_pct / 100)
            sl_distance_pct = ((entry_price - sl_price) / entry_price) * 100
            reward_pct = ((tp_price - entry_price) / entry_price) * 100

            return SLTPResult(
                method=SLMethod.BB_BASED,
                stop_loss_price=sl_price,
                take_profit_prices=[{"price": tp_price, "size_pct": 100, "reason": "Upper BB target"}],
                invalidation_reason=f"Price breaks lower BB at ${bb_lower:.4f}",
                risk_pct=sl_distance_pct,
                reward_pct=reward_pct,
                notes=f"BB Upper: {bb_upper:.4f}, BB Lower: {bb_lower:.4f}"
            )

    def calculate_sr_based_tp(
        self,
        entry_price: float,
        support_levels: List[float],
        partial_tp_enabled: bool = True,
        position_type: str = "SHORT"
    ) -> List[Dict[str, Any]]:
        """
        S/R-based take profit for positions.

        For shorts: TP placed at support levels where price may bounce.
        Multiple TPs recommended for partial profit taking.

        Integrates with: Learning 023 (S/R Detection)

        Args:
            entry_price: Entry price
            support_levels: List of support levels
            partial_tp_enabled: Enable partial TP at multiple levels
            position_type: SHORT or LONG

        Returns:
            List of TP targets with size and reason
        """
        if position_type == "SHORT":
            # Filter supports below entry
            valid_levels = [s for s in support_levels if s < entry_price]
            valid_levels.sort(reverse=True)  # Nearest first
        else:
            # Filter resistance above entry
            valid_levels = [r for r in support_levels if r > entry_price]
            valid_levels.sort()  # Nearest first

        if not valid_levels:
            return []

        if partial_tp_enabled and len(valid_levels) >= 2:
            return [
                {"price": valid_levels[0], "size_pct": 50, "reason": "First S/R level"},
                {"price": valid_levels[1], "size_pct": 50, "reason": "Second S/R level"}
            ]
        elif len(valid_levels) >= 1:
            return [
                {"price": valid_levels[0], "size_pct": 100, "reason": "Nearest S/R level"}
            ]

        return []

    def validate_risk_reward(
        self,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        position_type: str = "SHORT",
        min_rr_ratio: float = 2.0
    ) -> Dict[str, Any]:
        """
        Validate trade meets minimum risk-to-reward requirements.

        CRITICAL: Do NOT enter trades with R:R < 1:2.

        For shorts:
        - Risk = SL price - Entry price (amount price can move against)
        - Reward = Entry price - TP price (potential profit)

        Args:
            entry_price: Entry price
            stop_loss_price: Stop loss price
            take_profit_price: Take profit price
            position_type: SHORT or LONG
            min_rr_ratio: Minimum R:R ratio (default 2.0)

        Returns:
            Dict with R:R validation results
        """
        if position_type == "SHORT":
            risk = stop_loss_price - entry_price
            reward = entry_price - take_profit_price
        else:
            risk = entry_price - stop_loss_price
            reward = take_profit_price - entry_price

        if risk <= 0:
            return {
                "valid": False,
                "validation": RRValidation.INVALID.value,
                "reason": "Invalid SL placement (no risk calculated)",
                "rr_ratio": 0.0
            }

        if reward <= 0:
            return {
                "valid": False,
                "validation": RRValidation.INVALID.value,
                "reason": "Invalid TP placement (no reward calculated)",
                "rr_ratio": 0.0
            }

        rr_ratio = reward / risk

        result = {
            "entry_price": entry_price,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "risk_amount": risk,
            "reward_amount": reward,
            "risk_pct": abs(risk / entry_price * 100),
            "reward_pct": abs(reward / entry_price * 100),
            "rr_ratio": round(rr_ratio, 2),
            "min_required": min_rr_ratio,
            "valid": rr_ratio >= min_rr_ratio
        }

        if rr_ratio >= min_rr_ratio:
            result["validation"] = RRValidation.VALID.value
        elif rr_ratio >= min_rr_ratio * 0.8:  # Within 20% of threshold
            result["validation"] = RRValidation.MARGINAL.value
            result["warning"] = f"R:R ratio {rr_ratio:.2f} is marginal (near {min_rr_ratio} threshold)"
        else:
            result["validation"] = RRValidation.INVALID.value
            result["warning"] = f"R:R ratio {rr_ratio:.2f} below minimum {min_rr_ratio}. Adjust SL/TP or skip trade."

        return result

    def calculate_tge_exit_levels(
        self,
        launch_price: float,
        current_price: float,
        token_age_hours: int,
        conviction_score: float
    ) -> SLTPResult:
        """
        Calculate SL/TP for TGE tokens with limited history.

        Uses percentage-based exits for new tokens,
        transitioning to TA-based exits as data accumulates.

        Args:
            launch_price: TGE launch price
            current_price: Current price
            token_age_hours: Hours since TGE
            conviction_score: DACLE conviction score (0-10)

        Returns:
            SLTPResult with TGE-appropriate SL/TP
        """
        if token_age_hours <= 24:
            # Phase 1: First 24 hours - use % from launch
            sl_buffer_pct = 15 if conviction_score >= 8.0 else 20
            sl_price = launch_price * (1 + sl_buffer_pct / 100)

            tp_1_pct = -20
            tp_2_pct = -40
            tp_1_price = launch_price * (1 + tp_1_pct / 100)
            tp_2_price = launch_price * (1 + tp_2_pct / 100)

            sl_distance_pct = ((sl_price - current_price) / current_price) * 100

            return SLTPResult(
                method=SLMethod.TGE_PERCENTAGE,
                stop_loss_price=sl_price,
                take_profit_prices=[
                    {"price": tp_1_price, "size_pct": 50, "reason": f"TP1: {tp_1_pct}% from launch"},
                    {"price": tp_2_price, "size_pct": 50, "reason": f"TP2: {tp_2_pct}% from launch"}
                ],
                invalidation_reason=f"Price exceeds launch by {sl_buffer_pct}%",
                risk_pct=sl_distance_pct,
                notes=f"TGE Phase 1: Using % targets (age: {token_age_hours}h)"
            )

        elif token_age_hours <= 168:  # 1-7 days
            # Phase 2: Use 24h high as reference
            estimated_24h_high = launch_price * 1.10  # Placeholder - should use actual data

            sl_price = estimated_24h_high * 1.05
            tp_1_price = current_price * 0.85
            tp_2_price = current_price * 0.70

            sl_distance_pct = ((sl_price - current_price) / current_price) * 100

            return SLTPResult(
                method=SLMethod.TGE_24H_HIGH,
                stop_loss_price=sl_price,
                take_profit_prices=[
                    {"price": tp_1_price, "size_pct": 50, "reason": "TP1: -15% from current"},
                    {"price": tp_2_price, "size_pct": 50, "reason": "TP2: -30% from current"}
                ],
                invalidation_reason="Price breaks 24h high + 5% buffer",
                risk_pct=sl_distance_pct,
                notes=f"TGE Phase 2: Using 24h high (age: {token_age_hours}h)"
            )

        else:
            # Phase 3: Use standard TA methods
            return SLTPResult(
                method=SLMethod.STANDARD_TA,
                stop_loss_price=None,
                notes=f"TGE Phase 3: Use S/R, MA, and BB-based methods (age: {token_age_hours}h)"
            )

    def calculate_trailing_stop(
        self,
        entry_price: float,
        current_price: float,
        lowest_price_since_entry: float,
        initial_sl_price: float,
        trailing_pct: float = 5.0,
        position_type: str = "SHORT"
    ) -> Dict[str, Any]:
        """
        Trailing stop loss calculator.

        For shorts: Trail downward as price drops.
        Lock in gains while allowing continuation.

        Args:
            entry_price: Entry price
            current_price: Current price
            lowest_price_since_entry: Lowest price seen since entry (for shorts)
            initial_sl_price: Initial stop loss price
            trailing_pct: How far behind the low point to trail
            position_type: SHORT or LONG

        Returns:
            Dict with trailing stop info
        """
        if position_type == "SHORT":
            # Calculate trailing SL from lowest price
            trailing_sl = lowest_price_since_entry * (1 + trailing_pct / 100)

            # Use the LOWER of initial SL and trailing SL (for shorts)
            effective_sl = min(initial_sl_price, trailing_sl)

            # Calculate profit locked in
            profit_locked_pct = ((entry_price - effective_sl) / entry_price) * 100

            return {
                "method": SLMethod.TRAILING.value,
                "initial_sl": initial_sl_price,
                "trailing_sl": trailing_sl,
                "effective_sl": effective_sl,
                "trailing_pct": trailing_pct,
                "lowest_price_since_entry": lowest_price_since_entry,
                "profit_locked_pct": max(0, profit_locked_pct),
                "status": "TRAILING_ACTIVE" if trailing_sl < initial_sl_price else "USING_INITIAL"
            }

        else:  # LONG
            highest_price_since_entry = lowest_price_since_entry  # Rename for clarity
            trailing_sl = highest_price_since_entry * (1 - trailing_pct / 100)

            effective_sl = max(initial_sl_price, trailing_sl)

            profit_locked_pct = ((effective_sl - entry_price) / entry_price) * 100

            return {
                "method": SLMethod.TRAILING.value,
                "initial_sl": initial_sl_price,
                "trailing_sl": trailing_sl,
                "effective_sl": effective_sl,
                "trailing_pct": trailing_pct,
                "highest_price_since_entry": highest_price_since_entry,
                "profit_locked_pct": max(0, profit_locked_pct),
                "status": "TRAILING_ACTIVE" if trailing_sl > initial_sl_price else "USING_INITIAL"
            }

    def adjust_sl_for_volatility(
        self,
        base_sl_pct: float,
        btc_regime: str,
        atr_percentile: float,
        news_event_active: bool = False
    ) -> Dict[str, Any]:
        """
        Adjust stop loss width based on market conditions.

        ATR percentile indicates current volatility vs historical.
        Higher ATR = wider stops needed.

        Args:
            base_sl_pct: Base stop loss percentage
            btc_regime: BULL, BEAR, or CHOP
            atr_percentile: 0-100, where ATR ranks vs history
            news_event_active: Whether a news event is active

        Returns:
            Dict with adjusted SL info
        """
        volatility_multiplier = 1.0

        # Regime adjustment
        volatility_multiplier *= self.REGIME_ADJUSTMENTS.get(btc_regime, 1.0)

        # ATR adjustment (high vol = wider stops)
        if atr_percentile > 80:
            volatility_multiplier *= 1.20  # 20% wider in high vol
        elif atr_percentile > 60:
            volatility_multiplier *= 1.10  # 10% wider

        # News event adjustment
        if news_event_active:
            volatility_multiplier *= 1.25  # 25% wider during news

        adjusted_sl_pct = base_sl_pct * volatility_multiplier

        return {
            "base_sl_pct": base_sl_pct,
            "adjusted_sl_pct": round(adjusted_sl_pct, 2),
            "volatility_multiplier": round(volatility_multiplier, 2),
            "btc_regime": btc_regime,
            "atr_percentile": atr_percentile,
            "news_event_active": news_event_active
        }

    def calculate_comprehensive_sl_tp(
        self,
        entry_price: float,
        ta_data: Dict[str, Any],
        token_age_hours: int = 0,
        conviction_score: float = 5.0,
        position_type: str = "SHORT",
        launch_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive SL/TP using all available methods.

        Combines S/R, MA, BB methods and selects the most conservative.
        Validates R:R ratio and adds warnings.

        Args:
            entry_price: Entry price
            ta_data: TA analysis data (resistance, support, MAs, BB, RSI)
            token_age_hours: Hours since TGE (0 for mature tokens)
            conviction_score: DACLE conviction score
            position_type: SHORT or LONG
            launch_price: TGE launch price (for TGE tokens)

        Returns:
            Dict with comprehensive SL/TP analysis
        """
        results = {
            "entry_price": entry_price,
            "position_type": position_type,
            "methods": {},
            "recommended_sl": None,
            "recommended_tp": [],
            "rr_validation": None,
            "rsi_warning": None,
            "warnings": []
        }

        # 1. S/R-based SL
        if ta_data.get("nearest_resistance") and position_type == "SHORT":
            sr_result = self.calculate_sr_based_sl(
                entry_price=entry_price,
                nearest_resistance=ta_data["nearest_resistance"],
                position_type=position_type
            )
            results["methods"]["sr_based"] = sr_result.to_dict()

        # 2. MA-based SL
        ma_50 = ta_data.get("sma_50") or ta_data.get("ema_50")
        ma_200 = ta_data.get("sma_200") or ta_data.get("ema_200")
        if ma_50 or ma_200:
            ma_result = self.calculate_ma_based_sl(
                entry_price=entry_price,
                ma_50=ma_50,
                ma_200=ma_200,
                position_type=position_type
            )
            results["methods"]["ma_based"] = ma_result.to_dict()

        # 3. Bollinger Band-based SL
        bb_upper = ta_data.get("bb_upper")
        bb_lower = ta_data.get("bb_lower")
        if bb_upper and bb_lower:
            bb_result = self.calculate_bb_based_sl(
                entry_price=entry_price,
                bb_upper=bb_upper,
                bb_lower=bb_lower,
                position_type=position_type
            )
            results["methods"]["bb_based"] = bb_result.to_dict()

        # 4. TGE-specific (if applicable)
        if token_age_hours > 0 and token_age_hours <= 168 and launch_price:
            tge_result = self.calculate_tge_exit_levels(
                launch_price=launch_price,
                current_price=entry_price,
                token_age_hours=token_age_hours,
                conviction_score=conviction_score
            )
            results["methods"]["tge_based"] = tge_result.to_dict()

        # 5. S/R-based TP
        support_levels = ta_data.get("support_levels", [])
        if support_levels:
            tp_levels = self.calculate_sr_based_tp(
                entry_price=entry_price,
                support_levels=support_levels,
                position_type=position_type
            )
            results["sr_tp_levels"] = tp_levels

        # 6. RSI warning
        current_rsi = ta_data.get("rsi_14") or ta_data.get("rsi")
        if current_rsi:
            results["rsi_warning"] = self.get_rsi_warning(current_rsi, position_type)

        # Select recommended SL (most conservative for shorts = highest)
        sl_prices = []
        for method, method_result in results["methods"].items():
            if method_result.get("stop_loss_price"):
                sl_prices.append((method, method_result["stop_loss_price"]))

        if sl_prices:
            if position_type == "SHORT":
                # For shorts, most conservative = highest SL
                recommended = min(sl_prices, key=lambda x: x[1])  # Actually want tightest
            else:
                # For longs, most conservative = lowest SL
                recommended = max(sl_prices, key=lambda x: x[1])

            results["recommended_sl"] = {
                "price": recommended[1],
                "method": recommended[0],
                "all_sl_prices": dict(sl_prices)
            }

        # Set recommended TP
        if results.get("sr_tp_levels"):
            results["recommended_tp"] = results["sr_tp_levels"]
        elif "tge_based" in results["methods"]:
            results["recommended_tp"] = results["methods"]["tge_based"].get("take_profit_prices", [])

        # 7. Validate R:R ratio
        if results["recommended_sl"] and results["recommended_tp"]:
            avg_tp = sum(tp["price"] for tp in results["recommended_tp"]) / len(results["recommended_tp"])

            # Determine min R:R based on conviction
            if conviction_score >= 8.0:
                min_rr = self.MIN_RR_BY_CONVICTION["HIGH"]
            elif conviction_score >= 6.0:
                min_rr = self.MIN_RR_BY_CONVICTION["MEDIUM"]
            else:
                min_rr = self.MIN_RR_BY_CONVICTION["LOW"]

            rr_result = self.validate_risk_reward(
                entry_price=entry_price,
                stop_loss_price=results["recommended_sl"]["price"],
                take_profit_price=avg_tp,
                position_type=position_type,
                min_rr_ratio=min_rr
            )
            results["rr_validation"] = rr_result

            if not rr_result["valid"]:
                results["warnings"].append(rr_result.get("warning", "R:R ratio below minimum"))

        return results


# Convenience function
def calculate_exit_levels(
    entry_price: float,
    ta_data: Dict[str, Any],
    token_age_hours: int = 0,
    conviction_score: float = 5.0,
    position_type: str = "SHORT",
    launch_price: Optional[float] = None
) -> Dict[str, Any]:
    """
    Convenience function for comprehensive SL/TP calculation.

    Args:
        entry_price: Entry price
        ta_data: TA analysis data
        token_age_hours: Hours since TGE
        conviction_score: DACLE conviction score
        position_type: SHORT or LONG
        launch_price: TGE launch price

    Returns:
        Dict with comprehensive exit level analysis
    """
    calculator = SLTPCalculator()
    return calculator.calculate_comprehensive_sl_tp(
        entry_price=entry_price,
        ta_data=ta_data,
        token_age_hours=token_age_hours,
        conviction_score=conviction_score,
        position_type=position_type,
        launch_price=launch_price
    )


if __name__ == "__main__":
    # Test example
    logging.basicConfig(level=logging.INFO)

    calc = SLTPCalculator()

    # Test S/R-based SL
    result = calc.calculate_sr_based_sl(
        entry_price=0.090,
        nearest_resistance=0.115,
        position_type="SHORT"
    )
    print(f"S/R-based SL: {result.to_dict()}")

    # Test R:R validation
    rr = calc.validate_risk_reward(
        entry_price=0.090,
        stop_loss_price=0.117,
        take_profit_price=0.068,
        position_type="SHORT"
    )
    print(f"R:R Validation: {rr}")
