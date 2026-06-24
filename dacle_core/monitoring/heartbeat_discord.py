"""
Thin Discord posting layer used by pillar daemons.

Avoids non-core dependencies (notification routing, channel telemetry). Pillar
repos can override channel resolution by setting DISCORD_CHANNEL_<NAME> env
vars, e.g. DISCORD_CHANNEL_TRADES, DISCORD_CHANNEL_MACRO.
"""

import os
from typing import Optional

import httpx

from dacle_core.utils.logging_setup import get_logger

logger = get_logger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"


def _resolve_channel_id(channel_name: str) -> Optional[str]:
    """Resolve a Discord channel ID for a logical channel name."""
    normalized = channel_name.upper().replace("-", "_").replace(" ", "_")
    env_id = os.getenv(f"DISCORD_CHANNEL_{normalized}")
    if env_id:
        return env_id.strip()
    # Fallbacks for known DACLE channels
    fallbacks = {
        "macro-updates": os.getenv("DISCORD_MACRO_CHANNEL_ID"),
        "trades": os.getenv("DISCORD_TRADES_CHANNEL_ID"),
        "focus": os.getenv("DISCORD_FOCUS_CHANNEL_ID"),
        "owner": os.getenv("DISCORD_OWNER_ID"),
    }
    return (fallbacks.get(channel_name.lower()) or "").strip() or None


async def post_to_discord(channel_name: str, message: str) -> bool:
    """Post a plain-text message to a Discord channel."""
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN not set — cannot post to Discord")
        return False

    channel_id = _resolve_channel_id(channel_name)
    if not channel_id:
        logger.error(f"Unknown Discord channel: {channel_name}")
        return False

    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}"}
    payload = {"content": str(message)[:2000]}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code in (200, 201):
            return True
        logger.error(f"Discord post failed: {response.status_code} {response.text}")
        return False
    except Exception as exc:
        logger.error(f"Discord post exception: {exc}")
        return False


def post_to_discord_sync(channel_name: str, message: str) -> bool:
    """Synchronous variant for non-async callers."""
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN not set — cannot post to Discord")
        return False

    channel_id = _resolve_channel_id(channel_name)
    if not channel_id:
        logger.error(f"Unknown Discord channel: {channel_name}")
        return False

    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}"}
    payload = {"content": str(message)[:2000]}

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(url, headers=headers, json=payload)
        if response.status_code in (200, 201):
            return True
        logger.error(f"Discord post failed: {response.status_code} {response.text}")
        return False
    except Exception as exc:
        logger.error(f"Discord post exception: {exc}")
        return False
