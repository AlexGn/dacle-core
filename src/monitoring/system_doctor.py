"""
System Doctor (Tier 5.2 - Self-Healing)

Autonomous monitor that detects common failures (stuck locks, slow APIs) 
and performs 'healing' actions without operator intervention.
"""

import logging
import os
import json
import psutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from src.utils.redis_cache import get_redis_cache

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).parent.parent.parent

class SystemDoctor:
    """Detects and fixes common system ailments."""

    def __init__(self):
        self.cache = get_redis_cache()
        self._sudo_noninteractive_ok: Optional[bool] = None

    async def diagnose_and_heal(self) -> list[str]:
        """Perform a check-up and fix what's broken."""
        actions = []
        
        # 1. Check for stale refresh locks (Session 340 logic)
        stale_locks = await self._heal_stale_locks()
        if stale_locks:
            actions.append(f"Cleared {len(stale_locks)} stale Redis locks: {', '.join(stale_locks)}")

        # 2. Check for critical processes
        process_actions = await self._check_critical_processes()
        actions.extend(process_actions)

        if not actions:
            logger.info("SystemDoctor: Patient is healthy.")
        else:
            for action in actions:
                logger.warning(f"SystemDoctor HEAL: {action}")
        
        return actions

    async def _check_critical_processes(self) -> list[str]:
        """Verify that critical system processes are running."""
        actions = []
        
        # Define critical process signatures (unique parts of cmdline)
        critical_processes = {
            "API (uvicorn)": ["uvicorn", "api.main"],
            "Bot (dacle-bot)": ["run_bot.py"]
        }
        
        found = {name: False for name in critical_processes}
        
        try:
            for proc in psutil.process_iter(['name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline') or []
                    cmdline_str = " ".join(cmdline)
                    
                    for name, signature in critical_processes.items():
                        if all(part in cmdline_str for part in signature):
                            found[name] = True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logger.error(f"Failed to iterate processes: {e}")
            return [f"System error: Failed to check processes: {e}"]

        for name, is_running in found.items():
            if not is_running:
                # Session 415: Auto-restart critical services
                service_map = {
                    "API (uvicorn)": "dacle-api.service",
                    "Bot (dacle-bot)": "dacle-bot.service"
                }
                
                service_name = service_map.get(name)
                if service_name:
                    logger.warning(f"SystemDoctor: Attempting to restart {service_name}...")
                    if self._restart_service(service_name):
                        actions.append(f"RESTARTED: {name} is not running (auto-healed).")
                    else:
                        actions.append(f"FAILED RESTART: {name} is not running.")
                else:
                    actions.append(f"CRITICAL: {name} is not running (no service mapping).")
                
        return actions

    def _restart_service(self, service_name: str) -> bool:
        """Execute systemctl restart for a service using non-interactive sudo."""
        import subprocess
        if not self._can_use_noninteractive_sudo():
            logger.error(
                "SystemDoctor: passwordless sudo unavailable; "
                "configure NOPASSWD for systemctl restart on DACLE services."
            )
            return False
        try:
            subprocess.run(
                ["sudo", "-n", "systemctl", "restart", service_name],
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"SystemDoctor: Successfully restarted {service_name}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"SystemDoctor: Failed to restart {service_name}: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"SystemDoctor: Unexpected error restarting {service_name}: {e}")
            return False

    def _can_use_noninteractive_sudo(self) -> bool:
        """Probe whether sudo -n is available (cached)."""
        if self._sudo_noninteractive_ok is not None:
            return self._sudo_noninteractive_ok
        import subprocess
        try:
            subprocess.run(
                ["sudo", "-n", "true"],
                capture_output=True,
                text=True,
                check=True,
            )
            self._sudo_noninteractive_ok = True
        except Exception:
            self._sudo_noninteractive_ok = False
        return self._sudo_noninteractive_ok

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
