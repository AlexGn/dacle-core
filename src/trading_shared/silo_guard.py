"""Shared helpers for read-only cross-process silo guard keys and TTL.

Moved from src/polymarket/silo_guard.py as part of Phase 1 pillar decoupling;
consumed by both the Polymarket daemon and the Lighter CEX funding sniper.
"""

from __future__ import annotations

from typing import Optional

from src.trading_shared.capital_models import normalize_capital_namespace


def get_silo_guard_prefix(namespace: Optional[str] = None) -> str:
    ns = normalize_capital_namespace(namespace)
    if ns:
        return f"dacle:{ns}:silo_guard"
    return "dacle:silo_guard"


def get_silo_guard_key(process_name: str, namespace: Optional[str] = None) -> str:
    process = str(process_name or "").strip().lower()
    return f"{get_silo_guard_prefix(namespace)}:{process}:state:v1"


def get_silo_guard_ttl_sec(publish_interval_sec: float) -> int:
    interval = max(float(publish_interval_sec), 1.0)
    return max(int(interval * 3), 15)
