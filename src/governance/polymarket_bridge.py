"""
Polymarket Pillar — Sovereign Wrapper bridge.

Provides a lightweight adapter that Polymarket's position_manager uses
to add sovereign governance alongside existing admission checks.

The sovereign kill switch is a SUPERSET of the per-scanner kill switches:
if sovereign says kill, all scanners halt regardless of their individual
kill_switch attributes.

Usage in PolymarketPositionManager.try_admit_intent():
    # After existing checks, before Admission logic:
    sovereign_ok = await self._sovereign_bridge.check(
        intent_id=self.intent_id or str(uuid.uuid4()),
        notional_usd=float(arb_intent.get("size_usdc", 0)),
        open_positions=1 if self.state != ArbState.FLAT else 0,
        daily_loss_usd=self._daily_loss or 0.0,
    )
    if not sovereign_ok["approved"]:
        self._last_blocked_reason = sovereign_ok.get("reason_code", "SOVEREIGN_REJECT")
        logger.warning(f"Sovereign REJECTED: {sovereign_ok['reason']}")
        return False
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PolymarketSovereignBridge:
    """Thin bridge between Polymarket daemon and SovereignWrapper."""

    def __init__(self, wrapper=None):
        self._wrapper = wrapper

    async def check(
        self,
        *,
        intent_id: str = "",
        notional_usd: float = 0.0,
        open_positions: int = 0,
        daily_loss_usd: float = 0.0,
        regime: str = "NEUTRAL",
    ) -> Dict[str, Any]:
        """Check an arb intent through the sovereign wrapper.

        Returns dict with keys: approved, reason, reason_code, shadow.
        Polymarket callers check result['approved'] to gate admission.
        """
        if self._wrapper is None:
            return {"approved": True, "reason": "NO_WRAPPER", "shadow": True}

        try:
            decision = await self._wrapper.validate_intent(
                pillar="polymarket",
                intent_id=intent_id,
                notional_usd=notional_usd,
                side="ARB",
                strategy_id="arb",
                open_positions=open_positions,
                daily_loss_usd=daily_loss_usd,
                regime=regime,
            )
            return decision.to_dict()
        except Exception as e:
            logger.error(f"Polymarket sovereign bridge error: {e}")
            return {"approved": False, "reason": str(e), "reason_code": "SOVEREIGN_BRIDGE_ERROR"}


async def wire_polymarket_sovereign(redis, config: Optional[Dict] = None) -> PolymarketSovereignBridge:
    """Create and return a PolymarketSovereignBridge.

    Call once in daemon.start(). Polymarket already creates its own
    redis.asyncio.Redis client from REDIS_URL — pass it here.
    """
    from src.governance.sovereign import SovereignWrapper
    from src.governance.aoat import AOATWriter

    aoat = AOATWriter()
    wrapper = SovereignWrapper(redis=redis, aoat_writer=aoat)
    wrapper.load_limits()

    from src.governance.kill_switch import set_redis_client
    set_redis_client(redis)

    logger.info("Sovereign Wrapper wired for Polymarket pillar")
    return PolymarketSovereignBridge(wrapper)
