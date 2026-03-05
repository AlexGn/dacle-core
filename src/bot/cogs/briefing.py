"""
Scan Cog — Quick scan: positions + top setups + alerts

Provides:
- /scan command - Quick scan: positions + top setups + alerts
"""

from src.utils.logger import get_logger
from datetime import datetime
from typing import Any, Dict, List
import os

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.utils.interaction_response import safe_defer, safe_send

logger = get_logger(__name__)


def _api_headers() -> dict[str, str]:
    api_key = os.getenv("DACLE_API_KEY", "").strip()
    return {"X-API-Key": api_key} if api_key else {}


def format_scan_output(
    positions: List[Dict[str, Any]],
    tokens: List[Dict[str, Any]],
    alerts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Format compact scan output as a Discord embed dict.

    Shows: positions + top 3 tokens by score + pending alerts.
    Returns dict suitable for discord.Embed.from_dict().
    """
    fields = []

    # --- Positions section ---
    if positions:
        lines = []
        has_danger = False
        for pos in positions:
            token = pos.get("token", "???")
            side = pos.get("side", "?")
            pnl_pct = pos.get("unrealized_pnl_pct", 0)
            pnl_str = f"{pnl_pct:+.1f}%"
            if pnl_pct <= -10:
                has_danger = True
                pnl_str += " DANGER"
            lines.append(f"{token} {side} {pnl_str}")
        fields.append({"name": "Positions", "value": "\n".join(lines), "inline": False})
    else:
        has_danger = False
        fields.append({"name": "Positions", "value": "No open positions", "inline": False})

    # --- Top setups (top 3 by score) ---
    sorted_tokens = sorted(tokens, key=lambda t: t.get("score", 0), reverse=True)[:3]
    if sorted_tokens:
        lines = []
        for t in sorted_tokens:
            sym = t.get("symbol", "?")
            score = t.get("score", 0)
            direction = t.get("direction", "?")
            readiness = t.get("readiness", "?")
            lines.append(f"{sym} {score:.1f} {direction} [{readiness}]")
        fields.append({
            "name": f"Top Setups ({len(sorted_tokens)}/3)",
            "value": "\n".join(lines),
            "inline": False,
        })
    else:
        fields.append({"name": "Top Setups (0/3)", "value": "No setups available", "inline": False})

    # --- Alerts ---
    stale_count = sum(1 for a in alerts if a.get("type") == "STALE")
    critical_count = sum(1 for a in alerts if a.get("type") == "CRITICAL")
    alert_parts = []
    if stale_count:
        alert_parts.append(f"{stale_count} stale")
    if critical_count:
        alert_parts.append(f"{critical_count} critical")
    if alert_parts:
        fields.append({"name": "Alerts", "value": ", ".join(alert_parts), "inline": False})

    # --- Color ---
    if has_danger:
        color = 0xFF0000  # red
    elif positions:
        color = 0x00FF00  # green
    else:
        color = 0x3498DB  # blue

    return {
        "title": "Quick Scan",
        "description": f"{len(positions)} positions | {len(sorted_tokens)} setups | {len(alerts)} alerts",
        "color": color,
        "fields": fields,
        "footer": {"text": f"Scanned at {datetime.utcnow().strftime('%H:%M UTC')}"},
    }


class ScanCog(commands.Cog):
    """Quick scan command for positions, setups, and alerts."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("ScanCog initialized")

    @app_commands.command(name="scan", description="Quick scan: positions + top setups + alerts")
    async def scan_command(self, interaction: discord.Interaction):
        """Quick scan showing positions, top setups, and alerts."""
        await safe_defer(
            interaction,
            thinking=True,
            command_name="scan",
            logger=logger,
        )

        try:
            import sys
            from pathlib import Path
            import httpx

            # Fetch positions from DACLE API
            api_url = os.getenv("DACLE_API_URL", "http://localhost:8000")
            positions = []
            try:
                async with httpx.AsyncClient(timeout=10, headers=_api_headers()) as client:
                    resp = await client.get(f"{api_url}/api/blofin/positions")
                    if resp.status_code == 200:
                        body = resp.json()
                        positions = body if isinstance(body, list) else body.get("positions", [])
            except Exception as e:
                logger.warning(f"Failed to fetch positions for /scan: {e}")

            # Import data fetchers from daily brief script
            scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts" / "scheduled")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from daily_trading_brief import fetch_trade_setups, fetch_staleness_alerts

            tokens = fetch_trade_setups()
            alerts = fetch_staleness_alerts()

            embed_dict = format_scan_output(positions, tokens, alerts)
            await safe_send(
                interaction,
                command_name="scan",
                logger=logger,
                embed=discord.Embed.from_dict(embed_dict),
            )

        except Exception as e:
            logger.error(f"Error in /scan: {e}")
            await safe_send(
                interaction,
                command_name="scan",
                logger=logger,
                content="Error running scan. Check logs for details.",
                ephemeral=True,
            )

    async def cog_app_command_error(self, interaction, error):
        logger.error(f"[ScanCog] {error}", exc_info=error)


async def setup(bot: commands.Bot):
    """Setup function called by Discord.py when loading cog."""
    await bot.add_cog(ScanCog(bot))
    logger.info("ScanCog loaded successfully")
