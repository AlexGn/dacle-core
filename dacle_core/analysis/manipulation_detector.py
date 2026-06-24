#!/usr/bin/env python3
"""
Market Manipulation Detection Module - Session 316

Implements Kaizen Market Manipulation learnings for TGE short analysis:
- L089: Wash Trading Detection
- L090: Liquidation Cascade Awareness
- L091: Pre-Listing Insider Front-Running Detection
- L092: Mental Stop Loss Strategy
- L093: Hype-Driven Trade Filter (Celebrity/Influencer Pump)
- L094: Spoofing and Order Book Manipulation Detection

Usage:
    from dacle_core.analysis.manipulation_detector import ManipulationDetector

    detector = ManipulationDetector()
    result = detector.analyze_token(
        token_symbol="MEME",
        volume_24h=5_000_000,
        market_cap=35_000_000,
        twitter_followers=800,
        telegram_members=200,
        price_change_24h=0.5,
        exchange_distribution={"mexc": 0.98, "gate": 0.02},
        long_short_ratio=72.5,
        funding_rate=0.12,
        open_interest_change_24h=55.0,
        top_wallet_change_7d=18.5,
        dex_volume_multiplier=6.2,
        new_whale_wallets=12,
        celebrity_mentions=[],
        influencer_mentions=3,
        social_velocity_score=85,
        order_book_largest_wall=500_000,
        daily_volume=200_000
    )

Author: Claude Code (Session 316)
Date: 2026-01-11
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ManipulationRisk(Enum):
    """Risk level classification for manipulation signals."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class ManipulationType(Enum):
    """Types of market manipulation detected."""
    WASH_TRADING = "WASH_TRADING"                   # L089
    LIQUIDATION_CASCADE = "LIQUIDATION_CASCADE"     # L090
    INSIDER_FRONT_RUNNING = "INSIDER_FRONT_RUNNING" # L091
    STOP_HUNTING = "STOP_HUNTING"                   # L092
    HYPE_PUMP = "HYPE_PUMP"                         # L093
    SPOOFING = "SPOOFING"                           # L094


class StopLossType(Enum):
    """L092: Mental vs Hard stop loss recommendation."""
    MENTAL = "MENTAL"   # Manual close on 4H confirmation
    HARD = "HARD"       # Exchange order


@dataclass
class WashTradingResult:
    """L089: Wash trading detection result."""
    risk_level: ManipulationRisk
    signals_detected: List[str]
    volume_social_ratio: float          # Volume per Twitter follower
    exchange_concentration_pct: float   # % on single exchange
    price_volume_correlation: float     # High volume + flat price = suspicious
    conviction_modifier: float          # Applied to final score
    recommendation: str                 # SKIP, CAUTION, PROCEED


@dataclass
class LiquidationCascadeResult:
    """L090: Liquidation cascade awareness result."""
    risk_level: ManipulationRisk
    long_pct: float
    short_pct: float
    ls_ratio: float
    funding_rate: float
    is_crowded_trade: bool
    dominant_side: str                  # LONG or SHORT
    position_modifier: float            # 0.5-1.0
    sl_buffer_multiplier: float         # 1.0-1.5
    leverage_cap: int                   # Max leverage allowed
    warnings: List[str]


@dataclass
class InsiderActivityResult:
    """L091: Pre-listing insider front-running detection result."""
    risk_level: ManipulationRisk
    top_wallet_change_pct: float        # % change in top 100 holdings
    dex_volume_multiplier: float        # vs normal volume
    new_whale_wallets: int              # New wallets >$100K
    entry_delay_hours: int              # Recommended delay
    reasoning: str


@dataclass
class HypeFilterResult:
    """L093: Hype-driven trade filter result."""
    hype_level: ManipulationRisk
    twitter_velocity: int               # Mentions per hour
    celebrity_mentions: List[str]
    influencer_count: int
    google_trends_score: int            # 0-100
    is_coordinated_pump: bool
    wait_period_days: int
    recommendation: str                 # SKIP, CAUTION, PROCEED


