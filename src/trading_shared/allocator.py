import asyncio
import uuid
import json
import logging
import time
from typing import Dict, Optional, Any
from datetime import datetime, timedelta, timezone
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

class CapitalAllocator:
    """
    Manages global capital leases across concurrent strategies.
    Ensures that total allocated capital never exceeds the configured global cap.
    
    Unified Institutional Version: 
    - Atomic Lua reservation (cap + metadata)
    - Single source of truth for total_cap in Redis
    - Automatic TTL cleanup
    """

    def __init__(self, redis: Redis, total_cap: float, namespace: str = "dacle:capital"):
        self.redis = redis
        self.total_cap = total_cap
        self.namespace = namespace
        self.key_allocated = f"{namespace}:allocated"
        self.key_leases = f"{namespace}:active_leases"
        self.key_config_cap = f"{namespace}:config:total_cap"

    async def sync_global_config(self):
        """Ensure all pillars use the same global cap. First one sets it."""
        lua_script = """
        local cap_key = KEYS[1]
        local local_cap = tonumber(ARGV[1])
        local current = redis.call('GET', cap_key)
        if not current then
            redis.call('SET', cap_key, local_cap)
            return local_cap
        else
            return tonumber(current)
        end
        """
        try:
            global_cap = await self.redis.eval(lua_script, 1, self.key_config_cap, self.total_cap)
            if global_cap and float(global_cap) != self.total_cap:
                logger.warning(f"Local cap {self.total_cap} differs from global institutional cap {global_cap}. Syncing to global.")
                self.total_cap = float(global_cap)
        except Exception as e:
            logger.error(f"Failed to sync global config: {e}")

    async def get_active_lease_count(self) -> int:
        """Returns the number of globally active capital leases (proxy for concurrent positions)."""
        await self._cleanup_expired_leases()
        return await self.redis.hlen(self.key_leases)

    async def get_available(self) -> float:
        """Returns the current available (unallocated) capital from global Redis state."""
        await self.sync_global_config()
        await self._cleanup_expired_leases()
        raw = await self.redis.get(self.key_allocated)
        allocated = float(raw) if raw else 0.0
        return max(0.0, self.total_cap - allocated)

    async def request_lease(self, strategy_id: str, amount: float, ttl_sec: int = 3600, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Requests a new capital lease from the global pool.
        Uses Lua for ATOMIC check, reserve, and metadata storage.
        """
        await self.sync_global_config()
        await self._cleanup_expired_leases()
        
        lease_id = str(uuid.uuid4())
        expires_at_ts = time.time() + ttl_sec
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_sec)).isoformat()
        
        lease_info = {
            "lease_id": lease_id,
            "strategy_id": strategy_id,
            "session_id": session_id,
            "granted_amount": amount,
            "expires_at": expires_at,
            "expires_at_ts": expires_at_ts,
            "status": "ACTIVE"
        }
        
        lua_script = """
        local allocated_key = KEYS[1]
        local leases_key = KEYS[2]
        local total_cap = tonumber(ARGV[1])
        local amount = tonumber(ARGV[2])
        local lease_id = ARGV[3]
        local lease_json = ARGV[4]
        
        local current = tonumber(redis.call('GET', allocated_key) or "0")
        if current + amount <= total_cap then
            redis.call('SET', allocated_key, current + amount)
            redis.call('HSET', leases_key, lease_id, lease_json)
            -- Institutional Safety: 24h TTL on metadata hash and allocated counter
            -- This prevents leak if all cleanup loops fail simultaneously.
            redis.call('EXPIRE', allocated_key, 86400)
            redis.call('EXPIRE', leases_key, 86400)
            return 1
        else
            return 0
        end
        """
        
        try:
            success = await self.redis.eval(
                lua_script, 2, self.key_allocated, self.key_leases,
                self.total_cap, amount, lease_id, json.dumps(lease_info)
            )
            
            if not success or int(success) == 0:
                return None

            return lease_info
        except Exception as e:
            logger.error(f"Atomic global lease request failed: {e}")
            return None

    async def release_lease(self, strategy_id: str, lease_id: str):
        """
        Releases an active lease globally. Atomic decrement and metadata removal.
        """
        lua_script = """
        local allocated_key = KEYS[1]
        local leases_key = KEYS[2]
        local lease_id = ARGV[1]
        
        local raw_lease = redis.call('HGET', leases_key, lease_id)
        if not raw_lease then
            return -1
        end
        
        local lease = cjson.decode(raw_lease)
        local amount = tonumber(lease.granted_amount)
        
        local current = tonumber(redis.call('GET', allocated_key) or "0")
        local new_val = math.max(0, current - amount)
        redis.call('SET', allocated_key, new_val)
        redis.call('HDEL', leases_key, lease_id)
        return new_val
        """
        try:
            result = await self.redis.eval(lua_script, 2, self.key_allocated, self.key_leases, lease_id)
            if result != -1:
                logger.debug(f"Global lease {lease_id} released atomically.")
        except Exception as e:
            logger.error(f"Global lease release failed for {lease_id}: {e}")

    async def reclaim_strategy_leases(self, strategy_id: str) -> float:
        """
        Force-reclaims all active leases for a specific strategy.
        Useful for boot-time cleanup after a crash.
        """
        lua_script = """
        local allocated_key = KEYS[1]
        local leases_key = KEYS[2]
        local target_strategy = ARGV[1]

        local kv = redis.call('HGETALL', leases_key)
        local reclaimed = 0.0

        for i = 1, #kv, 2 do
            local lease_id = kv[i]
            local raw = kv[i + 1]
            local ok, lease = pcall(cjson.decode, raw)
            if ok and lease then
                if lease.strategy_id == target_strategy then
                    local amount = tonumber(lease.granted_amount or 0)
                    reclaimed = reclaimed + amount
                    redis.call('HDEL', leases_key, lease_id)
                end
            end
        end

        if reclaimed > 0 then
            local current = tonumber(redis.call('GET', allocated_key) or "0")
            redis.call('SET', allocated_key, math.max(0, current - reclaimed))
        end
        return reclaimed
        """
        try:
            reclaimed = await self.redis.eval(lua_script, 2, self.key_allocated, self.key_leases, strategy_id)
            if reclaimed and float(reclaimed) > 0:
                logger.info(f"Boot-reclaimed {reclaimed} USD from orphaned leases for {strategy_id}")
                return float(reclaimed)
            return 0.0
        except Exception as e:
            logger.error(f"Failed to reclaim strategy leases for {strategy_id}: {e}")
            return 0.0

    async def run_cleanup_loop(self, interval_sec: int = 60):
        """Proactive background task to reclaim orphaned/expired leases."""
        logger.info(f"Starting global capital cleanup loop ({interval_sec}s interval)")
        while True:
            try:
                await self._cleanup_expired_leases()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Capital cleanup loop error: {e}")
            await asyncio.sleep(interval_sec)

    async def _cleanup_expired_leases(self) -> None:
        """Reclaim stale lease reservations whose TTL has elapsed."""
        lua_script = """
        local allocated_key = KEYS[1]
        local leases_key = KEYS[2]
        local now_ts = tonumber(ARGV[1])

        local kv = redis.call('HGETALL', leases_key)
        local reclaimed = 0.0

        for i = 1, #kv, 2 do
            local lease_id = kv[i]
            local raw = kv[i + 1]
            local ok, lease = pcall(cjson.decode, raw)
            if ok and lease then
                local exp = tonumber(lease.expires_at_ts or 0)
                if exp > 0 and exp <= now_ts then
                    local amount = tonumber(lease.granted_amount or 0)
                    reclaimed = reclaimed + amount
                    redis.call('HDEL', leases_key, lease_id)
                end
            end
        end

        if reclaimed > 0 then
            local current = tonumber(redis.call('GET', allocated_key) or "0")
            redis.call('SET', allocated_key, math.max(0, current - reclaimed))
        end
        -- Institutional Persistence: Refresh 24h safety TTL on every check
        redis.call('EXPIRE', allocated_key, 86400)
        redis.call('EXPIRE', leases_key, 86400)
        return reclaimed
        """
        try:
            reclaimed = await self.redis.eval(
                lua_script, 2, self.key_allocated, self.key_leases, time.time()
            )
            if reclaimed and float(reclaimed) > 0:
                logger.warning(
                    "Reclaimed expired leases: %.2f %s",
                    float(reclaimed),
                    self.namespace,
                )
        except Exception as e:
            logger.error(f"Expired lease cleanup failed: {e}")
