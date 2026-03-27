import json
import logging
import time
import uuid
import asyncio
from typing import Dict, Optional, Any, List
from redis.asyncio import Redis
from src.trading_shared.capital_models import (
    get_config_key,
    get_allocated_key,
    get_strategy_allocated_key,
    get_strategy_registry_key,
    get_active_leases_key,
    get_preemption_channel,
    get_preemption_stream,
    get_pending_preemptions_key,
    normalize_capital_namespace,
    UnifiedLease,
    LeaseStatus
)

logger = logging.getLogger(__name__)

LUA_REQUEST_LEASE = """
local config_key = KEYS[1]
local allocated_key = KEYS[2]
local strategy_allocated_key = KEYS[3]
local strategy_registry_key = KEYS[4]
local active_leases_key = KEYS[5]
local preemption_channel = KEYS[6]
local preemption_stream = KEYS[7]
local pending_preemptions_key = KEYS[8]

local strategy_id = ARGV[1]
local requested_cents = tonumber(ARGV[2])
local priority_tier = tonumber(ARGV[3])
local ttl_sec = tonumber(ARGV[4])
local lease_id = ARGV[5]
local now_ts = tonumber(ARGV[6])
local session_id = ARGV[7]

-- 1. Fetch config and registry
local config_raw = redis.call('HGETALL', config_key)
if #config_raw == 0 then
    -- Default config if missing
    redis.call('HSET', config_key, 'global_cap_cents', 100000000, 'schema_version', 1, 'updated_at_ts', now_ts)
end
local global_cap_cents = tonumber(redis.call('HGET', config_key, 'global_cap_cents') or "0")

local registry_raw = redis.call('HGET', strategy_registry_key, strategy_id)
if not registry_raw then
    return cjson.encode({status = "REJECT_STRATEGY_NOT_REGISTERED"})
end
local strategy_registry = cjson.decode(registry_raw)
if not strategy_registry.enabled then
    return cjson.encode({status = "REJECT_STRATEGY_DISABLED"})
end

-- 2. Check strategy cap
local strategy_allocated = tonumber(redis.call('HGET', strategy_allocated_key, strategy_id) or "0")
if strategy_allocated + requested_cents > tonumber(strategy_registry.strategy_cap_cents) then
    return cjson.encode({status = "REJECT_STRATEGY_CAP"})
end

-- 3. Check global cap
local total_allocated = tonumber(redis.call('GET', allocated_key) or "0")
if total_allocated + requested_cents <= global_cap_cents then
    -- APPROVED
    redis.call('SET', allocated_key, total_allocated + requested_cents)
    redis.call('HINCRBY', strategy_allocated_key, strategy_id, requested_cents)
    
    local lease = {
        lease_id = lease_id,
        strategy_id = strategy_id,
        priority_tier = priority_tier,
        granted_amount_cents = requested_cents,
        requested_amount_cents = requested_cents,
        created_at_ts = now_ts,
        expires_at_ts = now_ts + ttl_sec,
        status = "ACTIVE",
        session_id = session_id
    }
    redis.call('HSET', active_leases_key, lease_id, cjson.encode(lease))
    
    -- Safety TTL (24h)
    redis.call('EXPIRE', allocated_key, 86400)
    redis.call('EXPIRE', strategy_allocated_key, 86400)
    redis.call('EXPIRE', active_leases_key, 86400)
    
    return cjson.encode({
        status = "APPROVED",
        lease_id = lease_id,
        granted_amount_cents = requested_cents,
        lease_status = "ACTIVE"
    })
end

-- 4. Preemption Logic
-- Only if global cap is breached
-- Find ACTIVE leases with priority_tier > requester.priority_tier
-- Order by oldest created_at_ts (OLDEST_FIRST)
local candidates = {}
local leases_kv = redis.call('HGETALL', active_leases_key)
for i = 1, #leases_kv, 2 do
    local lid = leases_kv[i]
    local lraw = leases_kv[i+1]
    local lease = cjson.decode(lraw)
    if lease.status == "ACTIVE" and lease.priority_tier > priority_tier then
        local age = now_ts - lease.created_at_ts
        if age >= tonumber(strategy_registry.min_age_seconds or 30) then
            table.insert(candidates, lease)
        end
    end
end

-- Sort by created_at_ts (oldest first)
table.sort(candidates, function(a, b) return a.created_at_ts < b.created_at_ts end)

local to_preempt = {}
local reclaimed_potential = 0
for _, c in ipairs(candidates) do
    table.insert(to_preempt, c)
    reclaimed_potential = reclaimed_potential + c.granted_amount_cents
    if total_allocated - reclaimed_potential + requested_cents <= global_cap_cents then
        break
    end
end

if #to_preempt > 0 and (total_allocated - reclaimed_potential + requested_cents <= global_cap_cents) then
    -- Mark for preemption
    for _, p in ipairs(to_preempt) do
        p.status = "REVOKE_PENDING"
        p.revoke_reason = "HIGHER_PRIORITY_REQUEST"
        p.preempted_by = strategy_id
        redis.call('HSET', active_leases_key, p.lease_id, cjson.encode(p))
        
        -- Emit Event
        local event = {
            event = "LEASE_PREEMPTED",
            lease_id = p.lease_id,
            strategy_id = p.strategy_id,
            preempted_by = strategy_id,
            ts = now_ts,
            reason = "HIGHER_PRIORITY_REQUEST"
        }
        local event_json = cjson.encode(event)
        redis.call('PUBLISH', preemption_channel, event_json)
        redis.call('XADD', preemption_stream, 'MAXLEN', '~', 5000, '*', 'data', event_json)
        
        -- Add to pending deadlines (e.g., 60s from now)
        redis.call('ZADD', pending_preemptions_key, now_ts + 60, p.lease_id)
    end
    
    return cjson.encode({
        status = "PREEMPT_PENDING",
        pending_count = #to_preempt,
        retry_after_ms = 2000
    })
end

return cjson.encode({status = "REJECT_GLOBAL_CAP"})
"""

