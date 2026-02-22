"""
Scalper Commands Cog
Provides /scalper slash command showing Lighter DEX scalper status and PnL.
"""

import aiohttp
import os

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.logger import get_logger

logger = get_logger(__name__)


class ScalperCommands(commands.Cog):
    """Lighter DEX scalper status via /scalper slash command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_url = os.getenv("DACLE_API_URL", "http://localhost:8000")
        self.api_key = os.getenv("DACLE_API_KEY", "").strip()
        logger.info("ScalperCommands cog initialized")

    def _build_api_headers(self) -> dict:
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    @app_commands.command(name="scalper", description="Show Lighter DEX scalper status and PnL")
    async def scalper_slash(self, interaction: discord.Interaction):
        """Display scalper status, PnL, last fill, and watchdog health."""
        await interaction.response.defer()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/api/scalping/status",
                    params={"detailed": "true"},
                    headers=self._build_api_headers(),
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        await interaction.followup.send(
                            f"Failed to fetch scalper status (HTTP {resp.status})."
                        )
                        return
                    data = await resp.json()

            embed = self._build_embed(data)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"/scalper command error: {e}")
            await interaction.followup.send(f"Error fetching scalper status: {e}")

    def _build_embed(self, data: dict) -> discord.Embed:
        """Build Discord embed from scalper status data. Pure function for testability."""
        is_running = data.get("is_running", False)
        mode = data.get("mode", "UNKNOWN")

        color = discord.Color.green() if is_running else discord.Color.red()
        status_icon = "\U0001f7e2" if is_running else "\U0001f534"

        embed = discord.Embed(
            title=f"{status_icon} Lighter Scalper \u2014 {mode}",
            color=color,
        )

        # Status section
        circuit = "OPEN \u26a0\ufe0f" if data.get("circuit_breaker_open") else "CLOSED"
        kill_active = bool(data.get("kill_active"))
        kill_reason = str(data.get("kill_reason") or "")
        stale = bool(data.get("stale"))
        token_ttl = data.get("token_ttl_sec")
        ttl_str = f"{int(token_ttl)}s" if token_ttl is not None else "N/A"
        status_line = f"Running: {is_running}"
        if not is_running:
            status_line = "Running: False"
        elif kill_active:
            status_line = f"HALTED: {kill_reason or 'No reason'}"
        elif stale:
            status_line = "UNSAFE: permission/sync stale"
        embed.add_field(
            name="Status",
            value=f"{status_line}\nCircuit: {circuit}\nToken TTL: {ttl_str}",
            inline=True,
        )

        # Fills section
        fill_count = data.get("fill_count_24h", 0)
        last_fill = data.get("last_fill")
        if isinstance(last_fill, dict):
            last_str = str(last_fill.get("utc_iso_timestamp", "N/A"))[:19]
        elif isinstance(last_fill, str):
            last_str = last_fill[:19]
        else:
            last_str = "None"
        embed.add_field(
            name="Fills (24h)",
            value=f"Count: {fill_count}\nLast: {last_str}",
            inline=True,
        )

        # Ghost sweeper
        ghost_err = data.get("ghost_last_error")
        ghost_str = ghost_err if ghost_err else "OK"
        embed.add_field(name="GhostSweeper", value=ghost_str, inline=True)

        # Permission
        perm = data.get("permission", {})
        if perm:
            allow = []
            if perm.get("allow_long"):
                allow.append("LONG")
            if perm.get("allow_short"):
                allow.append("SHORT")
            allow_str = ", ".join(allow) if allow else "NONE"
            max_notional = perm.get("max_notional_usd", 0)
            embed.add_field(
                name="Permission",
                value=f"Allowed: {allow_str}\nMax: ${max_notional:.0f}/trade",
                inline=True,
            )

        return embed

    async def cog_app_command_error(self, interaction, error):
        logger.error(f"[ScalperCommands] {error}", exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(ScalperCommands(bot))
    logger.info("ScalperCommands cog loaded")
