"""
Lighter Pillar — Sovereign Wrapper bridge.

Provides a lightweight adapter that the Lighter daemon imports to add
sovereign governance without restructuring the existing hot loop.

Usage in daemon start():
    from src.governance.lighter_bridge import wire_sovereign
    self._sovereign = await wire_sovereign(self.redis)

Usage in hot loop (before order submission):
    decision = await self._sovereign.check(
        pillar="lighter",
        intent_id=intent.intent_id,
        notional_usd=intent.size_usd,
        side=intent.side,
        strategy_id="scalper",
    )
    if not decision.approved:
        self._last_reject = decision.reason_code
        return  # skip this intent
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class LighterSovereignBridge:
    """Thin bridge between Lighter daemon and SovereignWrapper."""

    def __init__(self, wrapper=None):
        self._wrapper = wrapper

    async def check(
        self,
        *,
        intent_id: str = "",
        notional_usd: float = 0.0,
        side: str = "LONG",
        strategy_id: str = "scalper",
        open_positions: int = 0,
        daily_loss_usd: float = 0.0,
        regime: str = "NEUTRAL",
    ):
        """Check an intent through the sovereign wrapper.

        Returns a lightweight result dict so the daemon doesn't need
        to import sovereign types directly.
        """
        if self._wrapper is None:
            return {"approved": True, "reason": "NO_WRAPPER", "shadow": True}

        try:
            decision = await self._wrapper.validate_intent(
                pillar="lighter",
                intent_id=intent_id,
                notional_usd=notional_usd,
                side=side,
                strategy_id=strategy_id,
                open_positions=open_positions,
                daily_loss_usd=daily_loss_usd,
                regime=regime,
            )
            return decision.to_dict()
        except Exception as e:
            logger.error(f"Sovereign bridge error: {e}")
            # Fail-closed: if the wrapper crashes, reject
            return {"approved": False, "reason": str(e), "reason_code": "SOVEREIGN_BRIDGE_ERROR"}


async def wire_sovereign(redis, config: Optional[Dict] = None):
    """Create and return a LighterSovereignBridge.

    Call this once in daemon.start().

    Args:
        redis: async redis.asyncio.Redis client
        config: Optional config dict (reads from lighter.yaml if None)

    Returns:
        LighterSovereignBridge ready for hot-path checks
    """
    from src.governance.sovereign import SovereignWrapper
    from src.governance.aoat import AOATWriter

    aoat = AOATWriter()
    wrapper = SovereignWrapper(redis=redis, aoat_writer=aoat)
    wrapper.load_limits()

    # Wire kill switch Redis client
    from src.governance.kill_switch import set_redis_client
    set_redis_client(redis)

    logger.info("Sovereign Wrapper wired for Lighter pillar")
    return LighterSovereignBridge(wrapper)
