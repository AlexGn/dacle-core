"""
Macro Discord Commands

Slash commands for macro/market direction updates.
"""

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from api.routers.macro import get_market_direction
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MacroCommands(commands.Cog):
    """Cog for macro direction slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="market",
        description="Get current market direction bias (BULLISH / NEUTRAL / BEARISH)",
    )
    async def market(self, interaction: discord.Interaction):
        """Fetch and display current market direction."""
        await interaction.response.defer(ephemeral=False)

        try:
            data = await get_market_direction()
            if data.get("status") != "ok":
                error = data.get("error", "Unknown error")
                await interaction.followup.send(
                    f"❌ Failed to fetch market direction: {error}",
                    ephemeral=True,
                )
                return

            bias = data.get("bias", "UNKNOWN")
            score = data.get("score", 0)
            confidence = data.get("confidence_pct", 0)
            ts = data.get("timestamp")
            position = data.get("position_implications", {})
            signals = data.get("signals", [])
            context_signals = data.get("context_signals", [])
            key_levels = data.get("key_levels", {})

            color = discord.Color.light_grey()
            if bias == "BULLISH":
                color = discord.Color.green()
            elif bias == "BEARISH":
                color = discord.Color.red()
            elif bias == "NEUTRAL":
                color = discord.Color.gold()

            embed = discord.Embed(
                title=f"Market Direction: {bias}",
                description=f"Composite score: **{score:+.3f}** | Confidence: **{confidence}%**",
                color=color,
                timestamp=datetime.now(),
            )

            recommendation = position.get("recommendation", "N/A")
            short_sizing = position.get("short_sizing", "N/A")
            long_sizing = position.get("long_sizing", "N/A")
            embed.add_field(
                name="Positioning",
                value=(
                    f"Recommendation: **{recommendation}**\n"
                    f"SHORT sizing: **{short_sizing}**\n"
                    f"LONG sizing: **{long_sizing}**"
                ),
                inline=False,
            )

            if signals:
                signal_lines = []
                for s in signals:
                    emoji = s.get("emoji", "")
                    name = s.get("name", "Signal")
                    label = s.get("label", "")
                    weight_pct = s.get("weight_pct", 0)
                    signal_lines.append(f"{emoji} **{name}** ({weight_pct}%): {label}")
                embed.add_field(
                    name="Signals",
                    value="\n".join(signal_lines)[:1024],
                    inline=False,
                )

            if context_signals:
                context_lines = []
                for s in context_signals:
                    emoji = s.get("emoji", "")
                    name = s.get("name", "Context")
                    label = s.get("label", "")
                    context_lines.append(f"{emoji} **{name}**: {label}")
                embed.add_field(
                    name="Context",
                    value="\n".join(context_lines)[:1024],
                    inline=False,
                )

            btc_levels = key_levels.get("btc", []) if isinstance(key_levels, dict) else []
            btcdom_levels = key_levels.get("btcdom", []) if isinstance(key_levels, dict) else []
            total3_levels = key_levels.get("total3", []) if isinstance(key_levels, dict) else []
            level_lines = []
            for group_name, group in [("BTC", btc_levels), ("BTCDOM", btcdom_levels), ("TOTAL3", total3_levels)]:
                for lvl in group[:4]:
                    level = lvl.get("level")
                    distance = lvl.get("distance_pct")
                    name = lvl.get("name", "Level")
                    alert = "⚠️ " if lvl.get("alert") else ""
                    if distance is None:
                        line = f"{alert}**{group_name}** {name}: {level}"
                    else:
                        line = f"{alert}**{group_name}** {name}: {level} ({distance:+.1f}%)"
                    level_lines.append(line)
            if level_lines:
                embed.add_field(
                    name="Key Levels",
                    value="\n".join(level_lines)[:1024],
                    inline=False,
                )

            if ts:
                embed.set_footer(text=f"Data timestamp: {ts}")

            await interaction.followup.send(embed=embed)
            logger.info(f"✅ {interaction.user.name} requested /market")

        except Exception as e:
            logger.error(f"❌ Error in /market command: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error getting market direction: {e}",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    """Setup function for Discord extension loading."""
    await bot.add_cog(MacroCommands(bot))
