"""
Capital Allocator — pillar allocation engine.

Recommends allocations across the three pillars based on:
    - Pillar tier (higher tier → more capital)
    - Recent Sharpe (30d, higher → more capital)
    - Regime alignment (regime-conditional weighting)
    - Cross-pillar correlation (high corr → reduce concentration)

Shadow-only initially. Allocations are recommendations, not enforced
position limits. The SovereignWrapper's position_limits still apply.

Redis: dacle:governance:allocation (JSON, refreshed nightly)
"""

import json
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


KEY_ALLOCATION = "dacle:governance:allocation"


@dataclass
class Allocation:
    """Recommended allocation for a single pillar."""
    pillar: str
    weight: float       # 0.0 – 1.0 fraction of total capital
    notional_usd: float  # absolute USD allocation
    reason: str          # human-readable justification


@dataclass
class AllocationPlan:
    """Complete allocation recommendation for all pillars."""
    total_capital_usd: float
    pillars: Dict[str, Allocation] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    regime: str = "NEUTRAL"

    def to_dict(self) -> dict:
        return {
            "total_capital_usd": self.total_capital_usd,
            "ts": self.ts,
            "regime": self.regime,
            "pillars": {
                k: {
                    "weight": v.weight,
                    "notional_usd": v.notional_usd,
                    "reason": v.reason,
                } for k, v in self.pillars.items()
            },
        }


class CapitalAllocator:
    """Recommends cross-pillar capital allocations.

    Usage (nightly cron):
        allocator = CapitalAllocator(pnl=pnl_agg, sharpe=sharpe_svc, corr=corr_tracker)
        plan = await allocator.recommend(total_capital_usd=5000, regime="BULL")
        await allocator.publish(plan)
    """

    # Default weights per pillar tier
    TIER_WEIGHTS = {
        "R0": 0.0, "R1": 0.0, "R2": 0.05, "R3": 0.15,
        "R4": 0.25, "R5": 0.35, "R6": 0.40,
    }

    # Regime modifiers: multiply pillar weight by regime alignment
    REGIME_MODIFIERS = {
        # Lighter scalper: neutral in all regimes, suppressed in CRASH
        "lighter": {"BULL": 1.0, "BEAR": 1.0, "SIDEWAYS": 0.8, "RISK_OFF": 0.3, "RISK_ON": 1.2, "CRASH": 0.0},
        # Polymarket arb: benefits from volatility
        "polymarket": {"BULL": 0.8, "BEAR": 1.0, "SIDEWAYS": 1.2, "RISK_OFF": 0.6, "RISK_ON": 1.0, "CRASH": 0.5},
        # Swing strategic: regime-directional
        "swing": {"BULL": 1.2, "BEAR": 0.8, "SIDEWAYS": 0.5, "RISK_OFF": 0.2, "RISK_ON": 1.3, "CRASH": 0.0},
    }

    def __init__(
        self,
        pnl_aggregator=None,
        sharpe_service=None,
        correlation_tracker=None,
        redis=None,
    ):
        self._pnl = pnl_aggregator
        self._sharpe = sharpe_service
        self._corr = correlation_tracker
        self._redis = redis

    async def recommend(
        self,
        total_capital_usd: float = 5000.0,
        regime: str = "NEUTRAL",
        pillar_tiers: Optional[Dict[str, str]] = None,
    ) -> AllocationPlan:
        """Compute recommended pillar allocations.

        Args:
            total_capital_usd: Total deployable capital in USD.
            regime: Current market regime (BULL, BEAR, SIDEWAYS, etc.).
            pillar_tiers: Dict of pillar→tier e.g. {"lighter": "R4", "polymarket": "R3", "swing": "R0"}

        Returns:
            AllocationPlan with weights and notional allocations.
        """
        tiers = pillar_tiers or {"lighter": "R3", "polymarket": "R3", "swing": "R1"}
        sharpes = {}
        if self._sharpe:
            try:
                sharpes = await self._sharpe.get_all_sharpes(30)
            except Exception:
                pass

        max_corr = 0.0
        if self._corr:
            try:
                max_corr = await self._corr.get_max_correlation()
            except Exception:
                pass

        # Compute raw weights: tier_weight * sharpe_modifier * regime_modifier * correlation_penalty
        raw_weights: Dict[str, float] = {}
        for pillar in ("lighter", "polymarket", "swing"):
            tier = tiers.get(pillar, "R0")
            tier_w = self.TIER_WEIGHTS.get(tier, 0.0)

            # Sharpe modifier: positive Sharpe → boost, negative → penalty
            sharpe = sharpes.get(pillar)
            sharpe_mod = 1.0
            if sharpe is not None and isinstance(sharpe, (int, float)):
                if sharpe > 1.0:
                    sharpe_mod = 1.3
                elif sharpe > 0.5:
                    sharpe_mod = 1.1
                elif sharpe < -1.0:
                    sharpe_mod = 0.3
                elif sharpe < 0.0:
                    sharpe_mod = 0.7

            # Regime modifier
            regime_mod = self.REGIME_MODIFIERS.get(pillar, {}).get(regime, 1.0)

            # Correlation penalty: high cross-correlation → reduce all weights equally
            corr_penalty = 1.0
            if max_corr > 0.7:
                corr_penalty = 1.0 - (max_corr - 0.7)

            raw = tier_w * sharpe_mod * regime_mod * corr_penalty
            raw_weights[pillar] = raw

        # Normalize to sum to 1.0
        total_raw = sum(raw_weights.values())
        plan = AllocationPlan(total_capital_usd=total_capital_usd, regime=regime)

        if total_raw <= 0:
            # All pillars at zero — no allocation
            for p in ("lighter", "polymarket", "swing"):
                plan.pillars[p] = Allocation(pillar=p, weight=0.0, notional_usd=0.0,
                    reason="No eligible pillars")
            return plan

        for pillar in ("lighter", "polymarket", "swing"):
            weight = raw_weights[pillar] / total_raw
            notional = round(total_capital_usd * weight, 2)
            reasons = []
            if sharpes.get(pillar) is not None:
                reasons.append(f"Sharpe={sharpes[pillar]:.2f}")
            if max_corr > 0.7:
                reasons.append(f"corr_penalty={max_corr:.2f}")
            plan.pillars[pillar] = Allocation(
                pillar=pillar,
                weight=round(weight, 4),
                notional_usd=notional,
                reason=", ".join(reasons) or f"tier={tiers.get(pillar, 'R0')} regime={regime}"
            )

        return plan

    async def publish(self, plan: AllocationPlan) -> bool:
        """Publish allocation plan to Redis for dashboard/wrapper consumption."""
        if self._redis is None:
            return False
        try:
            await self._redis.set(KEY_ALLOCATION, json.dumps(plan.to_dict()), ex=86400)
            return True
        except Exception:
            return False

    async def get_current_plan(self) -> Optional[dict]:
        """Read the most recent allocation plan from Redis."""
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(KEY_ALLOCATION)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None
