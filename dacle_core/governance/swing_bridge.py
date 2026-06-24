"""
Swing Pillar — Sovereign Wrapper bridge.

Wraps the Swing (Blofin perps) order submission path with sovereign
governance. Swing is fully manual — human-initiated via Discord bot
commands flowing through api/routers/execution_v2.py.

This bridge provides governance for human-initiated orders.
Autonomous swing execution is out of scope for this phase.

Usage in execution_v2.py approve_and_execute_v2():
    # Before _submit_bracket_order():
    from dacle_core.governance.swing_bridge import swing_sovereign_check
    sov = await swing_sovereign_check(
        intent_id=scoped_key,
        notional_usd=reval.get("effective_size_usd", 0),
        correlation=0.0,
    )
    if not sov["approved"]:
        return ApproveAndExecuteResponseV2(
            status="VETOED",
            reason=sov.get("reason_code", "SOVEREIGN_REJECT"),
            idempotency_key=scoped_key,
        )
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Module-level bridge — set once at app startup
_bridge: Optional[Any] = None


async def init_swing_sovereign(redis) -> None:
    """Initialize the Swing sovereign bridge. Call once at FastAPI startup."""
    global _bridge
    from dacle_core.governance.sovereign import SovereignWrapper
    from dacle_core.governance.aoat import AOATWriter

    aoat = AOATWriter()
    wrapper = SovereignWrapper(redis=redis, aoat_writer=aoat)
    wrapper.load_limits()

    from dacle_core.governance.kill_switch import set_redis_client
    set_redis_client(redis)

    _bridge = wrapper
    logger.info("Sovereign Wrapper wired for Swing pillar (manual orders)")


async def swing_sovereign_check(
    *,
    intent_id: str = "",
    notional_usd: float = 0.0,
    regime: str = "NEUTRAL",
    correlation: float = 0.0,
    open_positions: int = 0,
    daily_loss_usd: float = 0.0,
) -> Dict[str, Any]:
    """Check a Swing intent through the sovereign wrapper.

    Returns dict with: approved, reason, reason_code, shadow.
    Call before _submit_bracket_order() in execution_v2.py.
    """
    global _bridge
    if _bridge is None:
        # Not initialized — fail-closed
        return {"approved": False, "reason": "Swing sovereign bridge not initialized",
                "reason_code": "SOVEREIGN_NOT_INITIALIZED", "shadow": False}

    try:
        decision = await _bridge.validate_intent(
            pillar="swing",
            intent_id=intent_id,
            notional_usd=notional_usd,
            side="MANUAL",
            strategy_id="swing_manual",
            open_positions=open_positions,
            daily_loss_usd=daily_loss_usd,
            current_correlation=correlation,
            regime=regime,
        )
        return decision.to_dict()
    except Exception as e:
        logger.error(f"Swing sovereign bridge error: {e}")
        return {"approved": False, "reason": str(e), "reason_code": "SOVEREIGN_BRIDGE_ERROR"}
