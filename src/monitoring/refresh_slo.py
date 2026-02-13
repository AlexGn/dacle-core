"""Refresh SLO metrics and lightweight alert evaluation."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
METRICS_PATH = PROJECT_ROOT / "data" / "analytics" / "refresh_slo_metrics.json"
MAX_DURATIONS = 2000
ALERT_COOLDOWN_SECONDS = 1800


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
    }


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

    return {
        "total": int(metrics.get("total", 0)),
        "success_rate": round((completed + completed_warn) / total, 4),
        "timeout_rate": round(timeouts / total, 4),
        "lock_conflict_rate": round(lock_conflicts / total, 4),
        "p95_duration_seconds": round(_compute_percentile(durations, 95), 3),
    }


def _evaluate_alert(snapshot: Dict[str, Any], metrics: Dict[str, Any], now_dt: datetime) -> Optional[str]:
    total = snapshot.get("total", 0)
    if total < 20:
        return None

    last_alert_raw = metrics.get("last_alert_at")
    if last_alert_raw:
        try:
            last_alert = datetime.fromisoformat(last_alert_raw.replace("Z", "+00:00"))
            if (now_dt - last_alert).total_seconds() < ALERT_COOLDOWN_SECONDS:
                return None
        except Exception:
            pass

    if snapshot["success_rate"] < 0.90:
        return f"Refresh SLO alert: success_rate={snapshot['success_rate']:.2%} below 90%"
    if snapshot["timeout_rate"] > 0.20:
        return f"Refresh SLO alert: timeout_rate={snapshot['timeout_rate']:.2%} above 20%"
    if snapshot["lock_conflict_rate"] > 0.15:
        return f"Refresh SLO alert: lock_conflict_rate={snapshot['lock_conflict_rate']:.2%} above 15%"
    if snapshot["p95_duration_seconds"] > 300:
        return f"Refresh SLO alert: p95_duration={snapshot['p95_duration_seconds']:.1f}s above 300s"

    return None


def record_refresh_outcome(
    *,
    status: str,
    duration_seconds: Optional[float] = None,
    failure_class: Optional[str] = None,
    path: Path = METRICS_PATH,
) -> Tuple[Dict[str, Any], Optional[str]]:
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
        if len(durations) > MAX_DURATIONS:
            durations = durations[-MAX_DURATIONS:]
        metrics["durations_seconds"] = durations

    snapshot = _build_snapshot(metrics)
    now_dt = datetime.now(timezone.utc)
    alert_message = _evaluate_alert(snapshot, metrics, now_dt)
    if alert_message:
        metrics["last_alert_at"] = now_dt.isoformat().replace("+00:00", "Z")

    _save_metrics(metrics, path)
    return snapshot, alert_message


def get_refresh_slo_snapshot(path: Path = METRICS_PATH) -> Dict[str, Any]:
    metrics = _load_metrics(path)
    snapshot = _build_snapshot(metrics)
    return {"snapshot": snapshot, "metrics": metrics}