@dataclass
class SpoofingResult:
    """L094: Spoofing and order book manipulation result."""
    risk_level: ManipulationRisk
    largest_wall_usd: float
    wall_to_volume_ratio: float
    is_symmetric_book: bool
    mm_activity_level: str              # HIGH, MEDIUM, LOW
    entry_timing_note: str


@dataclass
class StopLossRecommendation:
    """L092: Mental stop loss strategy recommendation."""
    sl_type: StopLossType
    reasoning: str
    alert_levels: Dict[str, float]      # warning, critical
    execution_method: str               # 4H_CLOSE, MARKET, LIMIT


@dataclass
class ManipulationAnalysisResult:
    """Combined result of all manipulation checks."""
    token_symbol: str
    timestamp: str

    # Individual results
    wash_trading: Optional[WashTradingResult]
    liquidation_cascade: Optional[LiquidationCascadeResult]
    insider_activity: Optional[InsiderActivityResult]
    hype_filter: Optional[HypeFilterResult]
    spoofing: Optional[SpoofingResult]
    stop_loss_recommendation: Optional[StopLossRecommendation]

    # Aggregated scores
    overall_manipulation_risk: ManipulationRisk
    total_conviction_modifier: float    # Sum of all modifiers (can be negative)
    position_size_multiplier: float     # Final multiplier after all checks

    # Recommendations
    should_skip: bool
    skip_reasons: List[str]
    warnings: List[str]
    entry_delay_hours: int              # 0 = proceed, >0 = wait

    # Labels for alert display
    risk_labels: List[str]              # [WASH TRADE RISK], [CROWDED TRADE], etc.

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "token_symbol": self.token_symbol,
            "timestamp": self.timestamp,
            "overall_manipulation_risk": self.overall_manipulation_risk.value,
            "total_conviction_modifier": self.total_conviction_modifier,
            "position_size_multiplier": self.position_size_multiplier,
            "should_skip": self.should_skip,
            "skip_reasons": self.skip_reasons,
            "warnings": self.warnings,
            "entry_delay_hours": self.entry_delay_hours,
            "risk_labels": self.risk_labels,
            "wash_trading": {
                "risk_level": self.wash_trading.risk_level.value,
                "signals": self.wash_trading.signals_detected,
                "conviction_modifier": self.wash_trading.conviction_modifier
            } if self.wash_trading else None,
            "liquidation_cascade": {
                "risk_level": self.liquidation_cascade.risk_level.value,
                "ls_ratio": self.liquidation_cascade.ls_ratio,
                "position_modifier": self.liquidation_cascade.position_modifier,
                "sl_buffer": self.liquidation_cascade.sl_buffer_multiplier
            } if self.liquidation_cascade else None,
            "insider_activity": {
                "risk_level": self.insider_activity.risk_level.value,
                "delay_hours": self.insider_activity.entry_delay_hours
            } if self.insider_activity else None,
            "hype_filter": {
                "hype_level": self.hype_filter.hype_level.value,
                "wait_days": self.hype_filter.wait_period_days,
                "is_coordinated": self.hype_filter.is_coordinated_pump
            } if self.hype_filter else None,
            "spoofing": {
                "risk_level": self.spoofing.risk_level.value,
                "wall_ratio": self.spoofing.wall_to_volume_ratio
            } if self.spoofing else None,
            "stop_loss": {
                "type": self.stop_loss_recommendation.sl_type.value,
                "method": self.stop_loss_recommendation.execution_method
            } if self.stop_loss_recommendation else None
        }


