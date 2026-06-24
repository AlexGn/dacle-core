"""
Trade Lifecycle ID — Session 443

Generates and parses unique lifecycle IDs that propagate through the entire
trade lifecycle: setup → pre-trade-check → position open → position close → trade_log.

Format: LC_{TOKEN}_{DIRECTION}_{YYYYMMDD}_{HHMMSS}_{HASH6}
Example: LC_ZRO_SHORT_20260221_143052_a1b2c3
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

_LIFECYCLE_PATTERN = re.compile(
    r"^LC_([A-Z0-9]+)_(SHORT|LONG)_(\d{8})_(\d{6})_([a-f0-9]{6})$"
)

# Pattern to extract lifecycle_id from Discord message HTML comments
LIFECYCLE_COMMENT_PATTERN = re.compile(r"<!--\s*lifecycle:(LC_[A-Za-z0-9_]+)\s*-->")


def generate_lifecycle_id(token: str, direction: str, now: Optional[datetime] = None) -> str:
    """Generate a unique lifecycle ID for a trade setup.

    Args:
        token: Token symbol (e.g., "ZRO").
        direction: Trade direction ("SHORT" or "LONG").
        now: Optional datetime for deterministic testing. Defaults to UTC now.

    Returns:
        Lifecycle ID string, e.g. "LC_ZRO_SHORT_20260221_143052_a1b2c3".
    """
    if now is None:
        now = datetime.now(timezone.utc)

    token_upper = token.upper()
    direction_upper = direction.upper()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")

    # Hash includes microseconds for uniqueness when called rapidly
    hash_input = f"{token_upper}:{direction_upper}:{now.isoformat()}"
    hash_suffix = hashlib.sha256(hash_input.encode()).hexdigest()[:6]

    return f"LC_{token_upper}_{direction_upper}_{date_str}_{time_str}_{hash_suffix}"


def parse_lifecycle_id(lifecycle_id: str) -> Optional[dict]:
    """Parse a lifecycle ID into its components.

    Args:
        lifecycle_id: Lifecycle ID string to parse.

    Returns:
        Dict with {token, direction, date, time, hash} or None if invalid.
    """
    match = _LIFECYCLE_PATTERN.match(lifecycle_id)
    if not match:
        return None

    date_str = match.group(3)
    time_str = match.group(4)

    try:
        timestamp = datetime.strptime(
            f"{date_str}_{time_str}", "%Y%m%d_%H%M%S"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    return {
        "token": match.group(1),
        "direction": match.group(2),
        "date": date_str,
        "time": time_str,
        "hash": match.group(5),
        "timestamp": timestamp,
    }


def extract_lifecycle_id_from_message(content: str) -> Optional[str]:
    """Extract lifecycle_id from a Discord message containing <!-- lifecycle:... -->.

    Args:
        content: Message text content.

    Returns:
        Lifecycle ID string or None.
    """
    match = LIFECYCLE_COMMENT_PATTERN.search(content)
    return match.group(1) if match else None


def embed_lifecycle_id(message: str, lifecycle_id: str) -> str:
    """Append a lifecycle_id HTML comment to a message.

    Args:
        message: Original message text.
        lifecycle_id: Lifecycle ID to embed.

    Returns:
        Message with lifecycle comment appended.
    """
    return f"{message}\n<!-- lifecycle:{lifecycle_id} -->"
