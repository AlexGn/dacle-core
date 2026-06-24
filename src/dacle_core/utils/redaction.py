#!/usr/bin/env python3
"""
Shared redaction helpers for logs, traces, and outbound LLM payloads.
"""

from __future__ import annotations

import re
from typing import Any, Dict

REDACTED = "[REDACTED]"

_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|authorization|auth|bearer|cookie|session|webhook|private[_-]?key|mnemonic|seed)",
    re.IGNORECASE,
)

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9._-]+"),
        "https://discord.com/api/webhooks/[REDACTED]/[REDACTED]",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), REDACTED),
    (re.compile(r"\bpplx-[A-Za-z0-9]{20,}\b"), REDACTED),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{20,}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), REDACTED),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}\b"), "Bearer [REDACTED]"),
]

_PRIVATE_IP_RE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b"
)
_LOCAL_PATH_RE = re.compile(
    r"(?<!http:)(?<!https:)//?((?:Users|home|root|var|tmp|etc|opt|srv|mnt)/[^\s\"'<>]+)"
)
_QUERY_SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)=([^&\s]+)")


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(key)))


def redact_string(value: str, *, max_length: int | None = 4000) -> str:
    redacted = value
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    redacted = _PRIVATE_IP_RE.sub("[REDACTED_IP]", redacted)
    redacted = _LOCAL_PATH_RE.sub("[REDACTED_PATH]", redacted)
    redacted = _QUERY_SECRET_RE.sub(r"\1=[REDACTED]", redacted)
    if max_length and len(redacted) > max_length:
        return redacted[:max_length] + "...[TRUNCATED]"
    return redacted


def redact_value(value: Any, *, key: str | None = None, _depth: int = 0, max_depth: int = 8) -> Any:
    if _depth > max_depth:
        return "[TRUNCATED_DEPTH]"

    if key and is_sensitive_key(key):
        return REDACTED

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return redact_string(value)

    if isinstance(value, bytes):
        return REDACTED

    if isinstance(value, dict):
        redacted_dict: Dict[str, Any] = {}
        for k, v in value.items():
            key_str = str(k)
            redacted_dict[key_str] = redact_value(v, key=key_str, _depth=_depth + 1, max_depth=max_depth)
        return redacted_dict

    if isinstance(value, (list, tuple, set, frozenset)):
        redacted_items = [redact_value(v, _depth=_depth + 1, max_depth=max_depth) for v in value]
        if isinstance(value, tuple):
            return tuple(redacted_items)
        if isinstance(value, (set, frozenset)):
            return list(redacted_items)
        return redacted_items

    try:
        return redact_string(str(value))
    except Exception:
        return REDACTED
