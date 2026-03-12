import asyncio
import time
import json
from typing import Dict, Optional, Any
from redis.asyncio import Redis

class RateLimitManager:
    """
    Manages API quota and rate limits across concurrent strategies.
    Implements Token Bucket algorithm globally via Redis.
    
    Unified Institutional Version: 
    - Supports independent refill rates and burst capacities.
    - Centralized budget enforcement via Lua.
    """

    def __init__(self, redis: Redis, strategy_budgets: Dict[str, float], capacity_multiplier: float = 10.0, emergency_pool_size: int = 10, emergency_refill_rate: float = 1/3600):
        self.redis = redis
        # strategy_budgets: Dict[strategy_id, refill_rate_per_sec]
        self.strategy_budgets = strategy_budgets
        if "GLOBAL" not in self.strategy_budgets:
            self.strategy_budgets["GLOBAL"] = 5.0 # Institutional default: 5 req/sec aggregate
            
        self.capacity_multiplier = capacity_multiplier
        self.emergency_pool_size = float(emergency_pool_size)
        self.emergency_refill_rate = emergency_refill_rate
        self.key_buckets = "dacle:rate_limit:buckets"

    async def request_quota(self, strategy_id: str, is_emergency: bool = False) -> bool:
        """
        Requests quota from the global Redis-backed bucket.
        Institutional safety: also checks the 'GLOBAL' aggregate budget.
        """
        if is_emergency or strategy_id == "GLOBAL":
            return await self._check_bucket(strategy_id, is_emergency)

        # Enforce global budget first.
        global_ok = await self._check_bucket("GLOBAL", False)
        if not global_ok:
            return False

        strategy_ok = await self._check_bucket(strategy_id, False)
        if strategy_ok:
            return True

        await self._refund_standard_token("GLOBAL")
        return False

    async def _check_bucket(self, strategy_id: str, is_emergency: bool) -> bool:
        refill_rate = self.strategy_budgets.get(strategy_id, 1.0)
        # Capacity defaults to multiplier * refill_rate (allows bursts)
        capacity = refill_rate * self.capacity_multiplier
        
        # Override capacity for background tasks like scanner to allow large initial scan
        if "scanner" in strategy_id:
            capacity = max(capacity, 100.0)

        lua_script = """
        local buckets_key = KEYS[1]
        local strategy_id = ARGV[1]
        local now = tonumber(ARGV[2])
        local refill_rate = tonumber(ARGV[3])
        local capacity = tonumber(ARGV[4])
        local is_emergency = ARGV[5] == "true"
        local emer_cap = tonumber(ARGV[6])
        local emer_refill_rate = tonumber(ARGV[7])
        
        local raw = redis.call('HGET', buckets_key, strategy_id)
        local bucket
        if raw then
            bucket = cjson.decode(raw)
        else
            bucket = {
                tokens = capacity,
                emergency_tokens = emer_cap,
                last_refill = now
            }
        end
        
        local elapsed = now - bucket.last_refill
        
        -- 1. Standard Refill
        bucket.tokens = math.min(capacity, bucket.tokens + (elapsed * refill_rate))
        
        -- 2. Emergency Refill
        bucket.emergency_tokens = math.min(emer_cap, bucket.emergency_tokens + (elapsed * emer_refill_rate))
        bucket.last_refill = now
        
        local success = false
        if is_emergency then
            if bucket.emergency_tokens >= 1.0 then
                bucket.emergency_tokens = bucket.emergency_tokens - 1.0
                success = true
            end
        else
            if bucket.tokens >= 1.0 then
                bucket.tokens = bucket.tokens - 1.0
                success = true
            end
        end
        
        redis.call('HSET', buckets_key, strategy_id, cjson.encode(bucket))
        return success and 1 or 0
        """
        
        try:
            result = await self.redis.eval(
                lua_script, 1, self.key_buckets, 
                strategy_id, time.time(), refill_rate, capacity,
                "true" if is_emergency else "false",
                self.emergency_pool_size,
                self.emergency_refill_rate
            )
            return bool(result)
        except Exception:
            return False

    async def _refund_standard_token(self, strategy_id: str) -> None:
        """Best-effort rollback of one standard token for a strategy bucket."""
        refill_rate = self.strategy_budgets.get(strategy_id, 1.0)
        capacity = refill_rate * self.capacity_multiplier
        if "scanner" in strategy_id: capacity = max(capacity, 100.0)

        lua_script = """
        local buckets_key = KEYS[1]
        local strategy_id = ARGV[1]
        local now = tonumber(ARGV[2])
        local capacity = tonumber(ARGV[3])
        local emer_cap = tonumber(ARGV[4])

        local raw = redis.call('HGET', buckets_key, strategy_id)
        local bucket
        if raw then
            bucket = cjson.decode(raw)
        else
            bucket = {
                tokens = 0.0,
                emergency_tokens = emer_cap,
                last_refill = now
            }
        end

        bucket.tokens = math.min(capacity, bucket.tokens + 1.0)
        bucket.last_refill = now
        redis.call('HSET', buckets_key, strategy_id, cjson.encode(bucket))
        return 1
        """
        try:
            await self.redis.eval(
                lua_script, 1, self.key_buckets,
                strategy_id, time.time(), capacity, self.emergency_pool_size
            )
        except Exception:
            return
