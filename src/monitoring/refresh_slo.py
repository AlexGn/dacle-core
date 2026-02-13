"""Refresh SLO metrics and lightweight alert evaluation."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
METRICS_PATH = PROJECT_ROOT / "data" / "analytics" / "refresh_slo_metrics.json"
CONFIG_PATH = PROJECT_ROOT / "config" / "refresh_slo.json"

DEFAULT_CONFIG = {
    "min_samples_for_alerts": 20,
    "alert_cooldown_seconds": 1800,
    "max_durations": 2000,
    "history_limit": 5000,
    "thresholds": {
        "success_rate_min": 0.90,
        "timeout_rate_max": 0.20,
        "lock_conflict_rate_max": 0.15,
        "p95_duration_seconds_max": 300.0,
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_metrics() -> Dict[str, Any]:
    return {
        "updated_at": _utc_now(),
        "last_alert_at": None,
        "total": 0,
        "completed": 0,
        "completed_with_warnings": 0,
        "failed": 0,
        "skipped": 0,
        "timeouts": 0,
        "lock_conflicts": 0,
        "durations_seconds": [],
        "history": [],
    }


def _build_config(override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = {
        "min_samples_for_alerts": int(DEFAULT_CONFIG["min_samples_for_alerts"]),
        "alert_cooldown_seconds": int(DEFAULT_CONFIG["alert_cooldown_seconds"]),
        "max_durations": int(DEFAULT_CONFIG["max_durations"]),
        "history_limit": int(DEFAULT_CONFIG["history_limit"]),
        "thresholds": dict(DEFAULT_CONFIG["thresholds"]),
    }
    if not isinstance(override, dict):
        return cfg

    for key in ("min_samples_for_alerts", "alert_cooldown_seconds", "max_durations", "history_limit"):
        if key in override and isinstance(override[key], (int, float)):
            cfg[key] = int(override[key])
    override_thresholds = override.get("thresholds")
    if isinstance(override_thresholds, dict):
        for key, value in override_thresholds.items():
            if isinstance(value, (int, float)):
                cfg["thresholds"][key] = float(value)
    return cfg


def _load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    if not path.exists():
        return _build_config()
    try:
        payload = json.loads(path.read_text())
    except Exception:
        payload = None
    return _build_config(payload if isinstance(payload, dict) else None)


def _load_metrics(path: Path = METRICS_PATH) -> Dict[str, Any]:
    if not path.exists():
        return _default_metrics()
    try:
        payload = json.loads(path.read_text())
        if isinstance(payload, dict):
            data = _default_metrics()
            data.update(payload)
            if not isinstance(data.get("durations_seconds"), list):
                data["durations_seconds"] = []
            if not isinstance(data.get("history"), list):
                data["history"] = []
            return data
    except Exception:
        pass
    return _default_metrics()


def _save_metrics(metrics: Dict[str, Any], path: Path = METRICS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(metrics, indent=2))
    tmp.replace(path)


def _compute_percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil((pct / 100.0) * len(ordered)) - 1
    rank = max(0, min(rank, len(ordered) - 1))
    return float(ordered[rank])


def _build_snapshot(metrics: Dict[str, Any]) -> Dict[str, Any]:
    total = max(int(metrics.get("total", 0)), 1)
    completed = int(metrics.get("completed", 0))
    completed_warn = int(metrics.get("completed_with_warnings", 0))
    timeouts = int(metrics.get("timeouts", 0))
    lock_conflicts = int(metrics.get("lock_conflicts", 0))
    durations = [float(v) for v in metrics.get("durations_seconds", []) if isinstance(v, (int, float))]

    snapshot = {
        "total": int(metrics.get("total", 0)),
        "success_rate": round((completed + completed_warn) / total, 4),
        "timeout_rate": round(timeouts / total, 4),
        "lock_conflict_rate": round(lock_conflicts / total, 4),
        "p95_duration_seconds": round(_compute_percentile(durations, 95), 3),
    }
    history = metrics.get("history", [])
    if isinstance(history, list):
        snapshot["window_1h"] = _build_window_snapshot(history, window_seconds=3600)
        snapshot["window_24h"] = _build_window_snapshot(history, window_seconds=86400)
    return snapshot


def _build_window_snapshot(events: list[dict], window_seconds: int, now_dt: Optional[datetime] = None) -> Dict[str, Any]:
    now = now_dt or datetime.now(timezone.utc)
    cutoff = now.timestamp() - max(0, int(window_seconds))
    filtered: list[dict] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        ts = event.get("timestamp")
        if not isinstance(ts, str):
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if dt.timestamp() >= cutoff:
            filtered.append(event)

    total = max(len(filtered), 1)
    completed = 0
    completed_warn = 0
    timeouts = 0
    lock_conflicts = 0
    durations = []
    for event in filtered:
        status = str(event.get("status") or "").lower()
        if status == "completed":
            completed += 1
        elif status == "completed_with_warnings":
            completed_warn += 1
        failure_class = event.get("failure_class")
        if failure_class == "PIPELINE_TIMEOUT":
            timeouts += 1
        elif failure_class == "CONCURRENT_REFRESH_LOCK":
            lock_conflicts += 1
        if isinstance(event.get("duration_seconds"), (int, float)):
            durations.append(float(event["duration_seconds"]))

    return {
        "total": len(filtered),
        "success_rate": round((completed + completed_warn) / total, 4),
        "timeout_rate": round(timeouts / total, 4),
        "lock_conflict_rate": round(lock_conflicts / total, 4),
        "p95_duration_seconds": round(_compute_percentile(durations, 95), 3),
    }


def _evaluate_alert(
    snapshot: Dict[str, Any],
    metrics: Dict[str, Any],
    now_dt: datetime,
    config: Dict[str, Any],
) -> Optional[str]:
    candidate = snapshot.get("window_24h") if isinstance(snapshot.get("window_24h"), dict) else snapshot
    total = candidate.get("total", 0)
    if total < int(config.get("min_samples_for_alerts", DEFAULT_CONFIG["min_samples_for_alerts"])):
        return None

    last_alert_raw = metrics.get("last_alert_at")
    if last_alert_raw:
        try:
            last_alert = datetime.fromisoformat(last_alert_raw.replace("Z", "+00:00"))
            cooldown = int(config.get("alert_cooldown_seconds", DEFAULT_CONFIG["alert_cooldown_seconds"]))
            if (now_dt - last_alert).total_seconds() < cooldown:
                return None
        except Exception:
            pass

    thresholds = config.get("thresholds") if isinstance(config.get("thresholds"), dict) else DEFAULT_CONFIG["thresholds"]
    success_min = float(thresholds.get("success_rate_min", DEFAULT_CONFIG["thresholds"]["success_rate_min"]))
    timeout_max = float(thresholds.get("timeout_rate_max", DEFAULT_CONFIG["thresholds"]["timeout_rate_max"]))
    lock_max = float(thresholds.get("lock_conflict_rate_max", DEFAULT_CONFIG["thresholds"]["lock_conflict_rate_max"]))
    p95_max = float(thresholds.get("p95_duration_seconds_max", DEFAULT_CONFIG["thresholds"]["p95_duration_seconds_max"]))

    if candidate["success_rate"] < success_min:
        return (
            f"Refresh SLO alert: success_rate={candidate['success_rate']:.2%} "
            f"below {success_min:.0%}"
        )
    if candidate["timeout_rate"] > timeout_max:
        return (
            f"Refresh SLO alert: timeout_rate={candidate['timeout_rate']:.2%} "
            f"above {timeout_max:.0%}"
        )
    if candidate["lock_conflict_rate"] > lock_max:
        return (
            f"Refresh SLO alert: lock_conflict_rate={candidate['lock_conflict_rate']:.2%} "
            f"above {lock_max:.0%}"
        )
    if candidate["p95_duration_seconds"] > p95_max:
        return (
            f"Refresh SLO alert: p95_duration={candidate['p95_duration_seconds']:.1f}s "
            f"above {p95_max:.1f}s"
        )

    return None


def record_refresh_outcome(
    *,
    status: str,
    duration_seconds: Optional[float] = None,
    failure_class: Optional[str] = None,
    path: Path = METRICS_PATH,
) -> Tuple[Dict[str, Any], Optional[str]]:
    config = _load_config()
    metrics = _load_metrics(path)
    metrics["total"] = int(metrics.get("total", 0)) + 1
    metrics["updated_at"] = _utc_now()

    status_key = str(status or "").lower()
    if status_key == "completed":
        metrics["completed"] = int(metrics.get("completed", 0)) + 1
    elif status_key == "completed_with_warnings":
        metrics["completed_with_warnings"] = int(metrics.get("completed_with_warnings", 0)) + 1
    elif status_key == "failed":
        metrics["failed"] = int(metrics.get("failed", 0)) + 1
    elif status_key == "skipped":
        metrics["skipped"] = int(metrics.get("skipped", 0)) + 1

    if failure_class == "PIPELINE_TIMEOUT":
        metrics["timeouts"] = int(metrics.get("timeouts", 0)) + 1
    if failure_class == "CONCURRENT_REFRESH_LOCK":
        metrics["lock_conflicts"] = int(metrics.get("lock_conflicts", 0)) + 1

    if duration_seconds is not None:
        durations = metrics.get("durations_seconds", [])
        durations.append(round(float(duration_seconds), 3))
        max_durations = int(config.get("max_durations", DEFAULT_CONFIG["max_durations"]))
        if len(durations) > max_durations:
            durations = durations[-max_durations:]
        metrics["durations_seconds"] = durations

    history = metrics.get("history", [])
    history.append(
        {
            "timestamp": _utc_now(),
            "status": status_key,
            "failure_class": failure_class,
            "duration_seconds": round(float(duration_seconds), 3) if duration_seconds is not None else None,
        }
    )
    history_limit = int(config.get("history_limit", DEFAULT_CONFIG["history_limit"]))
    if len(history) > history_limit:
        history = history[-history_limit:]
    metrics["history"] = history

    snapshot = _build_snapshot(metrics)
    now_dt = datetime.now(timezone.utc)
    alert_message = _evaluate_alert(snapshot, metrics, now_dt, config)
    if alert_message:
        metrics["last_alert_at"] = now_dt.isoformat().replace("+00:00", "Z")

    _save_metrics(metrics, path)
    return snapshot, alert_message


def get_refresh_slo_snapshot(path: Path = METRICS_PATH) -> Dict[str, Any]:
    metrics = _load_metrics(path)
    snapshot = _build_snapshot(metrics)
    return {"snapshot": snapshot, "metrics": metrics, "config": _load_config()}
