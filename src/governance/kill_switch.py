"""Sovereign kill switch — single Redis key + pub/sub broadcast.

Reuses KillSwitch Pydantic model from src/lighter/contracts.py. Pattern follows
src/trading_shared/governor.py GlobalGovernor for sub-1s propagation.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from src.governance.contracts import (
    CHAN_SOVEREIGN_EVENTS,
    KEY_SOVEREIGN_KILL,
)
from src.lighter.contracts import KillSwitch

logger = logging.getLogger(__name__)


class SovereignKillSwitch:
    """Cross-pillar kill switch.

    activate() persists state to Redis and broadcasts on pub/sub for fast
    propagation. Daemons subscribe to CHAN_SOVEREIGN_EVENTS and / or poll
    is_active() in their hot loop. Activation auto-expires after ttl_sec.
    """

    def __init__(self, redis: Any):
        self.redis = redis

    async def activate(self, actor: str, reason: str, ttl_sec: int = 3600) -> KillSwitch:
        ks = KillSwitch(active=True, actor=actor, reason=reason, ttl_sec=ttl_sec)
        payload = {
            "active": True,
            "actor": actor,
            "reason": reason,
            "ttl_sec": ttl_sec,
            "ts": ks.ts.replace(tzinfo=timezone.utc).isoformat() if ks.ts.tzinfo is None else ks.ts.isoformat(),
        }
        try:
            await self.redis.set(KEY_SOVEREIGN_KILL, json.dumps(payload), ex=ttl_sec)
            event = {"event": "SOVEREIGN_KILL_ACTIVATE", **payload}
            await self.redis.publish(CHAN_SOVEREIGN_EVENTS, json.dumps(event))
            logger.critical("SOVEREIGN KILL SWITCH ACTIVATED by %s: %s (ttl=%ds)", actor, reason, ttl_sec)
        except Exception as e:
            logger.exception("Sovereign kill activate failed: %s", e)
            raise
        return ks

    async def deactivate(self, actor: str = "operator") -> None:
        try:
            await self.redis.delete(KEY_SOVEREIGN_KILL)
            event = {
                "event": "SOVEREIGN_KILL_CLEAR",
                "actor": actor,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            await self.redis.publish(CHAN_SOVEREIGN_EVENTS, json.dumps(event))
            logger.warning("Sovereign kill cleared by %s", actor)
        except Exception as e:
            logger.exception("Sovereign kill deactivate failed: %s", e)
            raise

    async def is_active(self) -> bool:
        """True if kill is set in Redis. Fail-closed: any Redis error returns True."""
        try:
            val = await self.redis.get(KEY_SOVEREIGN_KILL)
            return val is not None
        except Exception as e:
            logger.error("Sovereign kill is_active() Redis error — failing CLOSED: %s", e)
            return True

    async def get_state(self) -> Optional[dict]:
        try:
            val = await self.redis.get(KEY_SOVEREIGN_KILL)
            if val is None:
                return None
            if isinstance(val, bytes):
                val = val.decode("utf-8")
            return json.loads(val)
        except Exception:
            return None