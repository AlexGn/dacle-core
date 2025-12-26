"""
Atomic State Management for DACLE Alert System

Session 257: Provides atomic read/write operations for JSON state files
to prevent race conditions that cause duplicate alerts.

Key Features:
- File locking (fcntl on Unix) for concurrent access safety
- Atomic writes via temp file + rename pattern
- Read-modify-write transactions with exclusive locks
- Graceful fallback when locking unavailable

Usage:
    from src.utils.atomic_state import atomic_read, atomic_write, atomic_update

    # Read state
    state = atomic_read('/path/to/state.json')

    # Write state atomically
    atomic_write('/path/to/state.json', new_state)

    # Update state with transaction
    def update_fn(state):
        state['counter'] += 1
        return state
    atomic_update('/path/to/state.json', update_fn)
"""

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union
import logging

logger = logging.getLogger(__name__)

# Try to import fcntl for file locking (Unix only)
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False
    logger.warning("fcntl not available - file locking disabled (Windows?)")


class AtomicStateError(Exception):
    """Base exception for atomic state operations."""
    pass


class LockTimeoutError(AtomicStateError):
    """Raised when file lock acquisition times out."""
    pass


class StateCorruptionError(AtomicStateError):
    """Raised when state file is corrupted or invalid."""
    pass


def _acquire_lock(file_handle, exclusive: bool = False, timeout: float = 5.0) -> bool:
    """
    Acquire a file lock with timeout.

    Args:
        file_handle: Open file handle
        exclusive: True for write lock, False for read lock
        timeout: Maximum seconds to wait for lock

    Returns:
        True if lock acquired, False if locking unavailable
    """
    if not HAS_FCNTL:
        return False

    lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    start_time = time.time()

    while True:
        try:
            fcntl.flock(file_handle.fileno(), lock_type | fcntl.LOCK_NB)
            return True
        except (IOError, OSError):
            if time.time() - start_time > timeout:
                raise LockTimeoutError(
                    f"Failed to acquire {'exclusive' if exclusive else 'shared'} lock "
                    f"after {timeout}s"
                )
            time.sleep(0.01)  # 10ms retry interval


def _release_lock(file_handle) -> None:
    """Release file lock if locking is available."""
    if HAS_FCNTL:
        try:
            fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
        except (IOError, OSError):
            pass  # Ignore unlock errors


def atomic_read(
    path: Union[str, Path],
    default: Optional[Dict] = None,
    lock_timeout: float = 5.0
) -> Dict[str, Any]:
    """
    Read JSON state file with shared lock.

    Args:
        path: Path to JSON file
        default: Default value if file doesn't exist (default: empty dict)
        lock_timeout: Seconds to wait for lock

    Returns:
        Parsed JSON data as dict

    Raises:
        StateCorruptionError: If JSON is invalid
        LockTimeoutError: If lock acquisition times out
    """
    path = Path(path)

    if not path.exists():
        return default if default is not None else {}

    try:
        with open(path, 'r') as f:
            _acquire_lock(f, exclusive=False, timeout=lock_timeout)
            try:
                content = f.read()
                if not content.strip():
                    return default if default is not None else {}
                return json.loads(content)
            finally:
                _release_lock(f)
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted state file {path}: {e}")
        raise StateCorruptionError(f"Invalid JSON in {path}: {e}")
    except LockTimeoutError:
        raise
    except Exception as e:
        logger.error(f"Failed to read state file {path}: {e}")
        return default if default is not None else {}


