"""Shared auth token readers for the Lighter scalper.

Reads SCALPER_AUTH_TOKEN from .env on disk, then process environment,
then Redis fallback key.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_REDIS_TOKEN_KEY = "scalper:auth:token:v1"


def _parse_env_token_from_file(path: Path) -> str:
    try:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
    except PermissionError:
        logger.warning("Permission denied reading %s, falling back to env", path)
        return ""
    except OSError:
        return ""

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("SCALPER_AUTH_TOKEN="):
            value = line.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            return value.strip()
    return ""


def _resolve_env_path(env_file_path: Optional[Union[str, Path]] = None) -> Path:
    if env_file_path is not None:
        return Path(env_file_path)
    env_override = os.environ.get("SCALPER_ENV_FILE", "").strip()
    if env_override:
        return Path(env_override)
    return _PROJECT_ROOT / ".env"


def _extract_token_payload(raw: Optional[str]) -> Tuple[str, float]:
    if not raw:
        return "", 0.0
    try:
        payload = json.loads(raw)
    except Exception:
        return "", 0.0
    if not isinstance(payload, dict):
        return "", 0.0
    token = str(payload.get("token") or "").strip()
    issued_at = float(payload.get("issued_at_epoch") or 0.0)
    if not token:
        return "", 0.0
    return token, issued_at


def read_token_from_env_file(env_file_path: Optional[Union[str, Path]] = None) -> Tuple[str, str]:
    """Read SCALPER_AUTH_TOKEN with fallback chain: file -> env.

    Returns:
        tuple[token, source] where source in {"file", "env", "empty"}
    """
    path = _resolve_env_path(env_file_path)
    token = _parse_env_token_from_file(path)
    if token:
        return token, "file"

    env_token = os.environ.get("SCALPER_AUTH_TOKEN", "").strip()
    if env_token:
        return env_token, "env"

    return "", "empty"


async def read_token_with_redis_fallback(
    redis_client: Any,
    env_file_path: Optional[Union[str, Path]] = None,
    *,
    current_token: str = "",
) -> Tuple[str, str]:
    """Read auth token with fallback chain: file -> env -> Redis.

    Redis payload format:
      {"token": str, "issued_at_epoch": float, ...}

    If current_token is newer/equivalent and non-empty, keep current_token.
    """
    current = str(current_token or "").strip()

    ttl_override = os.environ.get("SCALPER_AUTH_TOKEN_TTL_SEC", "").strip()
    ttl_sec = 0
    try:
        ttl_sec = int(ttl_override) if ttl_override else 0
    except ValueError:
        ttl_sec = 0

    # Primary source is .env/env because ExecStartPre updates .env atomically.
    # This prevents stale redis payloads from overriding a freshly refreshed token.
    token, source = read_token_from_env_file(env_file_path)
    if token:
        return token, source

    now = time.time()
    if redis_client is not None:
        try:
            raw = await redis_client.get(_REDIS_TOKEN_KEY)
            redis_token, issued_at = _extract_token_payload(raw)
            if redis_token:
                # Legacy/manual payloads without issuance timestamp are not trusted
                # over runtime/file/env because they can pin an expired token.
                if issued_at <= 0:
                    logger.warning("Ignoring redis auth token without issued_at metadata")
                    redis_token = ""
                elif ttl_sec > 0 and (now - issued_at) > float(ttl_sec):
                    logger.warning(
                        "Ignoring stale redis auth token age=%.1fs ttl=%ss",
                        now - issued_at,
                        ttl_sec,
                    )
                    redis_token = ""
            if redis_token:
                # If current exists and token string matches, keep current source semantics.
                if current and current == redis_token:
                    return current, "runtime"
                # Prefer fresh redis token.
                return redis_token, "redis"
        except Exception as e:
            logger.debug("Redis auth token read failed: %s", e)

    return current, "runtime" if current else ("", "empty")
