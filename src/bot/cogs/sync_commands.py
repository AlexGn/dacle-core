"""
Owner-only slash command to sync guild commands.
"""

import asyncio
import os
from src.utils.logger import get_logger
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

logger = get_logger(__name__)


class SyncCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_owner_id(self) -> Optional[int]:
        owner_id = os.getenv("DISCORD_OWNER_ID")
        if not owner_id:
            return None
        try:
            return int(owner_id)
        except ValueError:
            return None

    def _is_owner(self, user_id: int) -> bool:
        owner_id = self._get_owner_id()
        return owner_id is not None and user_id == owner_id

    @app_commands.command(name="sync", description="Sync slash commands for this server")
    async def sync_commands(self, interaction: discord.Interaction):
        # Allow owner ALWAYS, or allow ANYONE in the #audit-token channel for debugging
        audit_channel_id = 1474325144913838232
        is_owner = self._is_owner(interaction.user.id)
        is_audit_channel = interaction.channel_id == audit_channel_id

        if not (is_owner or is_audit_channel):
            await interaction.response.send_message("❌ You are not authorized to run /sync outside of #audit-token.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        logger.info(
            "SYNC_COMMAND_STARTED user=%s guild=%s channel=%s",
            interaction.user.id,
            interaction.guild.id if interaction.guild else "none",
            interaction.channel_id,
        )
        try:
            guild = interaction.guild
            if guild is None:
                await interaction.edit_original_response(content="❌ Sync must be run in a server.")
                return
            self.bot.tree.copy_global_to(guild=guild)
            synced = await asyncio.wait_for(self.bot.tree.sync(guild=guild), timeout=30)
            await interaction.edit_original_response(
                content=f"✅ Synced {len(synced)} commands to this server."
            )
            logger.info(
                "SYNC_COMMAND_COMPLETED user=%s guild=%s synced=%s",
                interaction.user.id,
                guild.id,
                len(synced),
            )
        except asyncio.TimeoutError:
            logger.error("SYNC_COMMAND_TIMEOUT user=%s", interaction.user.id)
            await interaction.edit_original_response(
                content="⚠️ Sync timed out after 30s. Try again in a few seconds."
            )
        except Exception as e:
            logger.error(f"Sync failed: {e}", exc_info=True)
            await interaction.edit_original_response(content=f"❌ Sync failed: {e}")


    async def cog_app_command_error(self, interaction, error):
        logger.error(f"[SyncCommands] {error}", exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(SyncCommands(bot))