LUA_RELEASE_LEASE = """
local allocated_key = KEYS[1]
local strategy_allocated_key = KEYS[2]
local active_leases_key = KEYS[3]

local strategy_id = ARGV[1]
local lease_id = ARGV[2]
local reason = ARGV[3]

local raw_lease = redis.call('HGET', active_leases_key, lease_id)
if not raw_lease then
    return cjson.encode({status = "NOOP_NOT_FOUND"})
end

local lease = cjson.decode(raw_lease)
if lease.status == "RELEASED" or lease.status == "EXPIRED" then
    return cjson.encode({status = "NOOP_ALREADY_RELEASED"})
end

if lease.strategy_id ~= strategy_id then
    return cjson.encode({status = "REJECT_OWNER_MISMATCH"})
end

local amount = tonumber(lease.granted_amount_cents)

-- Decrement counters
local current_total = tonumber(redis.call('GET', allocated_key) or "0")
redis.call('SET', allocated_key, math.max(0, current_total - amount))

local current_strat = tonumber(redis.call('HGET', strategy_allocated_key, strategy_id) or "0")
redis.call('HSET', strategy_allocated_key, strategy_id, math.max(0, current_strat - amount))

-- Update lease status
lease.status = "RELEASED"
lease.release_reason = reason
redis.call('HSET', active_leases_key, lease_id, cjson.encode(lease))

return cjson.encode({status = "RELEASED", released_amount_cents = amount})
"""

LUA_RECLAIM_STRATEGY_LEASES = """
local allocated_key = KEYS[1]
local strategy_allocated_key = KEYS[2]
local active_leases_key = KEYS[3]
local target_strategy = ARGV[1]

local leases_kv = redis.call('HGETALL', active_leases_key)
local reclaimed_total = 0

for i = 1, #leases_kv, 2 do
    local lid = leases_kv[i]
    local lraw = leases_kv[i+1]
    local lease = cjson.decode(lraw)
    
    if lease.strategy_id == target_strategy and lease.status ~= "RELEASED" and lease.status ~= "EXPIRED" then
        local amount = tonumber(lease.granted_amount_cents or 0)
        reclaimed_total = reclaimed_total + amount
        
        -- Mark as released (boot cleanup reason)
        lease.status = "RELEASED"
        lease.release_reason = "BOOT_RECLAIM"
        redis.call('HSET', active_leases_key, lid, cjson.encode(lease))
    end
end

if reclaimed_total > 0 then
    local current_total = tonumber(redis.call('GET', allocated_key) or "0")
    redis.call('SET', allocated_key, math.max(0, current_total - reclaimed_total))
    
    local current_strat = tonumber(redis.call('HGET', strategy_allocated_key, target_strategy) or "0")
    redis.call('HSET', strategy_allocated_key, target_strategy, math.max(0, current_strat - reclaimed_total))
end

return reclaimed_total
"""