class ManipulationDetector:
    """
    Unified market manipulation detection for TGE short analysis.

    Implements L089-L094 from Kaizen Market Manipulation series.
    """

    # L089: Wash Trading Thresholds
    WASH_TRADING_THRESHOLDS = {
        "high_risk": {
            "volume_min": 1_000_000,     # $1M+ volume
            "twitter_max": 1_000,         # <1K followers
            "telegram_max": 500,          # <500 members
            "exchange_concentration": 95, # >95% on one exchange
            "modifier": -1.0
        },
        "medium_risk": {
            "volume_min": 500_000,
            "twitter_max": 5_000,
            "telegram_max": 2_000,
            "exchange_concentration": 85,
            "modifier": -0.5
        }
    }

    # L090: Liquidation Cascade Thresholds
    CASCADE_RISK_RULES = {
        "HIGH": {
            "ls_ratio_threshold": 70,     # >70% on one side
            "funding_threshold": 0.1,     # >0.1% funding
            "oi_spike_threshold": 50,     # >50% OI increase
            "position_modifier": 0.5,
            "sl_buffer": 1.5,
            "leverage_cap": 5
        },
        "MEDIUM": {
            "ls_ratio_threshold": 60,
            "funding_threshold": 0.05,
            "oi_spike_threshold": 30,
            "position_modifier": 0.75,
            "sl_buffer": 1.25,
            "leverage_cap": 10
        },
        "LOW": {
            "ls_ratio_threshold": 0,
            "funding_threshold": 0,
            "oi_spike_threshold": 0,
            "position_modifier": 1.0,
            "sl_buffer": 1.0,
            "leverage_cap": 20
        }
    }

    # L091: Insider Activity Thresholds
    INSIDER_RISK_RULES = {
        "HIGH": {
            "top_wallet_change": 15,      # >15% accumulation
            "dex_multiplier": 5,          # 5x DEX volume
            "new_whales": 10,             # 10+ new large wallets
            "delay_hours": 24
        },
        "MEDIUM": {
            "top_wallet_change": 10,
            "dex_multiplier": 3,
            "new_whales": 5,
            "delay_hours": 12
        },
        "LOW": {
            "top_wallet_change": 5,
            "dex_multiplier": 2,
            "new_whales": 2,
            "delay_hours": 0
        }
    }

    # L092: Mental Stop Loss Thresholds
    MENTAL_SL_THRESHOLDS = {
        "market_cap_max": 50_000_000,    # <$50M = mental SL
        "volume_24h_max": 500_000,       # <$500K volume = mental SL
        "leverage_threshold": 10          # >10x leverage = mental SL
    }

    # L093: Hype Filter Thresholds
    HYPE_THRESHOLDS = {
        "extreme": {
            "twitter_velocity": 1000,     # >1000 mentions/hour
            "google_trends": 75,          # >75/100
            "influencer_count": 3,        # 3+ influencers same day
            "wait_days": 14
        },
        "high": {
            "twitter_velocity": 500,
            "google_trends": 50,
            "influencer_count": 2,
            "wait_days": 7
        },
        "medium": {
            "twitter_velocity": 100,
            "google_trends": 25,
            "influencer_count": 1,
            "wait_days": 0
        }
    }

    # L094: Spoofing Detection Thresholds
    SPOOFING_THRESHOLDS = {
        "wall_to_volume_high": 2.0,      # Wall > 2x daily volume
        "wall_to_volume_medium": 1.0,
        "symmetric_tolerance": 0.3       # Within 30% = symmetric
    }

    def __init__(self):
        """Initialize manipulation detector."""
        self.logger = logging.getLogger(f"{__name__}.ManipulationDetector")

    def analyze_token(
        self,
        token_symbol: str,
        # L089: Wash Trading inputs
        volume_24h: float = 0,
        market_cap: float = 0,
        twitter_followers: int = 0,
        telegram_members: int = 0,
        price_change_24h: float = 0,
        exchange_distribution: Optional[Dict[str, float]] = None,
        # L090: Liquidation Cascade inputs
        long_short_ratio: Optional[float] = None,
        funding_rate: float = 0,
        open_interest_change_24h: float = 0,
        # L091: Insider Activity inputs
        top_wallet_change_7d: float = 0,
        dex_volume_multiplier: float = 1.0,
        new_whale_wallets: int = 0,
        # L093: Hype Filter inputs
        celebrity_mentions: Optional[List[str]] = None,
        influencer_mentions: int = 0,
        social_velocity_score: int = 0,
        google_trends_score: int = 0,
        # L094: Spoofing inputs
        order_book_largest_wall: float = 0,
        daily_volume: float = 0,
        order_book_symmetric: bool = False,
        # Trade context
        leverage: float = 1.0,
        trade_type: str = "SWING"
    ) -> ManipulationAnalysisResult:
        """
        Perform comprehensive manipulation analysis on a token.

        Returns aggregated result with all manipulation checks.
        """
        timestamp = datetime.utcnow().isoformat()

        # Run all detection checks
        wash_result = self._check_wash_trading(
            volume_24h=volume_24h,
            twitter_followers=twitter_followers,
            telegram_members=telegram_members,
            price_change_24h=price_change_24h,
            exchange_distribution=exchange_distribution or {}
        )

        cascade_result = self._check_liquidation_cascade(
            long_short_ratio=long_short_ratio,
            funding_rate=funding_rate,
            oi_change_24h=open_interest_change_24h
        )

        insider_result = self._check_insider_activity(
            top_wallet_change=top_wallet_change_7d,
            dex_multiplier=dex_volume_multiplier,
            new_whales=new_whale_wallets
        )

        hype_result = self._check_hype_filter(
            celebrity_mentions=celebrity_mentions or [],
            influencer_count=influencer_mentions,
            social_velocity=social_velocity_score,
            google_trends=google_trends_score
        )

        spoofing_result = self._check_spoofing(
            largest_wall=order_book_largest_wall,
            daily_volume=daily_volume,
            is_symmetric=order_book_symmetric
        )

        sl_recommendation = self._get_stop_loss_recommendation(
            market_cap=market_cap,
            volume_24h=volume_24h,
            leverage=leverage,
            trade_type=trade_type
        )

        # Aggregate results
        return self._aggregate_results(
            token_symbol=token_symbol,
            timestamp=timestamp,
            wash_trading=wash_result,
            liquidation_cascade=cascade_result,
            insider_activity=insider_result,
            hype_filter=hype_result,
            spoofing=spoofing_result,
            stop_loss=sl_recommendation
        )

    def _check_wash_trading(
        self,
        volume_24h: float,
        twitter_followers: int,
        telegram_members: int,
        price_change_24h: float,
        exchange_distribution: Dict[str, float]
    ) -> WashTradingResult:
        """L089: Detect potential wash trading signals."""
        signals = []
        conviction_modifier = 0.0

        # Volume/Social ratio check
        volume_social_ratio = volume_24h / max(twitter_followers, 1)

        # High risk checks
        high = self.WASH_TRADING_THRESHOLDS["high_risk"]
        if volume_24h > high["volume_min"] and twitter_followers < high["twitter_max"]:
            signals.append("VOLUME_SOCIAL_MISMATCH_HIGH")
            conviction_modifier += high["modifier"]

        # Medium risk checks
        medium = self.WASH_TRADING_THRESHOLDS["medium_risk"]
        if volume_24h > medium["volume_min"] and twitter_followers < medium["twitter_max"]:
            if "VOLUME_SOCIAL_MISMATCH_HIGH" not in signals:
                signals.append("VOLUME_SOCIAL_MISMATCH_MEDIUM")
                conviction_modifier += medium["modifier"]

        # Exchange concentration
        exchange_concentration = 0.0
        if exchange_distribution:
            exchange_concentration = max(exchange_distribution.values()) * 100
            if exchange_concentration > high["exchange_concentration"]:
                signals.append("SINGLE_EXCHANGE_DOMINANCE")
                conviction_modifier += -0.5

        # Price stability with high volume (suspicious)
        price_volume_correlation = 0.0
        if volume_24h > 500_000 and abs(price_change_24h) < 1.0:
            signals.append("SUSPICIOUS_VOLUME_PATTERN")
            price_volume_correlation = volume_24h / max(abs(price_change_24h) + 0.1, 0.1)
            conviction_modifier += -0.5

        # Determine risk level
        if conviction_modifier <= -1.5:
            risk_level = ManipulationRisk.HIGH
            recommendation = "SKIP"
        elif conviction_modifier <= -0.5:
            risk_level = ManipulationRisk.MEDIUM
            recommendation = "CAUTION"
        else:
            risk_level = ManipulationRisk.LOW
            recommendation = "PROCEED"

        return WashTradingResult(
            risk_level=risk_level,
            signals_detected=signals,
            volume_social_ratio=volume_social_ratio,
            exchange_concentration_pct=exchange_concentration,
            price_volume_correlation=price_volume_correlation,
            conviction_modifier=conviction_modifier,
            recommendation=recommendation
        )

    def _check_liquidation_cascade(
        self,
        long_short_ratio: Optional[float],
        funding_rate: float,
        oi_change_24h: float
    ) -> LiquidationCascadeResult:
        """L090: Detect liquidation cascade risk."""
        warnings = []

        # Calculate L/S percentages
        if long_short_ratio is not None:
            long_pct = long_short_ratio
            short_pct = 100 - long_short_ratio
            ls_ratio = long_pct / max(short_pct, 1)
            dominant_side = "LONG" if long_pct > short_pct else "SHORT"
        else:
            long_pct = 50.0
            short_pct = 50.0
            ls_ratio = 1.0
            dominant_side = "BALANCED"

        # Determine risk level
        high = self.CASCADE_RISK_RULES["HIGH"]
        medium = self.CASCADE_RISK_RULES["MEDIUM"]

        is_crowded = max(long_pct, short_pct) > 60

        if max(long_pct, short_pct) > high["ls_ratio_threshold"]:
            risk_level = ManipulationRisk.HIGH
            rules = high
            warnings.append(f"{dominant_side} positions at {max(long_pct, short_pct):.1f}% - HIGH cascade risk")
        elif max(long_pct, short_pct) > medium["ls_ratio_threshold"]:
            risk_level = ManipulationRisk.MEDIUM
            rules = medium
            warnings.append(f"{dominant_side} positions at {max(long_pct, short_pct):.1f}% - MEDIUM cascade risk")
        else:
            risk_level = ManipulationRisk.LOW
            rules = self.CASCADE_RISK_RULES["LOW"]

        # Funding rate check
        if abs(funding_rate) > high["funding_threshold"]:
            warnings.append(f"Extreme funding rate: {funding_rate:.3f}% - crowded trade")
            if risk_level == ManipulationRisk.MEDIUM:
                risk_level = ManipulationRisk.HIGH
                rules = high

        # OI spike check
        if oi_change_24h > high["oi_spike_threshold"]:
            warnings.append(f"OI spike: +{oi_change_24h:.1f}% in 24h - leverage building")
            if risk_level == ManipulationRisk.MEDIUM:
                risk_level = ManipulationRisk.HIGH
                rules = high

        return LiquidationCascadeResult(
            risk_level=risk_level,
            long_pct=long_pct,
            short_pct=short_pct,
            ls_ratio=ls_ratio,
            funding_rate=funding_rate,
            is_crowded_trade=is_crowded,
            dominant_side=dominant_side,
            position_modifier=rules["position_modifier"],
            sl_buffer_multiplier=rules["sl_buffer"],
            leverage_cap=rules["leverage_cap"],
            warnings=warnings
        )

    def _check_insider_activity(
        self,
        top_wallet_change: float,
        dex_multiplier: float,
        new_whales: int
    ) -> InsiderActivityResult:
        """L091: Detect pre-listing insider front-running."""
        high = self.INSIDER_RISK_RULES["HIGH"]
        medium = self.INSIDER_RISK_RULES["MEDIUM"]

        # Score based on signals
        signals_high = 0
        signals_medium = 0

        if top_wallet_change > high["top_wallet_change"]:
            signals_high += 1
        elif top_wallet_change > medium["top_wallet_change"]:
            signals_medium += 1

        if dex_multiplier > high["dex_multiplier"]:
            signals_high += 1
        elif dex_multiplier > medium["dex_multiplier"]:
            signals_medium += 1

        if new_whales > high["new_whales"]:
            signals_high += 1
        elif new_whales > medium["new_whales"]:
            signals_medium += 1

        # Determine risk level
        if signals_high >= 2:
            risk_level = ManipulationRisk.HIGH
            delay_hours = high["delay_hours"]
            reasoning = "Heavy insider accumulation detected - expect delayed dump"
        elif signals_high >= 1 or signals_medium >= 2:
            risk_level = ManipulationRisk.MEDIUM
            delay_hours = medium["delay_hours"]
            reasoning = "Moderate insider activity - monitor distribution"
        else:
            risk_level = ManipulationRisk.LOW
            delay_hours = 0
            reasoning = "Normal activity - proceed with standard timing"

        return InsiderActivityResult(
            risk_level=risk_level,
            top_wallet_change_pct=top_wallet_change,
            dex_volume_multiplier=dex_multiplier,
            new_whale_wallets=new_whales,
            entry_delay_hours=delay_hours,
            reasoning=reasoning
        )

    def _check_hype_filter(
        self,
        celebrity_mentions: List[str],
        influencer_count: int,
        social_velocity: int,
        google_trends: int
    ) -> HypeFilterResult:
        """L093: Detect hype-driven pump patterns."""
        extreme = self.HYPE_THRESHOLDS["extreme"]
        high = self.HYPE_THRESHOLDS["high"]

        # Celebrity check is instant HIGH risk
        if celebrity_mentions:
            return HypeFilterResult(
                hype_level=ManipulationRisk.EXTREME,
                twitter_velocity=social_velocity,
                celebrity_mentions=celebrity_mentions,
                influencer_count=influencer_count,
                google_trends_score=google_trends,
                is_coordinated_pump=True,
                wait_period_days=14,
                recommendation="SKIP"
            )

        # Coordinated influencer pump
        is_coordinated = influencer_count >= extreme["influencer_count"]

        # Score signals
        extreme_signals = 0
        high_signals = 0

        if social_velocity > extreme["twitter_velocity"]:
            extreme_signals += 1
        elif social_velocity > high["twitter_velocity"]:
            high_signals += 1

        if google_trends > extreme["google_trends"]:
            extreme_signals += 1
        elif google_trends > high["google_trends"]:
            high_signals += 1

        if influencer_count >= extreme["influencer_count"]:
            extreme_signals += 1
        elif influencer_count >= high["influencer_count"]:
            high_signals += 1

        # Determine hype level
        if extreme_signals >= 2 or is_coordinated:
            hype_level = ManipulationRisk.HIGH
            wait_days = extreme["wait_days"] if extreme_signals >= 2 else high["wait_days"]
            recommendation = "SKIP"
        elif high_signals >= 2:
            hype_level = ManipulationRisk.MEDIUM
            wait_days = high["wait_days"]
            recommendation = "CAUTION"
        else:
            hype_level = ManipulationRisk.LOW
            wait_days = 0
            recommendation = "PROCEED"

        return HypeFilterResult(
            hype_level=hype_level,
            twitter_velocity=social_velocity,
            celebrity_mentions=celebrity_mentions,
            influencer_count=influencer_count,
            google_trends_score=google_trends,
            is_coordinated_pump=is_coordinated,
            wait_period_days=wait_days,
            recommendation=recommendation
        )

    def _check_spoofing(
        self,
        largest_wall: float,
        daily_volume: float,
        is_symmetric: bool
    ) -> SpoofingResult:
        """L094: Detect order book spoofing patterns."""
        wall_to_volume = largest_wall / max(daily_volume, 1)

        if wall_to_volume > self.SPOOFING_THRESHOLDS["wall_to_volume_high"]:
            risk_level = ManipulationRisk.HIGH
            mm_activity = "HIGH"
            entry_note = "Large wall may be spoofing - wait for wall test/removal"
        elif wall_to_volume > self.SPOOFING_THRESHOLDS["wall_to_volume_medium"]:
            risk_level = ManipulationRisk.MEDIUM
            mm_activity = "MEDIUM"
            entry_note = "Significant wall detected - monitor for persistence"
        else:
            risk_level = ManipulationRisk.LOW
            mm_activity = "LOW"
            entry_note = "Normal order book - proceed with standard analysis"

        return SpoofingResult(
            risk_level=risk_level,
            largest_wall_usd=largest_wall,
            wall_to_volume_ratio=wall_to_volume,
            is_symmetric_book=is_symmetric,
            mm_activity_level=mm_activity,
            entry_timing_note=entry_note
        )

    def _get_stop_loss_recommendation(
        self,
        market_cap: float,
        volume_24h: float,
        leverage: float,
        trade_type: str
    ) -> StopLossRecommendation:
        """L092: Determine mental vs hard stop loss."""
        thresholds = self.MENTAL_SL_THRESHOLDS

        use_mental = False
        reasons = []

        if market_cap < thresholds["market_cap_max"]:
            use_mental = True
            reasons.append(f"Low MC (${market_cap/1e6:.1f}M < $50M)")

        if volume_24h < thresholds["volume_24h_max"]:
            use_mental = True
            reasons.append(f"Low volume (${volume_24h/1e3:.0f}K < $500K)")

        if leverage > thresholds["leverage_threshold"]:
            use_mental = True
            reasons.append(f"High leverage ({leverage}x > 10x)")

        if trade_type == "SWING":
            use_mental = True
            reasons.append("Swing trade (>24h timeframe)")

        if use_mental:
            sl_type = StopLossType.MENTAL
            reasoning = "Mental SL recommended: " + ", ".join(reasons)
            execution = "4H_CLOSE" if trade_type == "SWING" else "LIMIT"
        else:
            sl_type = StopLossType.HARD
            reasoning = "Hard SL acceptable - sufficient liquidity"
            execution = "MARKET"

        # Alert levels (relative to SL price)
        alert_levels = {
            "warning": 0.95,    # 95% of SL
            "critical": 0.98   # 98% of SL
        }

        return StopLossRecommendation(
            sl_type=sl_type,
            reasoning=reasoning,
            alert_levels=alert_levels,
            execution_method=execution
        )

    def _aggregate_results(
        self,
        token_symbol: str,
        timestamp: str,
        wash_trading: WashTradingResult,
        liquidation_cascade: LiquidationCascadeResult,
        insider_activity: InsiderActivityResult,
        hype_filter: HypeFilterResult,
        spoofing: SpoofingResult,
        stop_loss: StopLossRecommendation
    ) -> ManipulationAnalysisResult:
        """Aggregate all manipulation check results."""
        skip_reasons = []
        warnings = []
        risk_labels = []

        # Determine if we should skip
        if wash_trading.recommendation == "SKIP":
            skip_reasons.append("High wash trading risk")
            risk_labels.append("[WASH TRADE RISK]")
        elif wash_trading.recommendation == "CAUTION":
            warnings.append("Medium wash trading risk detected")
            risk_labels.append("[WASH TRADE CAUTION]")

        if liquidation_cascade.risk_level in [ManipulationRisk.HIGH, ManipulationRisk.EXTREME]:
            warnings.extend(liquidation_cascade.warnings)
            risk_labels.append("[CASCADE RISK]")
            if liquidation_cascade.is_crowded_trade:
                risk_labels.append("[CROWDED TRADE]")

        if insider_activity.risk_level == ManipulationRisk.HIGH:
            warnings.append(insider_activity.reasoning)
            risk_labels.append("[INSIDER ACTIVITY]")

        if hype_filter.recommendation == "SKIP":
            skip_reasons.append(f"Hype-driven pump detected (wait {hype_filter.wait_period_days} days)")
            if hype_filter.celebrity_mentions:
                risk_labels.append("[CELEBRITY PUMP]")
            elif hype_filter.is_coordinated_pump:
                risk_labels.append("[INFLUENCER HYPE]")
        elif hype_filter.recommendation == "CAUTION":
            warnings.append(f"Elevated hype detected (consider waiting)")
            risk_labels.append("[HYPE CAUTION]")

        if spoofing.risk_level == ManipulationRisk.HIGH:
            warnings.append(spoofing.entry_timing_note)
            risk_labels.append("[MM ACTIVITY]")

        # Calculate aggregated modifiers
        total_conviction_modifier = wash_trading.conviction_modifier

        # Position size multiplier (cumulative from cascade risk)
        position_multiplier = liquidation_cascade.position_modifier

        # Apply hype filter reduction
        if hype_filter.hype_level == ManipulationRisk.HIGH:
            position_multiplier *= 0.5
        elif hype_filter.hype_level == ManipulationRisk.MEDIUM:
            position_multiplier *= 0.75

        # Entry delay (max of all delays)
        entry_delay = max(
            insider_activity.entry_delay_hours,
            hype_filter.wait_period_days * 24
        )

        # Overall risk level
        risk_levels = [
            wash_trading.risk_level,
            liquidation_cascade.risk_level,
            insider_activity.risk_level,
            hype_filter.hype_level,
            spoofing.risk_level
        ]

        if ManipulationRisk.EXTREME in risk_levels:
            overall_risk = ManipulationRisk.EXTREME
        elif risk_levels.count(ManipulationRisk.HIGH) >= 2:
            overall_risk = ManipulationRisk.EXTREME
        elif ManipulationRisk.HIGH in risk_levels:
            overall_risk = ManipulationRisk.HIGH
        elif risk_levels.count(ManipulationRisk.MEDIUM) >= 2:
            overall_risk = ManipulationRisk.HIGH
        elif ManipulationRisk.MEDIUM in risk_levels:
            overall_risk = ManipulationRisk.MEDIUM
        else:
            overall_risk = ManipulationRisk.LOW

        should_skip = len(skip_reasons) > 0 or overall_risk == ManipulationRisk.EXTREME

        return ManipulationAnalysisResult(
            token_symbol=token_symbol,
            timestamp=timestamp,
            wash_trading=wash_trading,
            liquidation_cascade=liquidation_cascade,
            insider_activity=insider_activity,
            hype_filter=hype_filter,
            spoofing=spoofing,
            stop_loss_recommendation=stop_loss,
            overall_manipulation_risk=overall_risk,
            total_conviction_modifier=total_conviction_modifier,
            position_size_multiplier=position_multiplier,
            should_skip=should_skip,
            skip_reasons=skip_reasons,
            warnings=warnings,
            entry_delay_hours=entry_delay,
            risk_labels=risk_labels
        )


# Convenience function for quick analysis
def analyze_manipulation(
    token_symbol: str,
    **kwargs
) -> ManipulationAnalysisResult:
    """
    Quick analysis wrapper for manipulation detection.

    Usage:
        result = analyze_manipulation(
            "MEME",
            volume_24h=5_000_000,
            market_cap=35_000_000,
            twitter_followers=800
        )
    """
    detector = ManipulationDetector()
    return detector.analyze_token(token_symbol, **kwargs)
