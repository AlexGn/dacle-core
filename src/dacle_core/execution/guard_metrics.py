import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterator, Optional

import redis

_METRICS_LOCK = Lock()
_REDIS_CLIENT_LOCK = Lock()
_REDIS_CLIENT: Optional[redis.Redis] = None
_REDIS_CLIENT_URL: str = ""

_DEFAULT_METRICS_FILE = Path("data/execution/execution_guard_metrics.json")
_COUNTERS_KEY = "exec:metrics:counters"
_REASONS_KEY_PREFIX = "exec:metrics:reasons:"
_REDIS_TIMEOUT_SEC = 0.02

try:
    import fcntl  # POSIX only
except Exception:  # pragma: no cover - non-POSIX path
    fcntl = None


def _resolve_redis_url(override_url: Optional[str] = None) -> str:
    if override_url is not None:
        return str(override_url or "").strip()
    explicit = str(os.getenv("EXECUTION_GUARD_METRICS_REDIS_URL", "") or "").strip()
    if explicit:
        return explicit
    return str(os.getenv("EXECUTION_CONTEXT_NONCE_REDIS_URL", "") or "").strip()


def _get_redis_client(redis_url: Optional[str] = None) -> Optional[redis.Redis]:
    global _REDIS_CLIENT, _REDIS_CLIENT_URL

    resolved_url = _resolve_redis_url(redis_url)
    if not resolved_url:
        return None

    with _REDIS_CLIENT_LOCK:
        if _REDIS_CLIENT is not None and _REDIS_CLIENT_URL == resolved_url:
            return _REDIS_CLIENT
        _REDIS_CLIENT = redis.Redis.from_url(
            resolved_url,
            decode_responses=True,
            socket_timeout=_REDIS_TIMEOUT_SEC,
            socket_connect_timeout=_REDIS_TIMEOUT_SEC,
        )
        _REDIS_CLIENT_URL = resolved_url
        return _REDIS_CLIENT


@contextmanager
def _locked_metrics_file(path: Path, exclusive: bool) -> Iterator[Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    try:
        if fcntl is not None:
            lock_mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(fh.fileno(), lock_mode)
        fh.seek(0)
        yield fh
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _load_metrics_file_locked(path: Path) -> Dict[str, Any]:
    with _locked_metrics_file(path, exclusive=False) as fh:
        raw = fh.read()
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except Exception:
            return {}
    return payload if isinstance(payload, dict) else {}


def _increment_file_fallback(
    counter_name: str,
    reason: Optional[str],
    amount: int,
    path: Path,
    fallback_reason: Optional[str] = None,
) -> None:
    with _locked_metrics_file(path, exclusive=True) as fh:
        raw = fh.read()
        if raw.strip():
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}
        else:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        runtime = payload.setdefault("runtime_counters", {})
        runtime[counter_name] = _safe_int(runtime.get(counter_name, 0)) + int(amount)

        if reason:
            by_reason = payload.setdefault("runtime_counter_reasons", {})
            reason_map = by_reason.setdefault(counter_name, {})
            reason_map[str(reason)] = _safe_int(reason_map.get(str(reason), 0)) + int(amount)

        source_meta = payload.setdefault("source_metadata", {})
        source_meta["last_write_source"] = "file_fallback"
        source_meta["file_fallback_used"] = True
        source_meta["redis_configured"] = bool(_resolve_redis_url(None))
        if fallback_reason:
            source_meta["redis_unavailable"] = True
            source_meta["redis_error"] = str(fallback_reason)

        payload["runtime_updated_at"] = datetime.now(timezone.utc).isoformat()
        fh.seek(0)
        fh.truncate()
        json.dump(payload, fh, indent=2)
        fh.flush()


def _redis_hincr(
    counter_name: str,
    reason: Optional[str],
    amount: int,
    redis_url: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None,
) -> tuple[bool, Optional[str]]:
    client = redis_client or _get_redis_client(redis_url=redis_url)
    if client is None:
        return False, "REDIS_NOT_CONFIGURED"
    try:
        client.hincrby(_COUNTERS_KEY, counter_name, int(amount))
        if reason:
            client.hincrby(f"{_REASONS_KEY_PREFIX}{counter_name}", str(reason), int(amount))
        return True, None
    except redis.TimeoutError:
        return False, "REDIS_TIMEOUT"
    except redis.ConnectionError:
        return False, "REDIS_CONNECTION_ERROR"
    except redis.RedisError as e:
        return False, f"REDIS_ERROR:{e.__class__.__name__}"
    except Exception as e:  # pragma: no cover - defensive
        return False, f"REDIS_UNKNOWN:{e.__class__.__name__}"


