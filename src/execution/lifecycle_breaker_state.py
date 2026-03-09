"""Persistence helpers for execution lifecycle breaker runtime state."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

import redis

_STATE_LOCK = Lock()
DEFAULT_LIFECYCLE_BREAKER_STATE_FILE = Path("data/execution/execution_lifecycle_breaker_state.json")
DEFAULT_LIFECYCLE_BREAKER_REDIS_KEY = "exec:lifecycle_breaker_state"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except Exception:
        return int(default)
    return parsed if parsed >= 0 else int(default)


def _normalize_account_state(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    return {
        "paused": bool(payload.get("paused", False)),
        "critical_hits": _safe_int(payload.get("critical_hits"), default=0),
        "updated_at": str(payload.get("updated_at") or ""),
        "reason": str(payload.get("reason") or ""),
    }


def _normalize_state(raw: Optional[Dict[str, Any]], default_account_id: str) -> Dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    accounts_raw = payload.get("accounts")
    accounts: Dict[str, Dict[str, Any]] = {}
    if isinstance(accounts_raw, dict):
        for account_id, account_payload in accounts_raw.items():
            account_key = str(account_id or "").strip()
            if not account_key:
                continue
            accounts[account_key] = _normalize_account_state(
                account_payload if isinstance(account_payload, dict) else {}
            )

    default_key = str(default_account_id or "primary").strip() or "primary"
    legacy_state = _normalize_account_state(payload)
    if default_key not in accounts:
        accounts[default_key] = legacy_state

    default_state = accounts.get(default_key, _normalize_account_state({}))
    return {
        "paused": bool(default_state.get("paused", False)),
        "critical_hits": _safe_int(default_state.get("critical_hits"), default=0),
        "updated_at": str(default_state.get("updated_at") or ""),
        "reason": str(default_state.get("reason") or ""),
        "accounts": accounts,
        "storage": "memory",
    }


def _resolve_redis_url(override_url: Optional[str] = None) -> str:
    if override_url is not None:
        return str(override_url or "").strip()
    return str(os.getenv("SWING_EXECUTION_LIFECYCLE_BREAKER_REDIS_URL", "") or "").strip()


def _resolve_redis_key(override_key: Optional[str] = None) -> str:
    if override_key is not None:
        return str(override_key or "").strip() or DEFAULT_LIFECYCLE_BREAKER_REDIS_KEY
    raw = str(os.getenv("SWING_EXECUTION_LIFECYCLE_BREAKER_REDIS_KEY", "") or "").strip()
    return raw or DEFAULT_LIFECYCLE_BREAKER_REDIS_KEY


def lifecycle_breaker_redis_enabled(redis_url: Optional[str] = None) -> bool:
    return bool(_resolve_redis_url(redis_url))


def _get_redis_client(
    *,
    redis_url: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None,
) -> Optional[redis.Redis]:
    if redis_client is not None:
        return redis_client
    resolved = _resolve_redis_url(redis_url)
    if not resolved:
        return None
    try:
        return redis.Redis.from_url(
            resolved,
            decode_responses=True,
            socket_timeout=0.05,
            socket_connect_timeout=0.05,
        )
    except Exception:
        return None


def _load_state_from_redis(
    *,
    default_account_id: str,
    redis_url: Optional[str] = None,
    redis_key: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None,
) -> Optional[Dict[str, Any]]:
    client = _get_redis_client(redis_url=redis_url, redis_client=redis_client)
    if client is None:
        return None
    try:
        raw = client.get(_resolve_redis_key(redis_key))
    except Exception:
        return None
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    normalized = _normalize_state(parsed, default_account_id)
    normalized["storage"] = "redis"
    return normalized


def _save_state_to_redis(
    payload: Dict[str, Any],
    *,
    redis_url: Optional[str] = None,
    redis_key: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None,
) -> bool:
    client = _get_redis_client(redis_url=redis_url, redis_client=redis_client)
    if client is None:
        return False
    try:
        serialized = json.dumps(payload, ensure_ascii=True, indent=2)
        client.set(_resolve_redis_key(redis_key), serialized)
        return True
    except Exception:
        return False


def load_lifecycle_breaker_state(
    *,
    state_file: Path = DEFAULT_LIFECYCLE_BREAKER_STATE_FILE,
    default_account_id: str = "primary",
    redis_url: Optional[str] = None,
    redis_key: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None,
) -> Dict[str, Any]:
    with _STATE_LOCK:
        redis_payload = _load_state_from_redis(
            default_account_id=default_account_id,
            redis_url=redis_url,
            redis_key=redis_key,
            redis_client=redis_client,
        )
        if redis_payload is not None:
            return redis_payload

        if not state_file.exists():
            payload = _normalize_state({}, default_account_id)
            payload["storage"] = "memory"
            return payload
        try:
            raw = state_file.read_text(encoding="utf-8")
            parsed = json.loads(raw) if raw.strip() else {}
        except Exception:
            parsed = {}
        payload = _normalize_state(parsed if isinstance(parsed, dict) else {}, default_account_id)
        payload["storage"] = "file"
        return payload


def save_lifecycle_breaker_state(
    *,
    paused: bool,
    critical_hits: int,
    reason: Optional[str] = None,
    state_file: Path = DEFAULT_LIFECYCLE_BREAKER_STATE_FILE,
    account_id: str = "primary",
    default_account_id: str = "primary",
    redis_url: Optional[str] = None,
    redis_key: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None,
) -> Dict[str, Any]:
    account_key = str(account_id or default_account_id or "primary").strip() or "primary"
    default_key = str(default_account_id or "primary").strip() or "primary"
    updated_account_payload = {
        "paused": bool(paused),
        "critical_hits": _safe_int(critical_hits, default=0),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "reason": str(reason or "").strip(),
    }
    with _STATE_LOCK:
        redis_payload = _load_state_from_redis(
            default_account_id=default_key,
            redis_url=redis_url,
            redis_key=redis_key,
            redis_client=redis_client,
        )
        if redis_payload is not None:
            normalized = _normalize_state(redis_payload, default_key)
        else:
            parsed: Dict[str, Any] = {}
            if state_file.exists():
                try:
                    raw = state_file.read_text(encoding="utf-8")
                    loaded = json.loads(raw) if raw.strip() else {}
                    if isinstance(loaded, dict):
                        parsed = loaded
                except Exception:
                    parsed = {}
            normalized = _normalize_state(parsed, default_key)

        accounts = dict(normalized.get("accounts") or {})
        accounts[account_key] = dict(updated_account_payload)
        default_state = accounts.get(default_key) or _normalize_account_state({})
        payload = {
            "paused": bool(default_state.get("paused", False)),
            "critical_hits": _safe_int(default_state.get("critical_hits"), default=0),
            "updated_at": str(default_state.get("updated_at") or ""),
            "reason": str(default_state.get("reason") or ""),
            "accounts": accounts,
        }
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_file.with_suffix(f"{state_file.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        os.replace(tmp_path, state_file)
        if _save_state_to_redis(
            payload,
            redis_url=redis_url,
            redis_key=redis_key,
            redis_client=redis_client,
        ):
            payload["storage"] = "redis"
        else:
            payload["storage"] = "file"
    return payload
