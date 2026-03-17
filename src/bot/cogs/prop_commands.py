import asyncio
import logging
import os
import json
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.ops.discord_channel_contract import get_discord_channel_contract

logger = logging.getLogger(__name__)

class PropCommands(commands.Cog):
    """Prop firm commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.prop_firm_channel_id = self._resolve_prop_channel_id()

    def _resolve_prop_channel_id(self) -> Optional[int]:
        cid = os.getenv("DISCORD_PROP_FIRM_CHANNEL_ID")
        if cid and cid.isdigit():
            return int(cid)
        return None

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        # Allow owner always
        owner_id = os.getenv("DISCORD_OWNER_ID")
        is_owner = str(interaction.user.id) == owner_id
        if is_owner:
            return True
            
        # Or allow in #prop-firm
        if self.prop_firm_channel_id is not None and interaction.channel_id == self.prop_firm_channel_id:
            return True
            
        return False

    @app_commands.command(
        name="show",
        description="Run the Top 50 Dacle Cipher Scanner for Prop Firm setups"
    )
    async def show_command(self, interaction: discord.Interaction):
        """Run the Top 50 scanner on demand."""
        if not self._is_authorized(interaction):
            await interaction.response.send_message(
                "❌ You are not authorized to run `/show` outside of the prop-firm channel.",
                ephemeral=True
            )
            return

        try:
            await interaction.response.defer(thinking=True)
            
            logger.info("Executing top_50_dacle_scanner.py from /show command")
            
            # Execute the scanner
            process = await asyncio.create_subprocess_exec(
                "python3", "scripts/scanners/top_50_dacle_scanner.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                await interaction.followup.send("✅ **/show completed** - Results sent to webhook channel.")
            else:
                err_msg = stderr.decode('utf-8')[-500:]
                await interaction.followup.send(f"❌ **/show failed**\n```\n{err_msg}\n```")
                
        except Exception as e:
            logger.error(f"/show failed: {e}")
            try:
                await interaction.followup.send(f"❌ Error: {str(e)}")
            except Exception:
                pass

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PropCommands(bot))
