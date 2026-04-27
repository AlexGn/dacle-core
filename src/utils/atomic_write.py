"""
Atomic JSON write utilities — Session 427

Clean atomic write for data files (consolidated.json, etc.)
without metadata injection. For alert state files, use atomic_state.py instead.
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Union

_lock_registry: Dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def _get_path_lock(path: Path) -> threading.Lock:
    """Get or create a per-path lock for thread-safe updates."""
    key = str(path.resolve())
    with _registry_lock:
        if key not in _lock_registry:
            _lock_registry[key] = threading.Lock()
        return _lock_registry[key]


def atomic_json_write(path: Union[str, Path], data: Any) -> None:
    """Write JSON data atomically using temp file + os.replace.

    Args:
        path: Target file path.
        data: JSON-serializable data. Written as-is (no metadata added).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.stem}_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, 0o644)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_jsonl_append(path: Union[str, Path], record: Dict[str, Any]) -> None:
    """Append a JSON line to a JSONL file atomically.

    Uses per-path locking to prevent interleaved writes from concurrent threads.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    lock = _get_path_lock(path)
    with lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()


def locked_json_update(
    path: Union[str, Path],
    updater: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> Dict[str, Any]:
    """Read-modify-write a JSON file under a per-path thread lock.

    Args:
        path: Target JSON file.
        updater: Function receiving current data (or ``{}``) and returning new data.

    Returns:
        The updated data dict.
    """
    path = Path(path)
    lock = _get_path_lock(path)
    with lock:
        current: Dict[str, Any] = {}
        if path.exists():
            current = json.loads(path.read_text())
        new_data = updater(current)
        atomic_json_write(path, new_data)
        return new_data
