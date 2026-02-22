"""
Position Commands Cog
Provides /positions slash command showing live Blofin + Lighter DEX positions.
"""

import aiohttp
import os
from typing import Any, Dict, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _build_scalper_field(data: Dict[str, Any]) -> Optional[Tuple[str, str, bool]]:
    """Build embed field tuple for Lighter DEX scalper section.

    Returns (name, value, inline) or None if there is nothing to show.
    Pure function - no I/O.
    """
    is_running = data.get("is_running", False)
    mode = data.get("mode", "SHADOW")
    exposure = data.get("current_exposure_usd", 0)
    fills_24h = data.get("fill_count_24h", 0)

    if not is_running and exposure == 0 and fills_24h == 0:
        return None

    icon = "\U0001f7e2" if is_running else "\u26aa"  # green circle vs white circle
    name = "{} Lighter DEX ({})".format(icon, mode)
    value = "Exposure: ${:.2f}\nFills (24h): {}".format(exposure, fills_24h)
    return (name, value, True)


class PositionCommands(commands.Cog):
    """Live position tracking via /positions slash command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_url = os.getenv("DACLE_API_URL", "http://localhost:8000")
        self.api_key = os.getenv("DACLE_API_KEY", "").strip()
        logger.info("PositionCommands cog initialized")

    def _build_api_headers(self) -> dict:
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    @app_commands.command(name="positions", description="Show live Blofin positions with P&L")
    async def positions(self, interaction: discord.Interaction):
        """Display current open positions from Blofin."""
        await interaction.response.defer(ephemeral=False)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/api/blofin/positions",
                    headers=self._build_api_headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        await interaction.followup.send(
                            f"Failed to fetch positions (HTTP {resp.status}). Is the API running?"
                        )
                        return
                    data = await resp.json()
        except Exception as e:
            logger.error(f"/positions API call failed: {e}")
            await interaction.followup.send(f"Failed to connect to DACLE API: {e}")
            return

        positions = data.get("positions", [])

        if not positions:
            embed = discord.Embed(
                title="Positions",
                description="No open positions (0/3 slots)",
                color=discord.Color.greyple(),
            )
            # Append Lighter DEX scalper section even when no Blofin positions
            await self._append_scalper_field(embed)
            await interaction.followup.send(embed=embed)
            return

        # Build embed
        embed = discord.Embed(
            title=f"Positions ({len(positions)}/3 slots)",
            color=discord.Color.blue(),
        )

        for p in positions:
            token = p.get("token") or p.get("symbol", "?")
            side = p.get("side", "?").upper()
            entry = p.get("entry_price", 0)
            current = p.get("current_price", 0)
            pnl_pct = p.get("unrealized_pnl_pct", 0)
            pnl_usd = p.get("unrealized_pnl", 0)
            size_usd = p.get("size_usd", 0)
            leverage = p.get("leverage", "?")

            # Health
            if pnl_pct <= -10:
                health = "DANGER"
                emoji = "\u26a0\ufe0f"
            elif pnl_pct <= -5:
                health = "At Risk"
                emoji = "\U0001f534"
            elif pnl_pct >= 10:
                health = "Near TP"
                emoji = "\U0001f3af"
            elif pnl_pct >= 0:
                health = "Healthy"
                emoji = "\U0001f7e2"
            else:
                health = "Monitoring"
                emoji = "\U0001f7e1"

            pnl_sign = "+" if pnl_usd >= 0 else ""
            field_value = (
                f"{emoji} **{side}** | {leverage}x\n"
                f"Entry: `${entry}` | Now: `${current}`\n"
                f"P&L: `{pnl_sign}${pnl_usd:.2f}` (`{pnl_pct:+.1f}%`) | Size: `${size_usd:.0f}`\n"
                f"Status: **{health}**"
            )

            embed.add_field(name=f"${token}", value=field_value, inline=False)

        # Append Lighter DEX scalper section
        await self._append_scalper_field(embed)

        # Total P&L
        total_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)
        total_sign = "+" if total_pnl >= 0 else ""
        embed.set_footer(text=f"Total Unrealized P&L: {total_sign}${total_pnl:.2f}")

        await interaction.followup.send(embed=embed)

    async def _append_scalper_field(self, embed: discord.Embed) -> None:
        """Fetch scalper status and append a Lighter DEX field to the embed."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/api/scalping/status",
                    headers=self._build_api_headers(),
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    if resp.status == 200:
                        scalper_data = await resp.json()
                        field = _build_scalper_field(scalper_data)
                        if field:
                            name, value, inline = field
                            embed.add_field(name=name, value=value, inline=inline)
        except Exception:
            pass  # Non-fatal -- Blofin positions still display

    async def cog_app_command_error(self, interaction, error):
        logger.error(f"[PositionCommands] {error}", exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(PositionCommands(bot))
    logger.info("PositionCommands cog loaded")