class UnifiedCapitalAllocator:
    """
    Institutional Unified Capital Allocator with Priority-Aware Preemption.
    Follows v2.2 spec (Audit-Integrated).
    """

    def __init__(self, redis: Redis, namespace: Optional[str] = None):
        self.redis = redis
        self.namespace = normalize_capital_namespace(namespace)

    async def request_lease(
        self,
        strategy_id: str,
        amount_cents: int,
        priority_tier: int,
        ttl_sec: int = 3600,
        session_id: Optional[str] = None,
        max_retries: int = 3,
        backoff_factor: float = 1.5
    ) -> Dict[str, Any]:
        """
        Requests a lease from the unified pool with institutional retry logic.
        - Retries on PREEMPT_PENDING (Wait for lower-tier strategies to flatten).
        - Exponential backoff with jitter.
        """
        lease_id = str(uuid.uuid4())
        now_ts = time.time()
        
        keys = [
            get_config_key(self.namespace),
            get_allocated_key(self.namespace),
            get_strategy_allocated_key(self.namespace),
            get_strategy_registry_key(self.namespace),
            get_active_leases_key(self.namespace),
            get_preemption_channel(self.namespace),
            get_preemption_stream(self.namespace),
            get_pending_preemptions_key(self.namespace)
        ]
        
        # Use attempt 0 as initial
        for attempt in range(max_retries + 1):
            args = [
                strategy_id,
                amount_cents,
                priority_tier,
                ttl_sec,
                lease_id,
                time.time(), # Refresh TS for each attempt
                session_id or ""
            ]
            
            try:
                result_json = await self.redis.eval(LUA_REQUEST_LEASE, len(keys), *keys, *args)
                res = json.loads(result_json)
                
                status = res.get("status")
                if status == "PREEMPT_PENDING":
                    if attempt < max_retries:
                        # Institutional backoff
                        base_wait_ms = res.get("retry_after_ms", 1000)
                        # Exp Backoff + Jitter
                        wait_sec = (base_wait_ms / 1000.0) * (backoff_factor ** attempt)
                        # Capped at 8 seconds to prevent excessive trade stall
                        wait_sec = min(wait_sec, 8.0)
                        
                        logger.warning(
                            f"PREEMPTION PENDING (Attempt {attempt+1}/{max_retries+1}) for {strategy_id}. "
                            f"Waiting {wait_sec:.2f}s..."
                        )
                        await asyncio.sleep(wait_sec)
                        continue # Retry
                    else:
                        logger.error(f"PREEMPTION TIMEOUT for {strategy_id} after {max_retries} retries")
                        return res
                
                return res
                
            except Exception as e:
                logger.error(f"Unified lease request failed (Attempt {attempt}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1.0)
                    continue
                return {"status": "ERROR", "message": str(e)}
        
        return {"status": "ERROR", "message": "Max retries exceeded"}

    async def release_lease(self, strategy_id: str, lease_id: str, reason: str = "NORMAL") -> Dict[str, Any]:
        """
        Releases a lease atomically.
        """
        keys = [
            get_allocated_key(self.namespace),
            get_strategy_allocated_key(self.namespace),
            get_active_leases_key(self.namespace)
        ]
        
        args = [
            strategy_id,
            lease_id,
            reason
        ]
        
        try:
            result_json = await self.redis.eval(LUA_RELEASE_LEASE, len(keys), *keys, *args)
            return json.loads(result_json)
        except Exception as e:
            logger.error(f"Unified lease release failed: {e}")
            return {"status": "ERROR", "message": str(e)}

    async def reclaim_strategy_leases(self, strategy_id: str) -> int:
        """
        Force-reclaims all active leases for a strategy.
        Useful for boot-time cleanup.
        """
        keys = [
            get_allocated_key(self.namespace),
            get_strategy_allocated_key(self.namespace),
            get_active_leases_key(self.namespace)
        ]
        try:
            reclaimed = await self.redis.eval(LUA_RECLAIM_STRATEGY_LEASES, len(keys), *keys, strategy_id)
            if reclaimed and int(reclaimed) > 0:
                logger.info(f"Unified boot-reclaimed {reclaimed} cents for {strategy_id}")
                return int(reclaimed)
            return 0
        except Exception as e:
            logger.error(f"Unified strategy reclaim failed for {strategy_id}: {e}")
            return 0

    async def register_strategy(
        self,
        strategy_id: str,
        priority_tier: int,
        strategy_cap_cents: int,
        enabled: bool = True,
        min_age_seconds: int = 30,
    ) -> None:
        """Register a strategy in the Redis registry. Idempotent — safe to call on every startup."""
        entry = {
            "priority_tier": priority_tier,
            "strategy_cap_cents": strategy_cap_cents,
            "enabled": enabled,
            "min_age_seconds": min_age_seconds,
        }
        try:
            await self.redis.hset(
                get_strategy_registry_key(self.namespace),
                strategy_id,
                json.dumps(entry),
            )
            logger.info(
                f"Strategy registered: {strategy_id} "
                f"(tier={priority_tier}, cap={strategy_cap_cents} cents, enabled={enabled})"
            )
        except Exception as e:
            logger.error(f"Failed to register strategy {strategy_id}: {e}")

    async def get_active_leases(self, strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetches all active leases from Redis, optionally filtered by strategy.
        """
        try:
            raw_dict = await self.redis.hgetall(get_active_leases_key(self.namespace))
            leases = []
            for lid, lraw in raw_dict.items():
                lease = json.loads(lraw)
                if strategy_id and lease.get("strategy_id") != strategy_id:
                    continue
                if lease.get("status") in ["ACTIVE", "REVOKE_PENDING", "STUCK_FLATTENING"]:
                    leases.append(lease)
            return leases
        except Exception as e:
            logger.error(f"Failed to fetch active leases: {e}")
            return []

    async def get_active_lease_count(self, strategy_id: Optional[str] = None) -> int:
        """Compatibility helper for callers expecting the legacy allocator API."""
        leases = await self.get_active_leases(strategy_id=strategy_id)
        return len(leases)
