"""Macro Discord Commands."""

import time

import discord
from discord import app_commands
from discord.ext import commands

from api.routers.macro import get_market_direction
from src.bot.formatters.market_direction import build_market_direction_embed
from src.bot.utils.interaction_response import safe_defer, safe_send
from src.utils.logger import get_logger

logger = get_logger(__name__)


class MacroCommands(commands.Cog):
    """Cog for macro direction slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active_market_requests: dict[int, float] = {}

    def _prune_market_requests(self) -> None:
        now = time.monotonic()
        expired = [user_id for user_id, expires_at in self._active_market_requests.items() if expires_at <= now]
        for user_id in expired:
            self._active_market_requests.pop(user_id, None)

    @app_commands.command(
        name="macro",
        description="Get current market direction bias (BULLISH / NEUTRAL / BEARISH)",
    )
    async def macro(self, interaction: discord.Interaction):
        """Fetch and display current market direction."""
        deferred = await safe_defer(
            interaction,
            ephemeral=False,
            thinking=True,
            command_name="macro",
            logger=logger,
        )

        user_id = getattr(getattr(interaction, "user", None), "id", None)
        self._prune_market_requests()
        if user_id is not None and user_id in self._active_market_requests:
            await safe_send(
                interaction,
                command_name="macro",
                logger=logger,
                content="⏳ `/macro` is already running for you. Please wait a few seconds.",
                ephemeral=True,
            )
            return
        if user_id is not None:
            self._active_market_requests[user_id] = time.monotonic() + 30

        if not deferred:
            logger.warning(
                "Aborting /macro after defer failure user_id=%s interaction_id=%s",
                user_id,
                getattr(interaction, "id", None),
            )
            self._active_market_requests.pop(user_id, None)
            return

        try:
            data = await get_market_direction()
            if data.get("status") != "ok":
                error = data.get("error", "Unknown error")
                await safe_send(
                    interaction,
                    command_name="macro",
                    logger=logger,
                    content=(
                    f"❌ Failed to fetch market direction: {error}",
                    ),
                    ephemeral=True,
                )
                return

            embed_payload = build_market_direction_embed(data, include_next_update=True)
            embed = discord.Embed.from_dict(embed_payload)

            await safe_send(
                interaction,
                command_name="macro",
                logger=logger,
                embed=embed,
            )
            logger.info(f"✅ {interaction.user.name} requested /macro")

        except Exception as e:
            logger.error(f"❌ Error in /macro command: {e}", exc_info=True)
            await safe_send(
                interaction,
                command_name="macro",
                logger=logger,
                content=f"❌ Error getting market direction: {e}",
                ephemeral=True,
            )
        finally:
            if user_id is not None:
                self._active_market_requests.pop(user_id, None)


    async def cog_app_command_error(self, interaction, error):
        logger.error(f"[MacroCommands] {error}", exc_info=error)


async def setup(bot: commands.Bot):
    """Setup function for Discord extension loading."""
    await bot.add_cog(MacroCommands(bot))