def increment_guard_counter(
    counter_name: str,
    reason: Optional[str] = None,
    amount: int = 1,
    metrics_file: Optional[Path] = None,
    redis_url: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None,
) -> None:
    path = metrics_file or _DEFAULT_METRICS_FILE
    with _METRICS_LOCK:
        ok, redis_err = _redis_hincr(
            counter_name=counter_name,
            reason=reason,
            amount=amount,
            redis_url=redis_url,
            redis_client=redis_client,
        )
        if ok:
            return
        _increment_file_fallback(
            counter_name=counter_name,
            reason=reason,
            amount=amount,
            path=path,
            fallback_reason=redis_err if redis_err != "REDIS_NOT_CONFIGURED" else None,
        )


def _load_redis_metrics(
    redis_url: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None,
) -> tuple[Dict[str, int], Dict[str, Dict[str, int]], Dict[str, Any]]:
    counters: Dict[str, int] = {}
    reasons: Dict[str, Dict[str, int]] = {}
    source_meta: Dict[str, Any] = {
        "redis_configured": bool(_resolve_redis_url(redis_url)),
        "redis_connected": False,
    }

    client = redis_client or _get_redis_client(redis_url=redis_url)
    if client is None:
        return counters, reasons, source_meta

    try:
        raw_counters = client.hgetall(_COUNTERS_KEY) or {}
        for k, v in raw_counters.items():
            counters[str(k)] = _safe_int(v)

        for key in client.scan_iter(match=f"{_REASONS_KEY_PREFIX}*"):
            key_text = key.decode("utf-8") if isinstance(key, bytes) else str(key)
            counter_name = key_text[len(_REASONS_KEY_PREFIX):]
            raw_reason_map = client.hgetall(key_text) or {}
            reason_map: Dict[str, int] = {}
            for reason, val in raw_reason_map.items():
                reason_key = reason.decode("utf-8") if isinstance(reason, bytes) else str(reason)
                reason_map[reason_key] = _safe_int(val)
            if reason_map:
                reasons[counter_name] = reason_map
        source_meta["redis_connected"] = True
    except redis.TimeoutError:
        source_meta["redis_error"] = "REDIS_TIMEOUT"
    except redis.ConnectionError:
        source_meta["redis_error"] = "REDIS_CONNECTION_ERROR"
    except redis.RedisError as e:
        source_meta["redis_error"] = f"REDIS_ERROR:{e.__class__.__name__}"
    except Exception as e:  # pragma: no cover - defensive
        source_meta["redis_error"] = f"REDIS_UNKNOWN:{e.__class__.__name__}"

    return counters, reasons, source_meta


def get_guard_metrics(
    as_json: bool = True,  # kept for interface compatibility
    metrics_file: Optional[Path] = None,
    redis_url: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None,
) -> Dict[str, Any]:
    _ = as_json  # output is dict for API usage
    path = metrics_file or _DEFAULT_METRICS_FILE

    with _METRICS_LOCK:
        file_payload = _load_metrics_file_locked(path) if path.exists() else {}
        file_counters = {
            str(k): _safe_int(v)
            for k, v in (file_payload.get("runtime_counters") or {}).items()
        }
        file_reasons_raw = file_payload.get("runtime_counter_reasons") or {}
        file_reasons: Dict[str, Dict[str, int]] = {}
        for counter_name, reason_map in file_reasons_raw.items():
            if not isinstance(reason_map, dict):
                continue
            file_reasons[str(counter_name)] = {
                str(reason): _safe_int(val) for reason, val in reason_map.items()
            }

        redis_counters, redis_reasons, redis_meta = _load_redis_metrics(
            redis_url=redis_url,
            redis_client=redis_client,
        )

        merged_counters = dict(file_counters)
        for key, val in redis_counters.items():
            merged_counters[key] = _safe_int(merged_counters.get(key, 0)) + _safe_int(val)

        merged_reasons = dict(file_reasons)
        for counter_name, reason_map in redis_reasons.items():
            target = merged_reasons.setdefault(counter_name, {})
            for reason, val in reason_map.items():
                target[reason] = _safe_int(target.get(reason, 0)) + _safe_int(val)

        sources = []
        if redis_meta.get("redis_connected"):
            sources.append("redis")
        if file_counters or file_reasons or path.exists():
            sources.append("file")
        if not sources:
            sources.append("none")

        source_meta = dict(file_payload.get("source_metadata") or {})
        source_meta.update(redis_meta)
        source_meta["sources"] = sources
        source_meta["file_path"] = str(path)
        source_meta["file_present"] = bool(path.exists())
        source_meta["last_read_at"] = datetime.now(timezone.utc).isoformat()

        return {
            "runtime_counters": merged_counters,
            "runtime_counter_reasons": merged_reasons,
            "runtime_updated_at": file_payload.get("runtime_updated_at"),
            "source_metadata": source_meta,
        }
