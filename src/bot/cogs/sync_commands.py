"""
Owner-only slash command to sync guild commands.
"""

import asyncio
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.ops.discord_channel_contract import get_discord_channel_contract
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SyncCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.audit_channel_id = 1474325144913838232
        self.discovery_channel_id = self._resolve_discovery_channel_id()

    @staticmethod
    def _resolve_discovery_channel_id() -> Optional[int]:
        try:
            return int(get_discord_channel_contract().id_for("discovery"))
        except Exception:
            return None

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

    @app_commands.command(name="sync", description="Force a Hard Sync of all commands (fixes invisible commands)")
    async def sync_commands(self, interaction: discord.Interaction):
        # Allow owner ALWAYS, or allow in #audit-token / #discovery
        is_owner = self._is_owner(interaction.user.id)
        is_audit_channel = interaction.channel_id == self.audit_channel_id
        is_discovery_channel = (
            self.discovery_channel_id is not None
            and interaction.channel_id == self.discovery_channel_id
        )

        if not (is_owner or is_audit_channel or is_discovery_channel):
            await interaction.response.send_message(
                "❌ You are not authorized to run /sync outside of #audit-token or #discovery.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            guild = interaction.guild
            if guild is None:
                await interaction.edit_original_response(content="❌ Sync must be run in a server.")
                return
            
            # 1. Clear the tree for this guild to force a full refresh
            self.bot.tree.clear_commands(guild=guild)
            
            # 2. Copy the new global commands to this guild
            self.bot.tree.copy_global_to(guild=guild)
            
            # 3. Sync with a generous timeout
            synced = await asyncio.wait_for(self.bot.tree.sync(guild=guild), timeout=60)
            
            await interaction.edit_original_response(
                content=f"✅ **Hard Sync Complete!** {len(synced)} commands registered.\n\n**IMPORTANT**: Please restart your Discord client (Ctrl+R) to see `/audit-team`."
            )
            logger.info(f"HARD SYNC COMPLETED: {len(synced)} commands for guild {guild.id}")
        except asyncio.TimeoutError:
            await interaction.edit_original_response(content="⚠️ Sync timed out. Discord servers are busy. Try again in 1 minute.")
        except Exception as e:
            logger.error(f"Hard Sync failed: {e}", exc_info=True)
            await interaction.edit_original_response(content=f"❌ Sync failed: {e}")


    async def cog_app_command_error(self, interaction, error):
        logger.error(f"[SyncCommands] {error}", exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(SyncCommands(bot))
