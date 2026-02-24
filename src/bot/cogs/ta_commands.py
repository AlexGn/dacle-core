"""
TA Card Command Cog
Unified /ta command for quick technical analysis cards.
Session 440: Phase 3 of Quick TA Audit plan.
"""

import aiohttp
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.logger import get_logger
from src.bot.formatters.ta_card import build_ta_card_embed
from src.bot.cogs.ta_card_view import TACardView

logger = get_logger(__name__)


def _get_api_base_url() -> str:
    """Resolve API base URL at call time (after load_config)."""
    return os.getenv("DACLE_API_URL", "http://localhost:8000")


def _get_api_headers() -> dict:
    api_key = os.getenv("DACLE_API_KEY", "").strip()
    return {"X-API-Key": api_key} if api_key else {}


class TACommands(commands.Cog):
    """Cog for the unified /ta slash command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("TACommands cog initialized")

    @app_commands.command(
        name="ta", description="Quick technical analysis card for a token"
    )
    @app_commands.describe(
        symbol="Token symbol (e.g., ZRO, LAYER, DRIFT)",
        direction="Trade direction (optional, auto-detected if omitted)",
    )
    @app_commands.choices(
        direction=[
            app_commands.Choice(name="SHORT", value="SHORT"),
            app_commands.Choice(name="LONG", value="LONG"),
        ]
    )
    async def ta_slash(
        self,
        interaction: discord.Interaction,
        symbol: str,
        direction: Optional[app_commands.Choice[str]] = None,
    ):
        """Quick TA card for a token — shows BQS, signals, levels, macro."""
        symbol = symbol.upper().lstrip("$")
        dir_value = direction.value if direction else None

        await interaction.response.defer(ephemeral=False)

        api_base = _get_api_base_url()
        params = {}
        if dir_value:
            params["direction"] = dir_value

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{api_base}/api/ta/card/{symbol}"
                async with session.get(
                    url,
                    params=params,
                    headers=_get_api_headers(),
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        try:
                            error_data = await resp.json()
                            error_msg = error_data.get(
                                "detail", f"API error {resp.status}"
                            )
                        except Exception:
                            error_msg = f"API error {resp.status}"
                        await interaction.followup.send(
                            f"Failed to get TA for **{symbol}**: {error_msg}"
                        )
                        return
                    data = await resp.json()
        except aiohttp.ClientError as e:
            logger.error(f"TA card API call failed: {e}")
            await interaction.followup.send(
                f"Failed to reach DACLE API: {e}"
            )
            return
        except Exception as e:
            logger.error(f"TA card unexpected error: {e}", exc_info=True)
            await interaction.followup.send(f"Unexpected error: {e}")
            return

        # Build embed from card data
        embed_data = build_ta_card_embed(data)
        embed = discord.Embed(
            title=embed_data["title"],
            description=embed_data.get("description", ""),
            color=embed_data.get("color", 0x9B9B9B),
        )
        for field in embed_data.get("fields", []):
            embed.add_field(
                name=field["name"],
                value=field["value"],
                inline=field.get("inline", False),
            )
        footer = embed_data.get("footer", {})
        if footer:
            embed.set_footer(text=footer.get("text", ""))

        view = TACardView(
            symbol=symbol,
            direction=data.get("direction", "SHORT"),
            data=data,
        )
        await interaction.followup.send(embed=embed, view=view)

    async def cog_app_command_error(self, interaction, error):
        """Handle slash command errors with user-visible messages."""
        logger.error(f"[TACommands] {error}", exc_info=error)
        try:
            msg = f"An error occurred: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    """Setup function called by bot when loading this cog."""
    await bot.add_cog(TACommands(bot))
    logger.info("TACommands cog loaded")