def atomic_write(
    path: Union[str, Path],
    data: Dict[str, Any],
    lock_timeout: float = 5.0
) -> bool:
    """
    Write JSON state file atomically using temp file + rename.

    This ensures:
    1. File is never partially written (atomic rename)
    2. Original file preserved if write fails
    3. No race conditions between read and write

    Args:
        path: Path to JSON file
        data: Dict to serialize as JSON
        lock_timeout: Seconds to wait for lock

    Returns:
        True if write succeeded

    Raises:
        LockTimeoutError: If lock acquisition times out
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Add metadata
    data_with_meta = data.copy()
    if '_last_updated' not in data_with_meta:
        data_with_meta['_last_updated'] = datetime.now(timezone.utc).isoformat()

    # Write to temp file first
    temp_fd, temp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f'.{path.stem}_',
        suffix='.tmp'
    )

    try:
        with os.fdopen(temp_fd, 'w') as f:
            json.dump(data_with_meta, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())  # Ensure data hits disk

        # Atomic rename (POSIX guarantees atomicity)
        os.rename(temp_path, path)
        logger.debug(f"Atomically wrote state to {path}")
        return True

    except Exception as e:
        logger.error(f"Failed to write state to {path}: {e}")
        # Clean up temp file on failure
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def atomic_update(
    path: Union[str, Path],
    update_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    default: Optional[Dict] = None,
    lock_timeout: float = 5.0,
    max_retries: int = 3
) -> Dict[str, Any]:
    """
    Read-modify-write transaction with exclusive lock.

    This is the safest way to update state when multiple processes
    might access the same file. The entire read-modify-write cycle
    is protected by an exclusive lock.

    Args:
        path: Path to JSON file
        update_fn: Function that takes current state and returns new state
        default: Default value if file doesn't exist
        lock_timeout: Seconds to wait for lock
        max_retries: Number of retries on transient failures

    Returns:
        The new state after update

    Raises:
        LockTimeoutError: If lock acquisition times out
        AtomicStateError: If update fails after retries

    Example:
        def mark_alerted(state):
            state.setdefault('alerted_tokens', []).append('VOOI')
            return state

        new_state = atomic_update('/path/to/state.json', mark_alerted)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        try:
            # If file doesn't exist, create it with default
            if not path.exists():
                initial_state = default if default is not None else {}
                atomic_write(path, initial_state)

            # Open for read+write to hold lock during entire transaction
            with open(path, 'r+') as f:
                _acquire_lock(f, exclusive=True, timeout=lock_timeout)
                try:
                    # Read current state
                    content = f.read()
                    current_state = json.loads(content) if content.strip() else {}

                    # Apply update function
                    new_state = update_fn(current_state)

                    # Write back atomically (still holding lock)
                    # Note: We write to temp and rename even with lock held
                    # for extra safety against crashes
                    atomic_write(path, new_state, lock_timeout=0)

                    return new_state
                finally:
                    _release_lock(f)

        except LockTimeoutError:
            if attempt < max_retries - 1:
                logger.warning(f"Lock timeout on {path}, retry {attempt + 1}/{max_retries}")
                time.sleep(0.1 * (attempt + 1))  # Exponential backoff
            else:
                raise

        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Update failed on {path}: {e}, retry {attempt + 1}/{max_retries}")
                time.sleep(0.1 * (attempt + 1))
            else:
                raise AtomicStateError(f"Failed to update {path} after {max_retries} attempts: {e}")

    raise AtomicStateError(f"Update failed after {max_retries} retries")


def atomic_check_and_mark(
    path: Union[str, Path],
    key: str,
    value: Any,
    collection_key: str = None,
    lock_timeout: float = 5.0
) -> bool:
    """
    Atomically check if a value exists and mark it if not.

    This is a common pattern for deduplication:
    1. Check if token/window already alerted
    2. If not, mark as alerted
    3. Return whether it was already alerted

    Args:
        path: Path to state file
        key: Key to check/set (e.g., 'VOOI')
        value: Value to set (e.g., '7d' for window or timestamp)
        collection_key: Optional key for nested collection (e.g., 'alerted_windows')
        lock_timeout: Seconds to wait for lock

    Returns:
        True if value was ALREADY present (should skip alert)
        False if value was NOT present (added it, should send alert)

    Example:
        # Check if VOOI 7d window was already alerted
        already_alerted = atomic_check_and_mark(
            '/path/to/post_tge_state.json',
            key='VOOI',
            value='7d',
            collection_key='alerted_windows'
        )
        if already_alerted:
            print("Skip - already sent this alert")
        else:
            print("Sending alert (and marked as sent)")
    """
    def check_and_mark_fn(state: Dict) -> Dict:
        if collection_key:
            collection = state.setdefault(collection_key, {})
            token_alerts = collection.setdefault(key, [])

            if value in token_alerts:
                # Mark that we found it (for return value)
                state['_was_present'] = True
            else:
                token_alerts.append(value)
                state['_was_present'] = False
        else:
            if key in state and state[key] == value:
                state['_was_present'] = True
            else:
                state[key] = value
                state['_was_present'] = False

        return state

    new_state = atomic_update(path, check_and_mark_fn, lock_timeout=lock_timeout)
    was_present = new_state.pop('_was_present', False)
    return was_present


# Convenience aliases
read_state = atomic_read
write_state = atomic_write
update_state = atomic_update


# Module-level state for tracking operations (debugging)
_operation_count = {
    'reads': 0,
    'writes': 0,
    'updates': 0,
    'lock_waits': 0
}


def get_operation_stats() -> Dict[str, int]:
    """Get statistics on atomic operations performed."""
    return _operation_count.copy()


def reset_operation_stats() -> None:
    """Reset operation statistics."""
    global _operation_count
    _operation_count = {k: 0 for k in _operation_count}
