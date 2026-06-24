"""
DACLE Scalper Real-Time Risk Ledger — Hard Safety v2
Redis-backed atomic risk ledger that maintains the true state of risk, inventory, and PnL.
"""

import logging
import time
from typing import Optional, Dict, Any, Tuple
from enum import Enum

try:
    import redis.asyncio as aioredis
except ModuleNotFoundError:  # pragma: no cover - allows import in test envs without redis installed
    aioredis = None
from src.utils.logger import get_logger

class RiskState(Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    UNWIND = "UNWIND"
    HARD_STOP = "HARD_STOP"

class RealTimeRiskLedger:
    def __init__(self, redis: Any, venue: str, symbol: str,
                 max_open_notional_usd: float = 25.0,
                 daily_loss_limit_usd: float = 10.0,
                 catastrophic_loss_limit_usd: float = 20.0,
                 heartbeat_ttl_sec: int = 10):
        self._redis = redis
        self.venue = venue
        self.symbol = symbol
        self.prefix = f"risk:{venue}:{symbol}"
        self.max_open_notional_usd = max_open_notional_usd
        self.daily_loss_limit_usd = daily_loss_limit_usd
        self.catastrophic_loss_limit_usd = catastrophic_loss_limit_usd
        self.heartbeat_ttl_sec = heartbeat_ttl_sec
        self._log = get_logger("realtime_risk_ledger")
        self._last_limit_breach_signature: Optional[tuple[str, float]] = None
        
        self._rebind_keys()

        # Lua script for atomic pre-send check
        # Returns 1 if ALLOWED, 0 if BLOCKED or STALE or HARD_STOP
        self._check_script = """
        local state = redis.call('GET', KEYS[1])
        if state ~= 'ALLOW' then
            return {0, 'STATE_NOT_ALLOW'}
        end
        local hb = tonumber(redis.call('GET', KEYS[2])) or 0
        local now = tonumber(ARGV[1])
        local ttl = tonumber(ARGV[2])
        if (now - hb) > ttl then
            return {0, 'HEARTBEAT_STALE'}
        end
        local notional = tonumber(redis.call('GET', KEYS[3])) or 0
        local max_notional = tonumber(ARGV[3])
        local intent = tonumber(ARGV[4])
        if (notional + intent) > max_notional then
            return {0, 'MAX_NOTIONAL_BREACH'}
        end
        return {1, 'CLEAR'}
        """

    def _rebind_keys(self):
        self.prefix = f"risk:{self.venue}:{self.symbol}"
        self.k_realized = f"{self.prefix}:realized_pnl_usd"
        self.k_unrealized = f"{self.prefix}:unrealized_pnl_usd"
        self.k_notional = f"{self.prefix}:open_notional_usd"
        self.k_inventory = f"{self.prefix}:net_inventory_qty"
        self.k_fees = f"{self.prefix}:fees_paid_usd"
        self.k_slippage = f"{self.prefix}:slippage_paid_usd"
        self.k_last_fill = f"{self.prefix}:last_fill_ts"
        self.k_risk_state = f"{self.prefix}:risk_state"
        self.k_heartbeat = f"{self.prefix}:permission_heartbeat_ts"
        self.k_synth_stop_state = f"{self.prefix}:synthetic_stop_state"
        self.k_synth_stop_price = f"{self.prefix}:synthetic_stop_price"
        self.k_synth_stop_reason = f"{self.prefix}:synthetic_stop_reason"
        self.k_synth_stop_ts = f"{self.prefix}:synthetic_stop_ts"
        # Rolling 24h PnL tracking (Gap: Calendar vs Rolling)
        self.k_pnl_history = f"{self.prefix}:pnl_history_zset"

    async def rebind_symbol(self, new_symbol: str):
        self.symbol = new_symbol
        self._rebind_keys()
        self._log.info(f"Rebound RealTimeRiskLedger keys to {new_symbol}")

    async def initialize(self):
        """Initialize default states if not present."""
        if not await self._redis.exists(self.k_risk_state):
            await self._redis.set(self.k_risk_state, RiskState.ALLOW.value)
        if not await self._redis.exists(self.k_synth_stop_state):
            await self._redis.set(self.k_synth_stop_state, "INACTIVE")
        if not await self._redis.exists(self.k_synth_stop_price):
            await self._redis.set(self.k_synth_stop_price, "0.0")
        if not await self._redis.exists(self.k_synth_stop_reason):
            await self._redis.set(self.k_synth_stop_reason, "")
        if not await self._redis.exists(self.k_synth_stop_ts):
            await self._redis.set(self.k_synth_stop_ts, "0")

    async def emit_heartbeat(self):
        """Permission writer emits heartbeat."""
        now = int(time.time())
        await self._redis.set(self.k_heartbeat, str(now))

    async def set_risk_state(self, state: RiskState):
        await self._redis.set(self.k_risk_state, state.value)
        self._log.warning(f"[{self.prefix}] Risk state changed to {state.value}")

    async def set_synthetic_stop_state(
        self,
        state: str,
        *,
        stop_price: float = 0.0,
        reason: str = "",
        ts: Optional[int] = None,
    ):
        """Persist synthetic-stop state for cross-process visibility."""
        state_norm = str(state or "INACTIVE").strip().upper()
        now = int(ts if ts is not None else time.time())
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(self.k_synth_stop_state, state_norm)
            pipe.set(self.k_synth_stop_price, str(float(stop_price or 0.0)))
            pipe.set(self.k_synth_stop_reason, str(reason or ""))
            pipe.set(self.k_synth_stop_ts, str(now))
            await pipe.execute()

    async def check_order_allowed(
        self,
        intent_notional_usd: float,
        is_reduce_only: bool = False,
    ) -> Tuple[bool, str]:
        """
        Atomic pre-send check to ensure we can place an order.
        """
        if is_reduce_only:
            # Emergency/reduce-only exits must never be blocked by notional caps.
            return True, "REDUCE_ONLY_BYPASS"

        now = int(time.time())
        try:
            res = await self._redis.eval(
                self._check_script,
                3,
                self.k_risk_state,
                self.k_heartbeat,
                self.k_notional,
                str(now),
                str(self.heartbeat_ttl_sec),
                str(self.max_open_notional_usd),
                str(intent_notional_usd)
            )
            is_allowed = bool(res[0])
            reason = str(res[1])
            return is_allowed, reason
        except Exception as e:
            self._log.error(f"Failed to check order allowed: {e}")
            return False, "REDIS_ERROR_FAIL_CLOSED"

    async def record_fill(self, 
                          qty: float, 
                          price: float, 
                          side: str, 
                          fee_usd: float, 
                          slippage_usd: float = 0.0,
                          realized_pnl_delta: float = 0.0):
        """
        Update ledger atomically on fill.
        """
        now = int(time.time())
        notional = qty * price
        
        # Calculate new inventory and notional logic depends on side and current inventory.
        # This is simplified. The Deterministic PnL pipeline should compute realized_pnl_delta.
        
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incrbyfloat(self.k_fees, fee_usd)
            pipe.incrbyfloat(self.k_slippage, slippage_usd)
            if realized_pnl_delta != 0:
                pipe.incrbyfloat(self.k_realized, realized_pnl_delta)
            
            # Inventory adjustment
            inv_delta = qty if side.upper() == 'BUY' else -qty
            pipe.incrbyfloat(self.k_inventory, inv_delta)
            
            # Simple open notional adjust (should be tracked properly by true inventory value)
            # In a real pipeline, open notional = abs(inventory * current_price)
            # We will just rely on the caller to update notional or we update it.
            
            pipe.set(self.k_last_fill, str(now))
            
            # Log for rolling 24h PnL window
            net_delta = realized_pnl_delta - fee_usd - slippage_usd
            if net_delta != 0:
                # Use a unique member (timestamp:rand) to handle multiple fills in same second
                import random
                member = f"{now}:{random.randint(0, 9999)}"
                pipe.zadd(self.k_pnl_history, {member: now})
                # We store the delta in a separate key mapping member -> delta
                # Actually, simpler to just store delta in a separate key and use zremrangebyscore
                # Wait, Redis doesn't have zrange with values.
                # Let's use a simple HSET for (member -> delta) and ZSET for (member -> ts).
                pipe.hset(f"{self.k_pnl_history}:data", member, str(net_delta))
            
            await pipe.execute()

        # After recording, check limits
        await self._check_limits()

    async def update_unrealized_and_notional(self, unrealized_pnl: float, open_notional: float):
        """Update mark-to-market values."""
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(self.k_unrealized, str(unrealized_pnl))
            pipe.set(self.k_notional, str(open_notional))
            await pipe.execute()
        await self._check_limits()

    async def clear_position_state(self):
        """
        Clear only position-derived state on a confirmed flat transition.

        This preserves realized/session fee history and the current risk state
        while ensuring stale inventory cannot recreate synthetic open notional
        on the next periodic mark-to-market tick.
        """
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(self.k_unrealized, "0.0")
            pipe.set(self.k_notional, "0.0")
            pipe.set(self.k_inventory, "0.0")
            await pipe.execute()
        await self._check_limits()

    async def _check_limits(self):
        """Check if limits breached and update risk state if necessary."""
        now = int(time.time())
        cutoff = now - 86400

        # 1. Cleanup and sum rolling realized PnL from zset
        # We need to get all members within [cutoff, +inf]
        members = await self._redis.zrangebyscore(self.k_pnl_history, cutoff, "+inf")
        
        rolling_realized = 0.0
        if members:
            # Fetch all deltas for these members
            deltas = await self._redis.hmget(f"{self.k_pnl_history}:data", members)
            rolling_realized = sum(float(d or 0.0) for d in deltas)

        # 2. Cleanup old entries to prevent zset bloat
        # Remove members older than 24h
        old_members = await self._redis.zrangebyscore(self.k_pnl_history, "-inf", cutoff - 1)
        if old_members:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(self.k_pnl_history, "-inf", cutoff - 1)
                pipe.hdel(f"{self.k_pnl_history}:data", *old_members)
                await pipe.execute()

        # 3. Current Floating PnL
        unrealized = float(await self._redis.get(self.k_unrealized) or 0.0)
        
        # total_rolling_pnl = rolling_realized_net + current_unrealized
        total_rolling_pnl = rolling_realized + unrealized

        # Session-level PnL (for catastrophic stop)
        realized_session = float(await self._redis.get(self.k_realized) or 0.0)
        fees_session = float(await self._redis.get(self.k_fees) or 0.0)
        total_session_pnl = realized_session + unrealized - fees_session

        current_state_raw = await self._redis.get(self.k_risk_state)
        current_state = (
            current_state_raw.decode('utf-8')
            if isinstance(current_state_raw, bytes)
            else str(current_state_raw or "")
        )
        
        # Priority 1: Catastrophic Stop (Session Level)
        if total_session_pnl <= -self.catastrophic_loss_limit_usd:
            breach_signature = (RiskState.HARD_STOP.value, round(total_session_pnl, 8))
            if current_state != RiskState.HARD_STOP.value:
                await self.set_risk_state(RiskState.HARD_STOP)
            if self._last_limit_breach_signature != breach_signature:
                self._log.critical(f"CATASTROPHIC LOSS LIMIT REACHED: {total_session_pnl}")
                self._last_limit_breach_signature = breach_signature
        # Priority 2: Daily Loss Limit (Rolling 24h Level)
        elif total_rolling_pnl <= -self.daily_loss_limit_usd:
            breach_signature = (RiskState.BLOCK.value, round(total_rolling_pnl, 8))
            if current_state != RiskState.HARD_STOP.value:
                if current_state != RiskState.BLOCK.value:
                    await self.set_risk_state(RiskState.BLOCK)
                if self._last_limit_breach_signature != breach_signature:
                    self._log.error(f"DAILY ROLLING LOSS LIMIT REACHED: {total_rolling_pnl}")
                    self._last_limit_breach_signature = breach_signature
        else:
            self._last_limit_breach_signature = None

    @staticmethod
    def _is_flat_startup_stale_diag(diag: Dict[str, Any]) -> bool:
        realized = float(diag.get("realized_pnl_usd") or 0.0)
        unrealized = float(diag.get("unrealized_pnl_usd") or 0.0)
        notional = float(diag.get("open_notional_usd") or 0.0)
        inventory = float(diag.get("net_inventory_qty") or 0.0)
        fees = float(diag.get("fees_paid_usd") or 0.0)
        slippage = float(diag.get("slippage_paid_usd") or 0.0)
        risk_state = str(diag.get("risk_state") or "")

        # On flat startup, any residual open exposure state is stale regardless
        # of realized session PnL. Exchange truth already says the account is flat.
        if abs(inventory) > 1e-8 or abs(notional) > 1e-6 or abs(unrealized) > 1e-6:
            return True

        return (
            abs(realized) < 1e-9
            and (
                risk_state in {RiskState.BLOCK.value, RiskState.HARD_STOP.value}
                and (fees > 0.0 or slippage > 0.0)
            )
        )

    async def reset_if_flat_startup_stale(self) -> Optional[Dict[str, Any]]:
        diag = await self.get_diagnostics()
        if not self._is_flat_startup_stale_diag(diag):
            return None

        self._log.warning(
            "[%s] Resetting stale flat-startup ledger: risk_state=%s fees=%.4f slippage=%.4f inventory=%.8f",
            self.prefix,
            diag.get("risk_state"),
            float(diag.get("fees_paid_usd") or 0.0),
            float(diag.get("slippage_paid_usd") or 0.0),
            float(diag.get("net_inventory_qty") or 0.0),
        )
        await self.reset_ledger()
        return diag

    async def reset_ledger(self):
        """Reset ledger for a new session. USE WITH CAUTION."""
        keys = [
            self.k_realized, self.k_unrealized, self.k_notional,
            self.k_inventory, self.k_fees, self.k_slippage
        ]
        async with self._redis.pipeline(transaction=True) as pipe:
            for k in keys:
                pipe.set(k, "0.0")
            pipe.set(self.k_risk_state, RiskState.ALLOW.value)
            pipe.set(self.k_synth_stop_state, "INACTIVE")
            pipe.set(self.k_synth_stop_price, "0.0")
            pipe.set(self.k_synth_stop_reason, "")
            pipe.set(self.k_synth_stop_ts, "0")
            # Clear rolling history
            pipe.delete(self.k_pnl_history)
            pipe.delete(f"{self.k_pnl_history}:data")
            await pipe.execute()
        self._last_limit_breach_signature = None
        self._log.info(f"[{self.prefix}] Risk Ledger RESET.")

    async def get_diagnostics(self) -> Dict[str, Any]:
        """Get current state for API."""
        keys = [
            self.k_realized, self.k_unrealized, self.k_notional,
            self.k_inventory, self.k_fees, self.k_slippage,
            self.k_last_fill, self.k_risk_state, self.k_heartbeat,
            self.k_synth_stop_state, self.k_synth_stop_price,
            self.k_synth_stop_reason, self.k_synth_stop_ts,
        ]
        values = await self._redis.mget(keys)
        
        # Medium: Handle both bytes and strings for risk_state safely (decode_responses support)
        risk_state_raw = values[7]
        if isinstance(risk_state_raw, bytes):
            risk_state = risk_state_raw.decode('utf-8')
        else:
            risk_state = str(risk_state_raw or "UNKNOWN")
            
        return {
            "realized_pnl_usd": float(values[0] or 0),
            "unrealized_pnl_usd": float(values[1] or 0),
            "open_notional_usd": float(values[2] or 0),
            "net_inventory_qty": float(values[3] or 0),
            "fees_paid_usd": float(values[4] or 0),
            "slippage_paid_usd": float(values[5] or 0),
            "last_fill_ts": int(values[6] or 0),
            "risk_state": risk_state,
            "permission_heartbeat_ts": int(values[8] or 0),
            "synthetic_stop_state": str(values[9] or "INACTIVE"),
            "synthetic_stop_price": float(values[10] or 0.0),
            "synthetic_stop_reason": str(values[11] or ""),
            "synthetic_stop_ts": int(values[12] or 0),
        }
