"""
Analysis Command Cog
Handles the "analyze" command natively in Python bot for proper thread support.
Session 396: Replaces OpenClaw "analyze" command which lacked thread awareness.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Tuple, Dict, Any

import discord
import requests
from discord.ext import commands

from src.orchestration.trade_workflow import full_pipeline
from src.bot.cogs.analysis_formatter import AnalysisFormatter
from src.bot.cogs.analysis_views import TradeApprovalView
from api.routers.macro import get_btc_regime_widget

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TOKENS_DIR = PROJECT_ROOT / "data" / "tokens"
API_BASE_URL = "http://localhost:8000"

REQUIRED_FIELDS = {
    "price": ("current_price", "price"),
    "fdv": ("fdv", "fully_diluted_valuation"),
    "market_cap": ("market_cap",),
    "float_percent": ("float_percent", "float_pct"),
}


class AnalysisCommands(commands.Cog):
    """
    Cog for on-demand analysis commands
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("AnalysisCommands cog initialized")

    def _refresh_token_data(self, symbol: str) -> Dict[str, Any]:
        """Trigger token refetch and wait for completion."""
        url = f"{API_BASE_URL}/api/tokens/{symbol}/refetch"
        resp = requests.post(url, params={"force": "true", "auto_analyze": "false"}, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        task_id = payload.get("task_id")
        if not task_id:
            raise RuntimeError("Refetch did not return a task_id")

        status_url = f"{API_BASE_URL}/api/tokens/research/{task_id}"
        start = time.time()
        while True:
            status_resp = requests.get(status_url, timeout=15)
            if status_resp.status_code == 404:
                time.sleep(2)
                continue
            status_resp.raise_for_status()
            status_payload = status_resp.json()
            status = status_payload.get("status")
            if status in {"completed", "completed_with_warnings"}:
                return status_payload
            if status in {"failed", "skipped"}:
                raise RuntimeError(status_payload.get("error") or status_payload.get("message") or "Refetch failed")
            if time.time() - start > 300:
                raise TimeoutError("Refetch timed out after 300s")
            time.sleep(2)

    def _load_consolidated(self, symbol: str) -> Dict[str, Any]:
        consolidated_path = TOKENS_DIR / symbol.upper() / "consolidated.json"
        if not consolidated_path.exists():
            raise FileNotFoundError(f"No consolidated.json found for {symbol}")
        with open(consolidated_path) as f:
            return json.load(f)

    def _validate_required_fields(self, data: Dict[str, Any]) -> Tuple[bool, list[str]]:
        missing = []
        for label, keys in REQUIRED_FIELDS.items():
            if not any(data.get(key) is not None for key in keys):
                missing.append(label)
        return (len(missing) == 0), missing

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
        # Pass the channel explicitly (it might be the new thread or the original channel)
        # Note: We use ctx.channel which we updated above if a thread was created
        asyncio.create_task(self._run_analysis_task(ctx, status_msg, symbol, ctx.channel))

    async def _run_analysis_task(self, ctx: commands.Context, status_msg: discord.Message, symbol: str, target_channel: discord.abc.Messageable):
        """Background task for analysis"""
        try:
            logger.info(f"🚀 Starting on-demand analysis for {symbol} requested by {ctx.author}")

            # Force refetch and validate required data (no embed if missing)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._refresh_token_data(symbol))
            consolidated = await loop.run_in_executor(None, lambda: self._load_consolidated(symbol))
            ok, missing = self._validate_required_fields(consolidated)
            if not ok:
                missing_str = ", ".join(missing)
                diagnostics = consolidated.get("refresh_diagnostics") or {}
                warning = consolidated.get("data_quality_warning")
                diag_lines = []
                if warning:
                    diag_lines.append(f"⚠️ {warning}")
                if diagnostics.get("missing_critical_groups"):
                    diag_lines.append(
                        f"Missing critical groups: {', '.join(diagnostics['missing_critical_groups'])}"
                    )
                if diagnostics.get("completeness_pct") is not None:
                    diag_lines.append(f"Completeness: {diagnostics['completeness_pct']}%")
                diag_text = "\n" + "\n".join(diag_lines) if diag_lines else ""
                await status_msg.edit(
                    content=(
                        f"❌ Analysis blocked: missing required data after refresh "
                        f"({missing_str}). Please refresh in dashboard and verify sources."
                        f"{diag_text}"
                    )
                )
                return

            # Run the full pipeline
            # Note: We run in executor to avoid blocking the bot's event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                lambda: full_pipeline(
                    symbol=symbol, 
                    force_refresh=False,  # Refresh is handled above
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

            # Delete the "Analyzing..." status message
            try:
                await status_msg.delete()
            except discord.NotFound:
                pass # Message already deleted or not found

            # Send result to the target channel (thread or main channel)
            # We use target_channel.send() instead of ctx.reply() to avoid 
            # "Cannot reply to a message in a different channel" errors when in a thread
            await target_channel.send(embed=embed, view=view)
            
            logger.info(f"✅ Sent analysis report for {symbol}")

        except Exception as e:
            logger.error(f"Error in analyze command: {e}", exc_info=True)
            # Try to report error to the user if possible
            try:
                if status_msg:
                    await status_msg.edit(content=f"❌ An error occurred: {str(e)}")
                else:
                    await target_channel.send(f"❌ An error occurred: {str(e)}")
            except:
                pass


async def setup(bot: commands.Bot):
    """Setup function called by bot when loading this cog"""
    await bot.add_cog(AnalysisCommands(bot))
    logger.info("AnalysisCommands cog loaded")
