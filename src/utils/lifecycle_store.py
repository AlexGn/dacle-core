"""
Lifecycle Store — Session 443

Persistence layer for trade lifecycle data. Maps lifecycle_id → stage timestamps,
execution snapshots, and exit reasons.

Storage: data/state/lifecycle_store.json
Auto-cleanup: entries older than 90 days are pruned on each write.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from src.utils.atomic_write import atomic_json_write

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LIFECYCLE_STORE_PATH = PROJECT_ROOT / "data" / "state" / "lifecycle_store.json"

# Auto-prune entries older than this
MAX_AGE_DAYS = 90


def _load_store() -> Dict[str, Any]:
    """Load the lifecycle store from disk."""
    if LIFECYCLE_STORE_PATH.exists():
        try:
            return json.loads(LIFECYCLE_STORE_PATH.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load lifecycle store: {e}")
    return {}


def _save_store(store: Dict[str, Any]) -> None:
    """Save the lifecycle store to disk with auto-cleanup."""
    _prune_old_entries(store)
    LIFECYCLE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(LIFECYCLE_STORE_PATH, store)


def _prune_old_entries(store: Dict[str, Any]) -> None:
    """Remove entries older than MAX_AGE_DAYS."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).isoformat()
    keys_to_remove = []
    for key, entry in store.items():
        created = entry.get("setup_time") or entry.get("created_at", "")
        if created and created < cutoff:
            keys_to_remove.append(key)
    for key in keys_to_remove:
        del store[key]
    if keys_to_remove:
        logger.info(f"Pruned {len(keys_to_remove)} old lifecycle entries")


def get_entry(lifecycle_id: str) -> Optional[Dict[str, Any]]:
    """Get a lifecycle entry by ID.

    Args:
        lifecycle_id: The lifecycle ID to look up.

    Returns:
        Entry dict or None.
    """
    store = _load_store()
    return store.get(lifecycle_id)


def upsert_entry(lifecycle_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update a lifecycle entry.

    Args:
        lifecycle_id: The lifecycle ID.
        updates: Dict of fields to set/update.

    Returns:
        The updated entry.
    """
    store = _load_store()
    entry = store.get(lifecycle_id, {"created_at": datetime.now(timezone.utc).isoformat()})
    entry.update(updates)
    store[lifecycle_id] = entry
    _save_store(store)
    return entry


def record_setup(
    lifecycle_id: str,
    token: str,
    direction: str,
    thread_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Record the setup stage of a lifecycle.

    Args:
        lifecycle_id: The lifecycle ID.
        token: Token symbol.
        direction: Trade direction.
        thread_id: Discord thread ID (if available).
        message_id: Discord message ID (if available).

    Returns:
        The updated entry.
    """
    return upsert_entry(lifecycle_id, {
        "token": token.upper(),
        "direction": direction.upper(),
        "setup_time": datetime.now(timezone.utc).isoformat(),
        "thread_id": thread_id,
        "message_id": message_id,
    })


def record_ptc(lifecycle_id: str, approved: bool) -> Dict[str, Any]:
    """Record that pre-trade-check was run for this lifecycle.

    Args:
        lifecycle_id: The lifecycle ID.
        approved: Whether PTC approved the trade.

    Returns:
        The updated entry.
    """
    return upsert_entry(lifecycle_id, {
        "ptc_time": datetime.now(timezone.utc).isoformat(),
        "ptc_approved": approved,
    })


def record_entry(
    lifecycle_id: str,
    entry_price: Optional[float] = None,
) -> Dict[str, Any]:
    """Record that a position was opened.

    Args:
        lifecycle_id: The lifecycle ID.
        entry_price: Price at which the position was opened.

    Returns:
        The updated entry.
    """
    return upsert_entry(lifecycle_id, {
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "entry_price": entry_price,
    })


def record_close(
    lifecycle_id: str,
    exit_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Record that a position was closed.

    Args:
        lifecycle_id: The lifecycle ID.
        exit_reason: Why the position was closed (TP_HIT, SL_HIT, MANUAL, etc.).

    Returns:
        The updated entry.
    """
    return upsert_entry(lifecycle_id, {
        "close_time": datetime.now(timezone.utc).isoformat(),
        "exit_reason": exit_reason,
    })


def record_snapshot(lifecycle_id: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Record execution snapshot for a lifecycle.

    Args:
        lifecycle_id: The lifecycle ID.
        snapshot: Execution context snapshot dict.

    Returns:
        The updated entry.
    """
    return upsert_entry(lifecycle_id, {
        "execution_snapshot": snapshot,
    })


def find_lifecycle_for_position(
    token: str,
    direction: str,
    max_age_hours: float = 24.0,
) -> Optional[str]:
    """Find the most recent lifecycle_id matching a token+direction.

    Used when a new position is detected on exchange to link it back
    to the setup that created it.

    Args:
        token: Token symbol.
        direction: Trade direction.
        max_age_hours: Maximum age of lifecycle entry to consider.

    Returns:
        lifecycle_id string or None.
    """
    store = _load_store()
    token_upper = token.upper()
    direction_upper = direction.upper()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    ).isoformat()

    best_id = None
    best_time = ""

    for lid, entry in store.items():
        if (
            entry.get("token") == token_upper
            and entry.get("direction") == direction_upper
            and not entry.get("close_time")  # Not already closed
        ):
            setup_time = entry.get("setup_time", "")
            if setup_time >= cutoff and setup_time > best_time:
                best_id = lid
                best_time = setup_time

    return best_id
