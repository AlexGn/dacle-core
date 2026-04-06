import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

try:
    from redis.asyncio import Redis
except ModuleNotFoundError:  # pragma: no cover - allows import in test envs without redis installed
    Redis = Any

logger = logging.getLogger(__name__)

# Canonical Redis Key for Lockdown Status
KEY_GLOBAL_LOCKDOWN = "dacle:control:lockdown"
# Pub/Sub Channel for Real-Time Interrupts
CHAN_CONTROL_EVENTS = "dacle:control:events"

class GlobalGovernor:
    """
    The 'Red Button' controller for the entire DACLE ecosystem.
    Broadcasts LOCKDOWN events via Pub/Sub for sub-50ms propagation.
    """
    def __init__(self, redis: Redis):
        self.redis = redis

    async def initiate_lockdown(self, strategy_id: str, reason: str, scope: str = "GLOBAL"):
        """
        Triggers a system-wide or strategy-specific lockdown.
        1. Updates persistent Redis state.
        2. Broadcasts interrupt event to all active daemons.
        """
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "event": "LOCKDOWN",
            "strategy_id": strategy_id,
            "reason": reason,
            "scope": scope,
            "initiated_at": now,
        }

        # 1. Persist state
        await self.redis.set(KEY_GLOBAL_LOCKDOWN, json.dumps(payload))

        # 2. Broadcast interrupt
        await self.redis.publish(CHAN_CONTROL_EVENTS, json.dumps(payload))

        logger.critical("GLOBAL LOCKDOWN INITIATED by %s: %s", strategy_id, reason)

    async def clear_lockdown(self):
        """Removes the lockdown state."""
        await self.redis.delete(KEY_GLOBAL_LOCKDOWN)
        payload = {"event": "CLEAR_LOCKDOWN", "ts": datetime.now(timezone.utc).isoformat()}
        await self.redis.publish(CHAN_CONTROL_EVENTS, json.dumps(payload))
        logger.info("Global lockdown cleared.")

    async def is_locked_down(self) -> bool:
        """Checks if a global lockdown is currently active."""
        return await self.redis.exists(KEY_GLOBAL_LOCKDOWN) > 0
