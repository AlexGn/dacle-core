"""
Token identity lock persistence.

Stores canonical token identity per symbol to prevent ambiguous symbol drift
during refresh/research pipelines.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCKS_PATH = PROJECT_ROOT / "data" / "bot" / "token_identity_locks.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_all() -> Dict[str, Dict[str, Any]]:
    if not LOCKS_PATH.exists():
        return {}
    try:
        with open(LOCKS_PATH, "r") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _save_all(data: Dict[str, Dict[str, Any]]) -> None:
    LOCKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = LOCKS_PATH.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, LOCKS_PATH)


def get_identity_lock(symbol: str) -> Optional[Dict[str, Any]]:
    locks = _load_all()
    return locks.get(symbol.upper())


def set_identity_lock(
    symbol: str,
    name: str,
    source: str,
    external_id: str,
    updated_by: str = "api",
) -> Dict[str, Any]:
    sym = symbol.upper().strip()
    record = {
        "symbol": sym,
        "name": str(name).strip(),
        "source": str(source).strip(),
        "external_id": str(external_id).strip(),
        "updated_at": _utc_now(),
        "updated_by": str(updated_by).strip() or "api",
    }
    locks = _load_all()
    locks[sym] = record
    _save_all(locks)
    return record
