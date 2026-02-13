"""
Tier 7 channel telemetry helpers for stability-window SNR tracking.

Tracks outbound Discord posts (non-blocking best effort) and computes
channel signal/noise ratios over a lookback window.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

TRACKED_CHANNELS = (
    "discovery",
    "focus",
    "trades",
    "macro-updates",
    "analysis-updates",
)

NOISE_MARKERS = (
    "HEARTBEAT_OK",
    "QUIET_HOURS",
    "NO_NEW_TOKENS",
    "DRY_RUN",
    "NO CHANGE",
)

SIGNAL_MARKERS = (
    "HIGH CONVICTION",
    "NEW HIGH CONVICTION",
    "ALERT",
    "SETUP",
    "ENTRY",
    "BREAKOUT",
    "TRADE",
    "DISCOVERY",
)


def _default_telemetry_path() -> Path:
    # src/monitoring/channel_telemetry.py -> project root is parents[2]
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "data" / "telemetry" / "channel_posts.jsonl"


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


def _classify_signal(message: str) -> bool:
    msg = (message or "").upper()
    if any(marker in msg for marker in NOISE_MARKERS):
        return False
    if any(marker in msg for marker in SIGNAL_MARKERS):
        return True
    return False


def write_channel_telemetry_event(
    telemetry_path: Path | None,
    timestamp_iso: str,
    channel: str,
    message: str,
    source: str,
    posted: bool,
) -> bool:
    """
    Append one outbound-message telemetry row to JSONL.
    Returns False on any write failure (best effort, never raise).
    """
    path = telemetry_path or _default_telemetry_path()
    payload = {
        "timestamp": timestamp_iso,
        "channel": channel,
        "source": source,
        "posted": bool(posted),
        "is_signal": _classify_signal(message),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
        return True
    except Exception:
        return False


def summarize_channel_snr(telemetry_path: Path | None, lookback_hours: int = 48) -> Dict[str, Any]:
    """
    Compute per-channel SNR from telemetry rows in the lookback window.
    SNR definition: signal / noise (signal when noise == 0 and signal > 0).
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(1, int(lookback_hours)))
    path = telemetry_path or _default_telemetry_path()

    by_channel: Dict[str, Dict[str, Any]] = {
        name: {"total": 0, "signal": 0, "noise": 0, "snr": 0.0}
        for name in TRACKED_CHANNELS
    }
    events_considered = 0

    if not path.exists():
        return {"events_considered": 0, "channels": by_channel}

    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return {"events_considered": 0, "channels": by_channel}

    for raw in lines:
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except Exception:
            continue

        ts = _parse_iso8601(str(row.get("timestamp", "")))
        if ts is None or ts < cutoff:
            continue
        if not row.get("posted", True):
            continue

        channel = str(row.get("channel", ""))
        if channel not in by_channel:
            by_channel[channel] = {"total": 0, "signal": 0, "noise": 0, "snr": 0.0}

        events_considered += 1
        by_channel[channel]["total"] += 1
        if bool(row.get("is_signal")):
            by_channel[channel]["signal"] += 1
        else:
            by_channel[channel]["noise"] += 1

    for ch in by_channel:
        sig = by_channel[ch]["signal"]
        noise = by_channel[ch]["noise"]
        if sig == 0 and noise == 0:
            snr = 0.0
        elif noise == 0:
            snr = float(sig)
        else:
            snr = round(sig / noise, 3)
        by_channel[ch]["snr"] = snr

    return {"events_considered": events_considered, "channels": by_channel}
