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
    Implements fail-closed behavior: in-memory fallback ensures lockdown
    state is never lost even when Redis is unavailable.
    """

    def __init__(self, redis: Redis):
        self.redis = redis
        self._fallback_locked: bool = False

    def is_fallback_locked(self) -> bool:
        """Returns True if the in-memory fallback lockdown flag is set."""
        return self._fallback_locked

    async def _publish_lockdown_event(self, channel: str, payload: Dict[str, Any]) -> None:
        """Publish to pubsub channel, gracefully handling Redis failures."""
        try:
            await self.redis.publish(channel, json.dumps(payload))
        except Exception as e:
            logger.warning("Failed to publish lockdown event to %s: %s", channel, e)

    async def initiate_lockdown(self, strategy_id: str, reason: str, scope: str = "GLOBAL"):
        """
        Triggers a system-wide or strategy-specific lockdown.
        1. Attempts to update persistent Redis state.
        2. Broadcasts interrupt event to all active daemons.
        3. Always sets in-memory fallback flag (fail-closed on Redis failure).
        """
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "event": "LOCKDOWN",
            "strategy_id": strategy_id,
            "reason": reason,
            "scope": scope,
            "initiated_at": now,
        }

        try:
            # 1. Persist state
            await self.redis.set(KEY_GLOBAL_LOCKDOWN, json.dumps(payload))
        except Exception as e:
            logger.error("Failed to persist lockdown state to Redis: %s", e)

        # Always set in-memory flag — fail closed
        self._fallback_locked = True

        # 2. Broadcast interrupt
        await self._publish_lockdown_event(CHAN_CONTROL_EVENTS, payload)

        logger.critical("GLOBAL LOCKDOWN INITIATED by %s: %s", strategy_id, reason)

    async def clear_lockdown(self):
        """Removes the lockdown state."""
        try:
            await self.redis.delete(KEY_GLOBAL_LOCKDOWN)
        except Exception as e:
            logger.error("Failed to clear lockdown state from Redis: %s", e)

        # Always clear in-memory flag
        self._fallback_locked = False

        payload = {"event": "CLEAR_LOCKDOWN", "ts": datetime.now(timezone.utc).isoformat()}
        await self._publish_lockdown_event(CHAN_CONTROL_EVENTS, payload)
        logger.info("Global lockdown cleared.")

    async def is_locked_down(self) -> bool:
        """
        Checks if a global lockdown is currently active.
        Returns True if either the in-memory fallback flag is set,
        or if Redis indicates an active lockdown.
        """
        # 1. In-memory fallback check (always succeeds)
        if self._fallback_locked:
            return True

        # 2. Redis check
        try:
            return await self.redis.exists(KEY_GLOBAL_LOCKDOWN) > 0
        except Exception as e:
            logger.warning("Failed to check Redis lockdown state: %s. Assuming lockdown (fail-closed).", e)
            # When Redis is down, set fallback flag to ensure future checks also return True
            self._fallback_locked = True
            return True
