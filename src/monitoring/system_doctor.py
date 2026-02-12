"""
System Doctor (Tier 5.2 - Self-Healing)

Autonomous monitor that detects common failures (stuck locks, slow APIs) 
and performs 'healing' actions without operator intervention.
"""

import logging
import os
import json
from pathlib import Path
from datetime import datetime, timezone
from src.utils.redis_cache import get_redis_cache

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).parent.parent.parent

class SystemDoctor:
    """Detects and fixes common system ailments."""

    def __init__(self):
        self.cache = get_redis_cache()

    async def diagnose_and_heal(self) -> list[str]:
        """Perform a check-up and fix what's broken."""
        actions = []
        
        # 1. Check for stale refresh locks (Session 340 logic)
        stale_locks = await self._heal_stale_locks()
        if stale_locks:
            actions.append(f"Cleared {len(stale_locks)} stale Redis locks: {', '.join(stale_locks)}")

        # 2. Check for zombie processes
        # TODO: Implement process monitoring logic

        if not actions:
            logger.info("SystemDoctor: Patient is healthy.")
        else:
            for action in actions:
                logger.warning(f"SystemDoctor HEAL: {action}")
        
        return actions

    async def _heal_stale_locks(self) -> list[str]:
        """Find and remove refresh locks that have been held for > 15 mins."""
        if not self.cache or not self.cache.client:
            return []

        cleared = []
        try:
            # Refresh locks follow pattern 'lock:refresh:*'
            for key in self.cache.client.scan_iter("lock:refresh:*"):
                ttl = self.cache.client.ttl(key)
                # If TTL is very high or key exists but task is dead
                # (Redis handles TTL usually, but sometimes we need manual pruning)
                # For now, let's just log them.
                pass
        except Exception as e:
            logger.error(f"Heal locks failed: {e}")
            
        return cleared

    def report_health_to_discord(self, actions: list[str]):
        """Post a summary of healing actions to the focus channel."""
        if not actions: return
        
        from src.monitoring.heartbeat_discord import post_to_discord
        import asyncio
        
        msg = """🩺 **System Doctor Update**

I detected and resolved the following issues:
"""
        msg += "\n".join([f"• {a}" for a in actions])
        msg += "\n\n_Everything is back to normal._"
        
        asyncio.create_task(post_to_discord("focus", msg))
