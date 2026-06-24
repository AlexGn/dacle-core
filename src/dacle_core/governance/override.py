"""Operator override — HMAC-signed escape hatch.

Allows the human operator to authorize a single rejected intent through
during incident response. Signed with HMAC-SHA256, max 24h TTL, audited.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Optional

from src.governance.contracts import KEY_SOVEREIGN_OVERRIDE

logger = logging.getLogger(__name__)

MAX_TTL_HOURS = 24
HMAC_SECRET_ENV = "SOVEREIGN_OVERRIDE_HMAC_SECRET"


@dataclass(frozen=True)
class OverrideToken:
    actor: str
    reason: str
    pillar: str
    intent_id: str
    issued_ts: float
    expires_ts: float
    signature: str

    def to_payload(self) -> dict:
        return {
            "actor": self.actor,
            "reason": self.reason,
            "pillar": self.pillar,
            "intent_id": self.intent_id,
            "issued_ts": self.issued_ts,
            "expires_ts": self.expires_ts,
            "signature": self.signature,
        }


def _get_secret() -> bytes:
    secret = os.environ.get(HMAC_SECRET_ENV, "")
    if not secret:
        raise OverrideError(f"missing env var {HMAC_SECRET_ENV}")
    return secret.encode("utf-8")


def _canonical_message(actor: str, reason: str, pillar: str, intent_id: str, issued_ts: float, expires_ts: float) -> bytes:
    parts = [actor, reason, pillar, intent_id, f"{issued_ts:.6f}", f"{expires_ts:.6f}"]
    return "|".join(parts).encode("utf-8")


def _sign(actor: str, reason: str, pillar: str, intent_id: str, issued_ts: float, expires_ts: float) -> str:
    msg = _canonical_message(actor, reason, pillar, intent_id, issued_ts, expires_ts)
    return hmac.new(_get_secret(), msg, sha256).hexdigest()


class OverrideError(Exception):
    pass


class OperatorOverride:
    """HMAC-signed single-use operator override.

    Stored in Redis under KEY_SOVEREIGN_OVERRIDE. SovereignWrapper checks for
    a valid override BEFORE rejecting an intent; if present, the override is
    consumed (deleted) and the decision flips to APPROVED with override_applied=True.
    """

    def __init__(self, redis: Optional[Any] = None, audit_trail: Optional[Any] = None):
        self.redis = redis
        self.audit_trail = audit_trail

    def create_override(
        self,
        actor: str,
        reason: str,
        pillar: str,
        intent_id: str,
        ttl_hours: float = 1.0,
    ) -> OverrideToken:
        if not actor or not reason or not pillar or not intent_id:
            raise OverrideError("actor/reason/pillar/intent_id required")
        if ttl_hours <= 0 or ttl_hours > MAX_TTL_HOURS:
            raise OverrideError(f"ttl_hours must be in (0, {MAX_TTL_HOURS}]")
        issued = time.time()
        expires = issued + (ttl_hours * 3600.0)
        sig = _sign(actor, reason, pillar, intent_id, issued, expires)
        token = OverrideToken(
            actor=actor,
            reason=reason,
            pillar=pillar,
            intent_id=intent_id,
            issued_ts=issued,
            expires_ts=expires,
            signature=sig,
        )
        logger.warning(
            "OVERRIDE CREATED actor=%s pillar=%s intent_id=%s ttl_h=%.2f",
            actor, pillar, intent_id, ttl_hours,
        )
        return token

    async def persist(self, token: OverrideToken) -> None:
        if self.redis is None:
            raise OverrideError("redis not configured")
        ttl_sec = max(1, int(token.expires_ts - time.time()))
        try:
            await self.redis.set(
                KEY_SOVEREIGN_OVERRIDE,
                json.dumps(token.to_payload()),
                ex=ttl_sec,
            )
        except Exception as e:
            raise OverrideError(f"redis persist failed: {e}")

    async def fetch_pending(self) -> Optional[OverrideToken]:
        if self.redis is None:
            return None
        try:
            val = await self.redis.get(KEY_SOVEREIGN_OVERRIDE)
            if val is None:
                return None
            if isinstance(val, bytes):
                val = val.decode("utf-8")
            data = json.loads(val)
            return OverrideToken(**data)
        except Exception:
            return None

    async def revoke_override(self, actor: str = "operator") -> None:
        if self.redis is None:
            return
        try:
            await self.redis.delete(KEY_SOVEREIGN_OVERRIDE)
            logger.warning("OVERRIDE REVOKED by %s", actor)
        except Exception as e:
            logger.error("revoke_override redis error: %s", e)

    def validate_override(self, token: OverrideToken, pillar: str, intent_id: str) -> bool:
        """Constant-time validate. Returns True only if signature matches AND not expired AND pillar/intent match."""
        try:
            now = time.time()
            if now > token.expires_ts:
                return False
            if (token.expires_ts - token.issued_ts) > (MAX_TTL_HOURS * 3600.0 + 1.0):
                return False
            if token.pillar != pillar or token.intent_id != intent_id:
                return False
            expected = _sign(
                token.actor, token.reason, token.pillar, token.intent_id,
                token.issued_ts, token.expires_ts,
            )
            return hmac.compare_digest(expected, token.signature)
        except Exception as e:
            logger.error("validate_override error: %s", e)
            return False