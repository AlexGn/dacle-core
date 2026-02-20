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

    @app_commands.command(name="sync", description="Total Command Purge & Fresh Re-sync (Fixes all /command issues)")
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
            
            # 1. Purge GLOBAL commands (the most aggressive fix)
            self.bot.tree.clear_commands(guild=None)
            await self.bot.tree.sync(guild=None)
            
            # 2. Clear this specific server's commands
            if guild:
                self.bot.tree.clear_commands(guild=guild)
                await self.bot.tree.sync(guild=guild)
                
                # 3. Re-register everything to the server
                self.bot.tree.copy_global_to(guild=guild)
                synced = await asyncio.wait_for(self.bot.tree.sync(guild=guild), timeout=60)
                
                await interaction.edit_original_response(
                    content=f"🚀 **TOTAL PURGE COMPLETE!** {len(synced)} fresh commands registered.\n\n**NEXT STEP**: Restart your Discord App (Ctrl+R) and `/audit` should be visible."
                )
            else:
                await interaction.edit_original_response(content="❌ Could not find server for sync.")
                
        except Exception as e:
            logger.error(f"Global Purge failed: {e}", exc_info=True)
            await interaction.edit_original_response(content=f"❌ Sync failed: {e}")


    async def cog_app_command_error(self, interaction, error):
        logger.error(f"[SyncCommands] {error}", exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(SyncCommands(bot))
