"""
Service restart SLO helpers.

Classifies restart velocity per service over a fixed window (default 24h).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable


def classify_restart_slo(restarts_in_window: float) -> str:
    """
    Restart SLO thresholds:
    - HEALTHY: < 2 restarts/hour
    - DEGRADED: >= 2 and < 5 restarts/hour
    - CRITICAL: >= 5 restarts/hour
    """
    count = float(restarts_in_window)
    if count >= 5.0:
        return "CRITICAL"
    if count >= 2.0:
        return "DEGRADED"
    return "HEALTHY"


def summarize_restart_slo(
    restart_counts_24h: Dict[str, Any] | None,
    services: Iterable[str] = ("dacle-api", "dacle-bot"),
    window_hours: int = 24,
) -> Dict[str, Any]:
    counts = restart_counts_24h or {}
    safe_window = max(1, int(window_hours))
    by_service: Dict[str, Dict[str, Any]] = {}
    overall = "HEALTHY"

    for svc in services:
        restarts = int(counts.get(svc, 0) or 0)
        rate = round(restarts / safe_window, 3)
        status = classify_restart_slo(restarts)
        by_service[svc] = {
            "restarts": restarts,
            "window_hours": safe_window,
            "restarts_per_hour": rate,
            "status": status,
        }
        if status == "CRITICAL":
            overall = "CRITICAL"
        elif status == "DEGRADED" and overall == "HEALTHY":
            overall = "DEGRADED"

    return {
        "overall_status": overall,
        "services": by_service,
    }
