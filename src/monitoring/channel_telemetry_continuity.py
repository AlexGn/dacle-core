"""
Channel telemetry continuity checks for Tier 7 stability confidence.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict


def _parse_iso8601(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def evaluate_channel_telemetry_continuity(
    telemetry_path: Path,
    window_hours: int = 24,
    min_events: int = 1,
    max_stale_minutes: int = 30,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    window = max(1, int(window_hours))
    stale_limit = max(1, int(max_stale_minutes))
    cutoff = now - timedelta(hours=window)

    if not telemetry_path.exists():
        return {
            "ok": False,
            "reason": "MISSING_TELEMETRY_FILE",
            "events_in_window": 0,
            "last_event_at": None,
            "window_hours": window,
        }

    try:
        lines = telemetry_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return {
            "ok": False,
            "reason": "UNREADABLE_TELEMETRY_FILE",
            "events_in_window": 0,
            "last_event_at": None,
            "window_hours": window,
        }

    events_in_window = 0
    last_event: datetime | None = None

    for raw in lines:
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except Exception:
            continue
        ts = _parse_iso8601(str(row.get("timestamp", "")))
        if ts is None:
            continue
        if last_event is None or ts > last_event:
            last_event = ts
        if ts >= cutoff:
            events_in_window += 1

    if last_event is None:
        return {
            "ok": False,
            "reason": "NO_VALID_EVENTS",
            "events_in_window": 0,
            "last_event_at": None,
            "window_hours": window,
        }

    staleness_minutes = (now - last_event).total_seconds() / 60.0
    if events_in_window < int(min_events):
        return {
            "ok": False,
            "reason": "INSUFFICIENT_EVENTS",
            "events_in_window": events_in_window,
            "last_event_at": last_event.isoformat(),
            "window_hours": window,
            "staleness_minutes": round(staleness_minutes, 2),
        }

    if staleness_minutes > stale_limit:
        return {
            "ok": False,
            "reason": "STALE_TELEMETRY",
            "events_in_window": events_in_window,
            "last_event_at": last_event.isoformat(),
            "window_hours": window,
            "staleness_minutes": round(staleness_minutes, 2),
        }

    return {
        "ok": True,
        "reason": "OK",
        "events_in_window": events_in_window,
        "last_event_at": last_event.isoformat(),
        "window_hours": window,
        "staleness_minutes": round(staleness_minutes, 2),
    }

