"""
Analysis Command Cog
Handles the "analyze" command natively in Python bot for proper thread support.
Session 396: Replaces OpenClaw "analyze" command which lacked thread awareness.
"""

import asyncio
import logging
import discord
from discord.ext import commands
from typing import Optional

from src.orchestration.trade_workflow import full_pipeline
from src.bot.cogs.analysis_formatter import AnalysisFormatter
from src.bot.cogs.analysis_views import TradeApprovalView
from api.routers.macro import get_btc_regime_widget

logger = logging.getLogger(__name__)


class AnalysisCommands(commands.Cog):
    """
    Cog for on-demand analysis commands
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("AnalysisCommands cog initialized")

    @commands.command(name="analyze")
    async def analyze(self, ctx: commands.Context, symbol: str):
        """
        Analyze a token and generate a playbook.
        Usage: @Dacle Bot analyze <SYMBOL>
        """
        # Check if we are in a text channel (not a thread/DM)
        if isinstance(ctx.channel, discord.TextChannel):
            try:
                # Create a thread for this analysis
                thread = await ctx.message.create_thread(
                    name=f"Analysis: {symbol.upper()}",
                    auto_archive_duration=1440 # 24 hours
                )
                # Reply INSIDE the new thread
                status_msg = await thread.send(f"🔍 Analyzing **{symbol.upper()}**... (this may take 10-20s)")
                
                # Update context to point to the thread for subsequent replies
                ctx.channel = thread
            except Exception as e:
                logger.warning(f"Failed to create thread: {e}")
                # Fallback to main channel reply
                status_msg = await ctx.reply(f"🔍 Analyzing **{symbol.upper()}**... (this may take 10-20s)", mention_author=False)
        else:
            # Already in a thread or DM, just reply
            status_msg = await ctx.reply(f"🔍 Analyzing **{symbol.upper()}**... (this may take 10-20s)", mention_author=False)

        # Run analysis in background task
        asyncio.create_task(self._run_analysis_task(ctx, status_msg, symbol))

    async def _run_analysis_task(self, ctx: commands.Context, status_msg: discord.Message, symbol: str):
        """Background task for analysis"""
        try:
            logger.info(f"🚀 Starting on-demand analysis for {symbol} requested by {ctx.author}")
            
            # Run the full pipeline
            # Note: We run in executor to avoid blocking the bot's event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                lambda: full_pipeline(
                    symbol=symbol, 
                    force_refresh=True,  # Always refresh like OpenClaw did
                    force_playbook=True, # Always generate playbook
                    notify_discord=False # We handle notification manually
                )
            )
            
            if result.has_error:
                await status_msg.edit(content=f"❌ Analysis failed: {result.error_message}")
                return

            # Fetch macro data for context (optional)
            macro = None
            try:
                macro = await get_btc_regime_widget()
            except Exception as e:
                logger.warning(f"Failed to fetch macro data: {e}")

            # Format the rich embed
            embed = AnalysisFormatter.format_candidate_embed(result, macro)
            view = TradeApprovalView(symbol, result.conviction_score)

            # Reply IN THE SAME THREAD (ctx.reply handles this automatically)
            # We delete the status message and send the full report
            await status_msg.delete()
            await ctx.reply(embed=embed, view=view, mention_author=False)
            
            logger.info(f"✅ Sent analysis report for {symbol} to #{ctx.channel.name}")

        except Exception as e:
            logger.error(f"Error in analyze command: {e}", exc_info=True)
            await status_msg.edit(content=f"❌ An error occurred: {str(e)}")


async def setup(bot: commands.Bot):
    """Setup function called by bot when loading this cog"""
    await bot.add_cog(AnalysisCommands(bot))
    logger.info("AnalysisCommands cog loaded")
