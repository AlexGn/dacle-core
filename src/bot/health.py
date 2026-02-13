"""
Bot health check — Session 427

Pure function for checking Discord bot connectivity state.
"""

from typing import Any, Dict, List


# Thresholds
MAX_LATENCY_SECONDS = 30.0


def bot_health_check(bot: Any) -> Dict[str, Any]:
    """Check bot Discord connectivity health.

    Args:
        bot: A discord.py Bot instance (or mock with matching interface).

    Returns:
        Dict with healthy (bool), latency_ms, guilds, degradation_reasons.
    """
    reasons: List[str] = []

    if not bot.is_ready():
        reasons.append("not_ready")

    if bot.ws is None:
        reasons.append("no_websocket")

    latency = bot.latency
    if latency >= MAX_LATENCY_SECONDS:
        reasons.append("high_latency")

    guild_count = len(bot.guilds)
    if guild_count == 0:
        reasons.append("no_guilds")

    return {
        "healthy": len(reasons) == 0,
        "latency_ms": round(latency * 1000, 1),
        "guilds": guild_count,
        "degradation_reasons": reasons,
    }
