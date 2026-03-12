"""
DACLE Scalper Global Risk Ledger — Session 451 (Phase 3)
Redis-backed atomic exposure ledger using integer cents.
"""

import logging
from typing import Optional
import redis.asyncio as aioredis
from src.lighter.contracts import KEY_GLOBAL_EXPOSURE_CENTS_V1, GateRejectCode
from src.utils.logger import get_logger

_LEASE_TTL_SEC = 900  # 15 minutes

_CHECKOUT_SCRIPT = """
local current     = math.max(0, tonumber(redis.call('GET', KEYS[1])) or 0)
local delta_cents = tonumber(ARGV[1])
local cap_cents   = tonumber(ARGV[2])
local ttl_sec     = tonumber(ARGV[3])
local new_total   = current + delta_cents
if new_total > cap_cents then
    return -1
end
redis.call('SET', KEYS[1], tostring(new_total), 'EX', tostring(ttl_sec))
return new_total
"""


class GlobalRiskLedger:
    """
    Redis-backed atomic exposure ledger.
    Stores exposure in INTEGER CENTS to avoid float precision drift.
    Thread-safe via Redis Lua atomicity.
    """

    def __init__(self, redis: aioredis.Redis, cap_usd: float, enabled: bool = True):
        self._redis = redis
        self._cap_cents = int(round(cap_usd * 100))
        self._enabled = enabled
        self._log = get_logger("risk_ledger")

    async def checkout_exposure(
        self,
        notional_usd: float,
        is_reduce_only: bool = False,
    ) -> tuple[bool, str]:
        """
        Atomically reserve notional_usd in the ledger.
        Returns (True, CLEAR) if within cap, (False, GLOBAL_EXPOSURE_CAP_EXCEEDED) otherwise.
        On Redis failure, fails CLOSED.
        """
        if is_reduce_only:
            # Reduce-only exits must not consume or block against global exposure.
            return True, GateRejectCode.CLEAR.value

        if not self._enabled:
            return True, GateRejectCode.CLEAR.value

        delta_cents = int(round(notional_usd * 100))
        try:
            result = await self._redis.eval(
                _CHECKOUT_SCRIPT,
                1,
                KEY_GLOBAL_EXPOSURE_CENTS_V1,
                str(delta_cents),
                str(self._cap_cents),
                str(_LEASE_TTL_SEC),
            )
            if result == -1:
                self._log.warning(
                    "global_risk_veto notional_usd=%.2f cap_usd=%.2f",
                    notional_usd,
                    self._cap_cents / 100,
                )
                return False, GateRejectCode.GLOBAL_EXPOSURE_CAP_EXCEEDED.value
            return True, GateRejectCode.CLEAR.value
        except Exception as exc:
            self._log.error("global_ledger_checkout_error err=%r — failing closed", exc)
            return False, GateRejectCode.GLOBAL_EXPOSURE_CAP_EXCEEDED.value

    async def correct_checkout(self, requested_usd: float, actual_usd: float) -> None:
        """
        After fill confirmation, release the over-reserved portion (requested - actual).
        Called when actual fill < requested (slippage / partial fill).
        """
        if not self._enabled:
            return
        over_reserved_cents = int(round((requested_usd - actual_usd) * 100))
        if over_reserved_cents <= 0:
            return
        try:
            # Atomic decrement but clamp at 0, refresh TTL
            await self._redis.eval(
                """
                local current = math.max(0, tonumber(redis.call('GET', KEYS[1])) or 0)
                local delta = tonumber(ARGV[1])
                local ttl_sec = tonumber(ARGV[2])
                local new_val = math.max(0, current - delta)
                redis.call('SET', KEYS[1], tostring(new_val), 'EX', tostring(ttl_sec))
                return new_val
                """,
                1,
                KEY_GLOBAL_EXPOSURE_CENTS_V1,
                str(over_reserved_cents),
                str(_LEASE_TTL_SEC),
            )
        except Exception as exc:
            self._log.error("global_ledger_correct_error err=%r", exc)

    async def check_in_exposure(self, notional_usd: float) -> None:
        """
        Release notional_usd from the ledger when a position closes.
        Called from _on_flat_transition(). Clamps to zero to prevent negative ledger.
        """
        if not self._enabled or notional_usd <= 0:
            return
        release_cents = int(round(notional_usd * 100))
        try:
            # Decrement but clamp at 0 (Lua for atomicity), refresh TTL
            await self._redis.eval(
                """
                local current = math.max(0, tonumber(redis.call('GET', KEYS[1])) or 0)
                local delta = tonumber(ARGV[1])
                local ttl_sec = tonumber(ARGV[2])
                local new_val = math.max(0, current - delta)
                redis.call('SET', KEYS[1], tostring(new_val), 'EX', tostring(ttl_sec))
                return new_val
                """,
                1,
                KEY_GLOBAL_EXPOSURE_CENTS_V1,
                str(release_cents),
                str(_LEASE_TTL_SEC),
            )
        except Exception as exc:
            self._log.error("global_ledger_checkin_error err=%r", exc)

    async def re_assert_exposure(self, notional_usd: float) -> None:
        """
        Called on daemon startup after checkpoint recovery.
        If checkpoint shows active position, re-asserts exposure into ledger.
        Uses SET (not INCRBY) because this IS the authoritative value after a restart.
        """
        if not self._enabled:
            return
        cents = int(round(notional_usd * 100))
        try:
            if cents > 0:
                await self._redis.set(
                    KEY_GLOBAL_EXPOSURE_CENTS_V1, str(cents), ex=_LEASE_TTL_SEC
                )
                self._log.info("global_ledger_reassert notional_usd=%.2f", notional_usd)
            # If notional == 0 (FLAT checkpoint), do not zero the key —
            # another daemon instance may have exposure registered.
        except Exception as exc:
            self._log.error("global_ledger_reassert_error err=%r", exc)

    async def get_current_exposure_usd(self) -> Optional[float]:
        """Observability: read current ledger value for status endpoint."""
        if not self._enabled:
            return None
        try:
            raw = await self._redis.get(KEY_GLOBAL_EXPOSURE_CENTS_V1)
            if raw is None:
                return 0.0
            return max(0.0, int(raw) / 100.0)
        except Exception:
            return None
