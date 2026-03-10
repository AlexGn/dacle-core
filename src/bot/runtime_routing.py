"""Shared runtime routing helpers for Discord channel flows."""

from __future__ import annotations

import os
from typing import Any, Optional

from src.ops.discord_channel_contract import get_discord_channel_contract


def get_bot_api_base_url() -> str:
    """Resolve the canonical DACLE API base URL for bot-side callers."""
    return str(os.getenv("DACLE_API_URL", "http://localhost:8000")).rstrip("/")


def get_channel_id(key: str) -> int:
    """Resolve canonical Discord channel id from the channel contract."""
    return int(get_discord_channel_contract().id_for(key))


def resolve_channel(bot: Any, key: str) -> Optional[Any]:
    """Resolve a channel object from a Discord bot client via the canonical contract."""
    return bot.get_channel(get_channel_id(key))
