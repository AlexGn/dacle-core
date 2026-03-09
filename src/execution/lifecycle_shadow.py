"""Lifecycle shadow comparator utilities for Swing execution parity tracking."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from src.execution.v2_models import ExecutionState


class LifecycleMismatchClass(str, Enum):
    NONE = "none"
    ELEVATED = "elevated"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


EXECUTION_LIFECYCLE_SHADOW_LOG = Path("data/execution/execution_lifecycle_shadow.jsonl")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _safe_account_fragment(account_id: str) -> str:
    raw = str(account_id or "").strip() or "primary"
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)
    return cleaned or "primary"


def resolve_lifecycle_shadow_log_path(account_id: str, *, log_path: Path = EXECUTION_LIFECYCLE_SHADOW_LOG) -> Path:
    if not _env_bool("SWING_LIFECYCLE_SHADOW_ACCOUNT_SCOPED", default=False):
        return log_path
    account_fragment = _safe_account_fragment(account_id)
    return log_path.parent / "accounts" / account_fragment / log_path.name


def classify_lifecycle_mismatch(
    *,
    shadow_approved: Optional[bool],
    state: ExecutionState,
) -> LifecycleMismatchClass:
    if shadow_approved is None:
        return LifecycleMismatchClass.UNKNOWN

    execution_states = {
        ExecutionState.PROTECTION_ARMED,
        ExecutionState.SUBMITTED,
        ExecutionState.PARTIALLY_FILLED,
        ExecutionState.FILLED,
    }
    blocked_states = {
        ExecutionState.VETOED,
        ExecutionState.PROTECTION_FAILED,
        ExecutionState.FAILED,
        ExecutionState.CANCELED,
        ExecutionState.EXPIRED,
    }

    if shadow_approved is False and state in execution_states:
        return LifecycleMismatchClass.CRITICAL
    if shadow_approved is True and state in blocked_states:
        return LifecycleMismatchClass.ELEVATED
    return LifecycleMismatchClass.NONE


def append_lifecycle_shadow_event(
    *,
    idempotency_key: str,
    account_id: str,
    symbol: str,
    state: ExecutionState,
    source: str,
    shadow_approved: Optional[bool],
    details: Optional[Dict[str, Any]] = None,
    log_path: Path = EXECUTION_LIFECYCLE_SHADOW_LOG,
) -> Dict[str, Any]:
    resolved_log_path = resolve_lifecycle_shadow_log_path(account_id, log_path=log_path)
    mismatch_class = classify_lifecycle_mismatch(shadow_approved=shadow_approved, state=state)
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": str(idempotency_key),
        "account_id": str(account_id),
        "symbol": str(symbol),
        "state": str(state),
        "source": str(source),
        "shadow_approved": shadow_approved,
        "mismatch_class": mismatch_class.value,
        "details": details or {},
    }

    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
    with resolved_log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True) + "\n")
    return event
