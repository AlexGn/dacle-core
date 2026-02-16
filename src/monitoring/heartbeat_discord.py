"""
HEARTBEAT Monitor — Discord Posting Layer

T1.2: Thin I/O layer. Posts messages to Discord via direct HTTP API.
Pattern from scripts/monitors/market_direction_monitor.py:201-226.
"""

import os
from datetime import datetime, timezone

import httpx

from src.ops.discord_channel_contract import get_discord_channel_contract
from src.monitoring.channel_telemetry import write_channel_telemetry_event
from src.utils.logger import get_logger

logger = get_logger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"

CHANNEL_IDS = get_discord_channel_contract().ids


async def post_to_discord(channel_name: str, message: str) -> bool:
    """
    Post a plain-text message to a Discord channel.

    Args:
        channel_name: Key from CHANNEL_IDS ("macro-updates", "trades", "focus")
        message: Message text to post

    Returns:
        True if posted successfully, False otherwise.
    """
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN not set — cannot post to Discord")
        return False

    channel_id = CHANNEL_IDS.get(channel_name)
    if not channel_id:
        logger.error(f"Unknown Discord channel: {channel_name}")
        return False

    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    payload = {"content": message}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                logger.info(f"Posted HEARTBEAT alert to #{channel_name}")
                write_channel_telemetry_event(
                    telemetry_path=None,
                    timestamp_iso=datetime.now(timezone.utc).isoformat(),
                    channel=channel_name,
                    message=message,
                    source="heartbeat_discord",
                    posted=True,
                )
                return True
            else:
                logger.error(
                    f"Discord API error {resp.status_code}: {resp.text[:300]}"
                )
                write_channel_telemetry_event(
                    telemetry_path=None,
                    timestamp_iso=datetime.now(timezone.utc).isoformat(),
                    channel=channel_name,
                    message=message,
                    source="heartbeat_discord",
                    posted=False,
                )
                return False
    except Exception as e:
        logger.error(f"Discord post failed: {e}")
        write_channel_telemetry_event(
            telemetry_path=None,
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            channel=channel_name,
            message=message,
            source="heartbeat_discord",
            posted=False,
        )
        return False
