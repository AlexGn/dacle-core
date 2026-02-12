"""
Memory guard helpers for bot startup and watchdog.
"""

import os
from typing import Optional


def parse_meminfo_kb(meminfo_text: str) -> dict:
    info = {}
    for line in meminfo_text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            info[key] = int(parts[0])
        except ValueError:
            continue
    return info


def get_mem_available_mb(meminfo_text: Optional[str] = None) -> Optional[int]:
    try:
        if meminfo_text is None:
            if not os.path.exists("/proc/meminfo"):
                return None
            with open("/proc/meminfo", "r") as f:
                meminfo_text = f.read()
        info = parse_meminfo_kb(meminfo_text)
        mem_available_kb = info.get("MemAvailable")
        if mem_available_kb is None:
            return None
        return int(mem_available_kb / 1024)
    except Exception:
        return None


def get_sync_min_mem_mb() -> int:
    try:
        return int(os.getenv("BOT_SYNC_MIN_MEM_MB", "500"))
    except ValueError:
        return 500


def get_memory_alert_mb() -> int:
    try:
        return int(os.getenv("BOT_MEMORY_ALERT_MB", "300"))
    except ValueError:
        return 300


def should_skip_sync(mem_available_mb: Optional[int]) -> bool:
    if mem_available_mb is None:
        return False
    return mem_available_mb < get_sync_min_mem_mb()
