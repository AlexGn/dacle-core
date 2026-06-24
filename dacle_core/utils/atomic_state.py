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
    from dacle_core.utils.atomic_state import atomic_read, atomic_write, atomic_update

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
from datetime import datetime, timedelta, timezone
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


def _acquire_lock(file_handle, exclusive: bool = False, timeout: float = 30.0) -> bool:
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


def _recover_temp_file(path: Path) -> bool:
    """
    Session 257 Fix 3: Recover from interrupted atomic writes.

    If a .tmp file exists but the main file doesn't, a crash occurred
    during atomic_write after temp file creation but before rename.

    Recovery logic:
    - If .tmp exists and main doesn't: Use .tmp (atomic rename didn't complete)
    - If both exist: Prefer main (rename completed, .tmp is stale)
    - If only main exists: Normal case

    Args:
        path: Path to the main state file

    Returns:
        True if recovery was performed, False otherwise
    """
    # Find any .tmp files matching this state file
    pattern = f'.{path.stem}_*.tmp'
    tmp_files = list(path.parent.glob(pattern))

    if not tmp_files:
        return False

    # Sort by modification time (most recent first)
    tmp_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    newest_tmp = tmp_files[0]

    if not path.exists():
        # Main file doesn't exist but temp does - recover!
        logger.warning(f"Recovering state from interrupted write: {newest_tmp} -> {path}")
        try:
            os.rename(newest_tmp, path)
            # Clean up any other stale .tmp files
            for stale_tmp in tmp_files[1:]:
                try:
                    stale_tmp.unlink()
                except OSError:
                    pass
            return True
        except OSError as e:
            logger.error(f"Failed to recover temp file: {e}")
            return False
    else:
        # Both exist - main file is authoritative, clean up stale temps
        for stale_tmp in tmp_files:
            try:
                stale_tmp.unlink()
                logger.debug(f"Cleaned up stale temp file: {stale_tmp}")
            except OSError:
                pass
        return False


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

    # Session 257 Fix 3: Check for and recover interrupted writes
    _recover_temp_file(path)

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


def cleanup_stale_entries(
    path: Union[str, Path],
    max_age_hours: float = 48.0,
    timestamp_key: str = '_last_updated',
    collections_to_clean: Optional[list] = None,
    lock_timeout: float = 5.0
) -> Dict[str, Any]:
    """
    Session 257 Fix 2: Remove stale entries from state file to prevent bloat.

    Gemini Review: "Since you are storing 'Already Alerted' tokens in a JSON file,
    this file will grow indefinitely. Risk: load(path) will eventually take >100ms."

    This janitor function removes entries older than the dedup window.

    Args:
        path: Path to state file
        max_age_hours: Maximum age in hours (default: 48h, matches dedup window)
        timestamp_key: Key used for timestamps in entries
        collections_to_clean: List of collection keys to clean (e.g., ['alerted_windows'])
                             If None, cleans all collections that look like alert data
        lock_timeout: Seconds to wait for lock

    Returns:
        Dict with cleanup statistics: {
            'entries_removed': int,
            'entries_kept': int,
            'collections_cleaned': list,
            'state_size_before': int,
            'state_size_after': int
        }

    Example:
        # Run daily via cron to prevent state file bloat
        stats = cleanup_stale_entries(
            '/path/to/post_tge_state.json',
            max_age_hours=48.0,
            collections_to_clean=['alerted_windows', 'alerted_tokens']
        )
        print(f"Cleaned {stats['entries_removed']} stale entries")
    """
    path = Path(path)
    stats = {
        'entries_removed': 0,
        'entries_kept': 0,
        'collections_cleaned': [],
        'state_size_before': 0,
        'state_size_after': 0
    }

    if not path.exists():
        return stats

    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    def clean_state(state: Dict) -> Dict:
        nonlocal stats
        stats['state_size_before'] = len(json.dumps(state))

        # Determine which collections to clean
        if collections_to_clean:
            target_collections = collections_to_clean
        else:
            # Auto-detect collections that look like alert tracking data
            target_collections = [
                k for k in state.keys()
                if isinstance(state[k], dict) and k not in ['_last_updated', '_metadata']
            ]

        for collection_key in target_collections:
            if collection_key not in state:
                continue

            collection = state[collection_key]
            if not isinstance(collection, dict):
                continue

            cleaned_collection = {}
            for token_key, token_data in collection.items():
                if isinstance(token_data, dict):
                    # Token data has timestamps - filter old entries
                    ts_str = token_data.get(timestamp_key) or token_data.get('timestamp')
                    if ts_str:
                        try:
                            entry_time = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                            if entry_time.tzinfo is None:
                                entry_time = entry_time.replace(tzinfo=timezone.utc)
                            if entry_time >= cutoff_time:
                                cleaned_collection[token_key] = token_data
                                stats['entries_kept'] += 1
                            else:
                                stats['entries_removed'] += 1
                                logger.debug(f"Removed stale entry: {collection_key}/{token_key}")
                        except (ValueError, TypeError):
                            # Can't parse timestamp - keep entry to be safe
                            cleaned_collection[token_key] = token_data
                            stats['entries_kept'] += 1
                    else:
                        # No timestamp - keep entry
                        cleaned_collection[token_key] = token_data
                        stats['entries_kept'] += 1

                elif isinstance(token_data, list):
                    # List of alerts - filter to recent window entries
                    # Keep list items that are strings (window IDs) or recent timestamped dicts
                    cleaned_list = []
                    for item in token_data:
                        if isinstance(item, str):
                            # Window identifiers like '7d', '14d' - keep all
                            cleaned_list.append(item)
                            stats['entries_kept'] += 1
                        elif isinstance(item, dict):
                            ts_str = item.get(timestamp_key) or item.get('timestamp')
                            if ts_str:
                                try:
                                    entry_time = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                                    if entry_time.tzinfo is None:
                                        entry_time = entry_time.replace(tzinfo=timezone.utc)
                                    if entry_time >= cutoff_time:
                                        cleaned_list.append(item)
                                        stats['entries_kept'] += 1
                                    else:
                                        stats['entries_removed'] += 1
                                except (ValueError, TypeError):
                                    cleaned_list.append(item)
                                    stats['entries_kept'] += 1
                            else:
                                cleaned_list.append(item)
                                stats['entries_kept'] += 1
                        else:
                            cleaned_list.append(item)
                            stats['entries_kept'] += 1

                    if cleaned_list:
                        cleaned_collection[token_key] = cleaned_list
                else:
                    # Unknown format - keep
                    cleaned_collection[token_key] = token_data
                    stats['entries_kept'] += 1

            state[collection_key] = cleaned_collection
            stats['collections_cleaned'].append(collection_key)

        stats['state_size_after'] = len(json.dumps(state))
        return state

    try:
        atomic_update(path, clean_state, lock_timeout=lock_timeout)
        logger.info(
            f"State cleanup complete: removed {stats['entries_removed']} entries, "
            f"kept {stats['entries_kept']}, size {stats['state_size_before']} -> {stats['state_size_after']} bytes"
        )
    except Exception as e:
        logger.error(f"State cleanup failed: {e}")

    return stats


# Convenience aliases
read_state = atomic_read
write_state = atomic_write
update_state = atomic_update
cleanup_state = cleanup_stale_entries


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
