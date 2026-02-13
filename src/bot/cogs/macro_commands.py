"""Macro Discord Commands."""

import discord
from discord import app_commands
from discord.ext import commands

from api.routers.macro import get_market_direction
from src.bot.formatters.market_direction import build_market_direction_embed
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

            embed_payload = build_market_direction_embed(data, include_next_update=True)
            embed = discord.Embed.from_dict(embed_payload)

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
