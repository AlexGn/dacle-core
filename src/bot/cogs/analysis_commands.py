"""
Analysis Command Cog
Handles the "analyze" command natively in Python bot for proper thread support.
Session 396: Replaces OpenClaw "analyze" command which lacked thread awareness.
"""

import asyncio
import json
from src.utils.logger import get_logger
import time
from pathlib import Path
from typing import Tuple, Dict, Any, Optional, List

import discord
from discord import app_commands
import requests
import os
from discord.ext import commands

from src.orchestration.trade_workflow import full_pipeline
from src.bot.cogs.analysis_formatter import AnalysisFormatter
from src.bot.cogs.analysis_views import TradeApprovalView
from src.bot.utils.safe_task import safe_create_task
from src.utils.config import get_discord_config
from api.routers.macro import get_btc_regime_widget

logger = get_logger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TOKENS_DIR = PROJECT_ROOT / "data" / "tokens"
DISAMBIGUATION_PATH = PROJECT_ROOT / "data" / "bot" / "token_disambiguation.json"
def _get_api_base_url() -> str:
    """Resolve API base URL at call time (after load_config)."""
    return os.getenv("DACLE_API_URL", "http://localhost:8000")
DEFAULT_ANALYSIS_CHANNEL_ID = 1470403542253703369

REQUIRED_FIELDS = {
    "price": ("current_price", "price"),
    "fdv": ("fdv", "fully_diluted_valuation"),
    "market_cap": ("market_cap",),
    # float_percent: scorer handles missing gracefully (0/5 score + "MISSING DATA" flag).
    # Hard-gating here blocks established tokens (e.g. TAO) where sources lack supply data.
}
ANALYSIS_REFRESH_TIMEOUT_SECONDS = 180
ANALYSIS_PIPELINE_TIMEOUT_SECONDS = 240
MAX_BATCH_SYMBOLS = 5
BATCH_CONCURRENCY = 3
TA_FRESHNESS_THRESHOLD_MINUTES = 30


def _check_ta_freshness(symbol: str) -> bool:
    """Return True if TA data is fresh (<30 min), False if stale."""
    ta_file = TOKENS_DIR / symbol.upper() / "ta" / "latest.json"
    if not ta_file.exists():
        return False
    try:
        mtime = ta_file.stat().st_mtime
        age_min = (time.time() - mtime) / 60
        return age_min < TA_FRESHNESS_THRESHOLD_MINUTES
    except Exception:
        return False


def parse_batch_symbols(symbols_str: str) -> list[str]:
    """Parse comma or space separated symbols into a deduped list.

    - Strips $ prefix
    - Uppercases
    - Deduplicates (preserving order)
    - Max 5 symbols
    """
    # Split on commas first; if no commas, split on whitespace
    if "," in symbols_str:
        parts = [s.strip() for s in symbols_str.split(",")]
    else:
        parts = symbols_str.split()

    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        cleaned = part.strip().lstrip("$").upper()
        if not cleaned:
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result[:MAX_BATCH_SYMBOLS]


class TokenDisambiguationSelect(discord.ui.Select):
    def __init__(self, parent: "TokenDisambiguationView"):
        self._parent_view = parent
        options: List[discord.SelectOption] = []
        for idx, option in enumerate(parent.options):
            name = option.get("name") or "Unknown"
            symbol = (option.get("symbol") or "").upper()
            source = option.get("source") or "unknown"
            mc = option.get("market_cap")
            mc_text = f"MC ${mc:,.0f}" if isinstance(mc, (int, float)) else "MC n/a"
            label = f"{idx + 1}. {name}"
            if len(label) > 100:
                label = label[:97] + "..."
            description = f"{symbol} • {source} • {mc_text}"
            if len(description) > 100:
                description = description[:97] + "..."
            options.append(discord.SelectOption(label=label, description=description, value=str(idx)))

        super().__init__(
            placeholder="Select the correct token...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if self._parent_view.requester_id and interaction.user.id != self._parent_view.requester_id:
            await interaction.response.send_message(
                "Only the requester can select a token for this analysis.",
                ephemeral=True,
            )
            return

        selected_idx = int(self.values[0])
        self._parent_view.selection = self._parent_view.options[selected_idx]
        for child in self._parent_view.children:
            child.disabled = True
        await interaction.response.edit_message(view=self._parent_view)
        self._parent_view.stop()


class TokenDisambiguationView(discord.ui.View):
    def __init__(self, options: List[Dict[str, Any]], requester: Optional[discord.abc.User]):
        super().__init__(timeout=300)
        self.options = options
        self.requester_id = requester.id if requester else None
        self.selection: Optional[Dict[str, Any]] = None
        self.message: Optional[discord.Message] = None

        self.add_item(TokenDisambiguationSelect(self))
        cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
        cancel_button.callback = self._cancel
        self.add_item(cancel_button)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                logger.debug("Failed to disable disambiguation view on timeout", exc_info=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        logger.exception("Disambiguation interaction failed: %s", error)
        if interaction.response.is_done():
            await interaction.followup.send(
                "❌ Failed to apply selection. Please retry `/analyze`.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Failed to apply selection. Please retry `/analyze`.",
                ephemeral=True,
            )

    async def _cancel(self, interaction: discord.Interaction):
        if self.requester_id and interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the requester can cancel this analysis.",
                ephemeral=True,
            )
            return
        self.selection = None
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class AnalysisCommands(commands.Cog):
    """
    Cog for on-demand analysis commands
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("AnalysisCommands cog initialized")

    def _load_disambiguation_cache(self) -> Dict[str, Dict[str, Any]]:
        if not DISAMBIGUATION_PATH.exists():
            return {}
        try:
            with open(DISAMBIGUATION_PATH, "r") as f:
                payload = json.load(f)
                return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _write_disambiguation_cache(self, cache: Dict[str, Dict[str, Any]]) -> None:
        DISAMBIGUATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = DISAMBIGUATION_PATH.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(cache, f, indent=2)
        os.replace(tmp_path, DISAMBIGUATION_PATH)

    def _get_cached_disambiguation(self, symbol: str) -> Optional[Dict[str, Any]]:
        cache = self._load_disambiguation_cache()
        return cache.get(symbol.upper())

    def _cache_disambiguation(self, symbol: str, selection: Dict[str, Any]) -> None:
        cache = self._load_disambiguation_cache()
        cache[symbol.upper()] = {
            "symbol": selection.get("symbol"),
            "name": selection.get("name"),
            "external_id": selection.get("external_id") or selection.get("coingecko_id"),
            "source": selection.get("source"),
        }
        self._write_disambiguation_cache(cache)

    def _search_token(self, symbol: str) -> List[Dict[str, Any]]:
        api_base = _get_api_base_url()
        url = f"{api_base}/api/tokens/search"
        resp = requests.post(url, json={"symbol": symbol.upper()}, timeout=15)
        resp.raise_for_status()
        payload = resp.json() or {}
        return payload.get("matches") or []

    def _research_token_data(self, symbol: str, name: str) -> Dict[str, Any]:
        api_base = _get_api_base_url()
        url = f"{api_base}/api/tokens/research"
        resp = requests.post(url, json={"symbol": symbol.upper(), "name": name}, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        task_id = payload.get("task_id")
        if not task_id:
            raise RuntimeError("Research did not return a task_id")

        status_url = f"{api_base}/api/tokens/research/{task_id}"
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
                raise RuntimeError(status_payload.get("error") or status_payload.get("message") or "Research failed")
            if time.time() - start > 300:
                raise TimeoutError("Research timed out after 300s")
            time.sleep(2)

    def _refresh_token_data(self, symbol: str) -> Dict[str, Any]:
        """Trigger token refetch and wait for completion."""
        api_base = _get_api_base_url()
        url = f"{api_base}/api/tokens/{symbol}/refetch"
        resp = requests.post(url, params={"force": "true", "auto_analyze": "false"}, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        task_id = payload.get("task_id")
        if not task_id:
            raise RuntimeError("Refetch did not return a task_id")

        status_url = f"{api_base}/api/tokens/research/{task_id}"
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
        try:
            with open(consolidated_path) as f:
                return json.load(f)
        except PermissionError as e:
            raise PermissionError(
                f"Permission denied reading {consolidated_path}. "
                "Fix ownership/permissions for data/tokens."
            ) from e

    def _validate_required_fields(self, data: Dict[str, Any]) -> Tuple[bool, list[str]]:
        missing = []
        for label, keys in REQUIRED_FIELDS.items():
            if not any(data.get(key) is not None for key in keys):
                missing.append(label)
        return (len(missing) == 0), missing

    def _resolve_analysis_channel(self) -> Optional[discord.TextChannel]:
        """Resolve canonical analysis-updates channel."""
        channel_id = DEFAULT_ANALYSIS_CHANNEL_ID
        try:
            discord_cfg = get_discord_config()
            if discord_cfg.analysis_channel_id:
                channel_id = discord_cfg.analysis_channel_id
        except Exception:
            # Config may be unavailable in certain test contexts; use fallback.
            pass

        channel = self.bot.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _maybe_disambiguate(
        self,
        symbol: str,
        requester: Optional[discord.abc.User],
        target_channel: discord.abc.Messageable,
    ) -> Optional[Tuple[str, Optional[str]]]:
        force_prompt = len(symbol) <= 2
        if not force_prompt:
            cached = self._get_cached_disambiguation(symbol)
            if cached and cached.get("name"):
                return cached.get("symbol") or symbol, cached.get("name")

        loop = asyncio.get_event_loop()
        matches = await loop.run_in_executor(None, lambda: self._search_token(symbol))
        if not matches:
            return symbol, None
        if len(matches) == 1 and not force_prompt:
            match = matches[0]
            if match.get("name"):
                self._cache_disambiguation(symbol, match)
            return match.get("symbol") or symbol, match.get("name")

        selection = await self._prompt_disambiguation(matches, requester, target_channel, symbol)
        if not selection:
            return None
        self._cache_disambiguation(symbol, selection)
        return selection.get("symbol") or symbol, selection.get("name")

    async def _prompt_disambiguation(
        self,
        matches: List[Dict[str, Any]],
        requester: Optional[discord.abc.User],
        target_channel: discord.abc.Messageable,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        max_options = min(5, len(matches))
        shortlist = matches[:max_options]
        lines = []
        for idx, match in enumerate(shortlist, start=1):
            name = match.get("name") or "Unknown"
            src = match.get("source") or "unknown"
            mc = match.get("market_cap")
            mc_text = f"${mc:,.0f}" if isinstance(mc, (int, float)) else "n/a"
            lines.append(f"{idx}. {name} ({src}, MC {mc_text})")
        prompt = (
            f"Multiple matches found for **{symbol.upper()}**. Select the correct token:\n"
            + "\n".join(lines)
        )
        view = TokenDisambiguationView(shortlist, requester)
        prompt_msg = await target_channel.send(content=prompt, view=view)
        view.message = prompt_msg
        await view.wait()
        if not view.selection:
            await prompt_msg.edit(content="⏳ Disambiguation timed out. Please retry the command.", view=None)
            return None
        await prompt_msg.edit(
            content=(
                f"✅ Selected **{view.selection.get('name')}** ({view.selection.get('symbol')}). "
                "Continuing analysis..."
            ),
            view=None,
        )
        return view.selection

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
                status_msg = await thread.send(f"🔍 Resolving **{symbol.upper()}**...")
                
                # Update context to point to the thread for subsequent replies
                ctx.channel = thread
            except Exception as e:
                logger.warning(f"Failed to create thread: {e}")
                # Fallback to main channel reply
                status_msg = await ctx.reply(f"🔍 Resolving **{symbol.upper()}**...", mention_author=False)
        else:
            # Already in a thread or DM, just reply
            status_msg = await ctx.reply(f"🔍 Resolving **{symbol.upper()}**...", mention_author=False)

        resolved = await self._maybe_disambiguate(symbol, ctx.author, ctx.channel)
        if not resolved:
            await status_msg.edit(content="❌ Analysis cancelled. No token selected.")
            return
        resolved_symbol, resolved_name = resolved
        await status_msg.edit(
            content=f"🔍 Analyzing **{resolved_symbol.upper()}**... (this may take 10-20s)"
        )

        # Run analysis in background task
        # Pass the channel explicitly (it might be the new thread or the original channel)
        # Note: We use ctx.channel which we updated above if a thread was created
        safe_create_task(
            self._run_analysis_task(ctx.author, status_msg, resolved_symbol, ctx.channel, resolved_name=resolved_name),
            logger=logger,
            error_channel=ctx.channel,
            name=f"analyze-{resolved_symbol}",
        )

    @app_commands.command(name="analyze", description="Analyze a token and generate a playbook")
    @app_commands.describe(symbol="Token symbol (e.g., ZRO, ZAMA, RIVER)")
    async def analyze_slash(self, interaction: discord.Interaction, symbol: str):
        """
        Slash command version of analyze.
        Usage: /analyze <SYMBOL>
        """
        symbol = symbol.upper()
        request_id = f"analyze-{interaction.id}"
        invoke_channel = interaction.channel
        analysis_channel = self._resolve_analysis_channel()
        if analysis_channel is None:
            analysis_channel = invoke_channel if isinstance(invoke_channel, discord.TextChannel) else None

        if analysis_channel is None:
            await interaction.response.send_message(
                "❌ Could not resolve analysis channel. Try again in `#analysis-updates`.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🔍 Analyzing **{symbol}**. I will post results in {analysis_channel.mention}.",
            ephemeral=True,
        )

        logger.info(
            "ANALYZE_SLASH_START "
            f"request_id={request_id} "
            f"symbol={symbol} "
            f"user_id={interaction.user.id} "
            f"channel_id={analysis_channel.id}"
        )

        logger.info(
            f"🔍 /analyze requested by {interaction.user} for {symbol}; "
            f"target channel #{analysis_channel.name} ({analysis_channel.id})"
        )

        status_msg = await analysis_channel.send(
            f"🔍 Analyzing **{symbol}**... (requested by {interaction.user.mention})"
        )
        target_channel: discord.abc.Messageable = analysis_channel

        try:
            thread = await status_msg.create_thread(
                name=f"Analysis: {symbol}",
                auto_archive_duration=1440,
            )
            thread_status = await thread.send(
                f"🔍 Analyzing **{symbol}**... (this may take 10-20s)"
            )
            status_msg = thread_status
            target_channel = thread
        except Exception as e:
            logger.warning(f"Failed to create thread for slash analyze ({symbol}): {e}")
            target_channel = analysis_channel

        try:
            resolved = await self._maybe_disambiguate(symbol, interaction.user, target_channel)
            if not resolved:
                await status_msg.edit(content="❌ Analysis cancelled. No token selected.")
                return
            resolved_symbol, resolved_name = resolved
            await status_msg.edit(
                content=f"🔍 Analyzing **{resolved_symbol}**... (this may take 10-20s)"
            )

            safe_create_task(
                self._run_analysis_task(
                    interaction.user,
                    status_msg,
                    resolved_symbol,
                    target_channel,
                    notify_channel=analysis_channel,
                    resolved_name=resolved_name,
                    request_id=request_id,
                ),
                logger=logger,
                error_channel=target_channel,
                name=f"analyze-slash-{resolved_symbol}",
            )
        except Exception as e:
            logger.error(
                "ANALYZE_SLASH_ERROR "
                f"request_id={request_id} "
                f"symbol={symbol} "
                f"reason=slash_setup_exception "
                f"error={e}",
                exc_info=True,
            )
            try:
                await status_msg.edit(
                    content=f"❌ Analysis failed for **{symbol}**: {e}"
                )
            except Exception:
                pass

    @app_commands.command(name="analyze-batch", description="Analyze multiple tokens concurrently")
    @app_commands.describe(symbols="Comma-separated token symbols (e.g., ZRO, ALCH, DRIFT)")
    async def analyze_batch_slash(self, interaction: discord.Interaction, symbols: str):
        """Slash command to analyze multiple tokens at once."""
        parsed = parse_batch_symbols(symbols)
        if not parsed:
            await interaction.response.send_message(
                "No valid symbols provided. Use comma or space separated symbols (e.g., `ZRO, ALCH, DRIFT`).",
                ephemeral=True,
            )
            return

        analysis_channel = self._resolve_analysis_channel()
        invoke_channel = interaction.channel
        if analysis_channel is None:
            analysis_channel = invoke_channel if isinstance(invoke_channel, discord.TextChannel) else None

        if analysis_channel is None:
            await interaction.response.send_message(
                "Could not resolve analysis channel. Try again in `#analysis-updates`.",
                ephemeral=True,
            )
            return

        symbol_list = ", ".join(parsed)
        await interaction.response.send_message(
            f"Analyzing **{len(parsed)}** tokens: {symbol_list}. Results will appear in {analysis_channel.mention}.",
            ephemeral=True,
        )

        logger.info(
            f"/analyze-batch requested by {interaction.user} for [{symbol_list}] "
            f"target channel #{analysis_channel.name} ({analysis_channel.id})"
        )

        semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)
        tasks = [
            self._analyze_one(sym, analysis_channel, interaction.user, semaphore)
            for sym in parsed
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success_count = sum(
            1 for r in results
            if isinstance(r, tuple) and r[1] is True
        )
        try:
            await interaction.followup.send(
                f"Batch complete: **{success_count}/{len(parsed)}** analyses finished.",
                ephemeral=True,
            )
        except Exception:
            logger.debug("Failed to send batch completion followup", exc_info=True)

    async def _analyze_one(
        self,
        symbol: str,
        analysis_channel: discord.TextChannel,
        requester: discord.abc.User,
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, bool]:
        """Analyze a single token with semaphore-controlled concurrency."""
        async with semaphore:
            status_msg = await analysis_channel.send(
                f"Analyzing **{symbol}**... (batch request by {requester.mention})"
            )
            target_channel: discord.abc.Messageable = analysis_channel
            try:
                thread = await status_msg.create_thread(
                    name=f"Analysis: {symbol}",
                    auto_archive_duration=1440,
                )
                thread_status = await thread.send(
                    f"Analyzing **{symbol}**... (this may take 10-20s)"
                )
                status_msg = thread_status
                target_channel = thread
            except Exception:
                pass

            try:
                resolved = await self._maybe_disambiguate(symbol, requester, target_channel)
                if not resolved:
                    await status_msg.edit(content=f"Skipped {symbol} -- disambiguation cancelled.")
                    return symbol, False
                resolved_symbol, resolved_name = resolved
                await status_msg.edit(
                    content=f"Analyzing **{resolved_symbol}**... (this may take 10-20s)"
                )

                await self._run_analysis_task(
                    requester,
                    status_msg,
                    resolved_symbol,
                    target_channel,
                    notify_channel=analysis_channel,
                    resolved_name=resolved_name,
                )
                return symbol, True
            except Exception as e:
                logger.error(f"Batch analysis failed for {symbol}: {e}", exc_info=True)
                try:
                    await status_msg.edit(content=f"Analysis failed for **{symbol}**: {e}")
                except Exception:
                    pass
                return symbol, False

    async def _run_analysis_task(
        self,
        requester: Optional[discord.abc.User],
        status_msg: discord.Message,
        symbol: str,
        target_channel: discord.abc.Messageable,
        notify_channel: Optional[discord.abc.Messageable] = None,
        resolved_name: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        """Background task for analysis"""
        try:
            requester_name = requester if requester else "unknown"
            logger.info(
                f"🚀 Starting on-demand analysis for {symbol} requested by {requester_name} "
                f"(request_id={request_id or 'n/a'})"
            )

            # Force refetch and validate required data (no embed if missing)
            loop = asyncio.get_running_loop()
            try:
                if resolved_name:
                    await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: self._research_token_data(symbol, resolved_name)),
                        timeout=ANALYSIS_REFRESH_TIMEOUT_SECONDS,
                    )
                else:
                    await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: self._refresh_token_data(symbol)),
                        timeout=ANALYSIS_REFRESH_TIMEOUT_SECONDS,
                    )
                consolidated = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: self._load_consolidated(symbol)),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "ANALYZE_SLASH_ERROR "
                    f"request_id={request_id or 'n/a'} "
                    f"symbol={symbol} "
                    "reason=refresh_timed_out"
                )
                await status_msg.edit(
                    content=(
                        f"❌ Analysis timed out while refreshing **{symbol}**. "
                        "Please retry in ~1 minute."
                    )
                )
                return
            # 4c: TA freshness warning
            if os.getenv("AUTO_REFRESH_ON_ANALYZE", "").lower() == "true":
                if not _check_ta_freshness(symbol):
                    logger.info(f"[{symbol}] TA data is stale (>{TA_FRESHNESS_THRESHOLD_MINUTES}min)")
                    try:
                        await status_msg.edit(
                            content=f"Refreshing stale TA data for **{symbol}**..."
                        )
                    except Exception:
                        pass

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
                logger.error(
                    "ANALYZE_SLASH_ERROR "
                    f"request_id={request_id or 'n/a'} "
                    f"symbol={symbol} "
                    f"reason=missing_required_data "
                    f"missing={missing_str}"
                )
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
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: full_pipeline(
                            symbol=symbol,
                            force_refresh=False,  # Refresh is handled above
                            force_playbook=True,  # Always generate playbook
                            notify_discord=False,  # We handle notification manually
                        ),
                    ),
                    timeout=ANALYSIS_PIPELINE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "ANALYZE_SLASH_ERROR "
                    f"request_id={request_id or 'n/a'} "
                    f"symbol={symbol} "
                    "reason=pipeline_timed_out"
                )
                await status_msg.edit(
                    content=(
                        f"❌ Analysis timed out while running pipeline for **{symbol}**. "
                        "Please retry shortly."
                    )
                )
                return
            
            if result.has_error:
                logger.error(
                    "ANALYZE_SLASH_ERROR "
                    f"request_id={request_id or 'n/a'} "
                    f"symbol={symbol} "
                    f"reason=pipeline_error "
                    f"error={result.error_message}"
                )
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
            view = TradeApprovalView(symbol, result.conviction_score, direction=result.direction)

            # Send result to the target channel (thread or main channel)
            # We use target_channel.send() instead of ctx.reply() to avoid 
            # "Cannot reply to a message in a different channel" errors when in a thread
            await target_channel.send(embed=embed, view=view)

            # Delete status only after successful delivery
            try:
                await status_msg.delete()
            except discord.NotFound:
                pass  # Message already deleted or not found
            
            logger.info(
                "ANALYZE_SLASH_SUCCESS "
                f"request_id={request_id or 'n/a'} "
                f"symbol={symbol}"
            )
            logger.info(f"✅ Sent analysis report for {symbol}")

        except Exception as e:
            logger.error(
                "ANALYZE_SLASH_ERROR "
                f"request_id={request_id or 'n/a'} "
                f"symbol={symbol} "
                f"reason=exception "
                f"error={e}"
            )
            logger.error(f"Error in analyze command: {e}", exc_info=True)
            # Try to report error to the user if possible
            try:
                err_text = str(e)
                if "Permission denied" in err_text and "consolidated.json" in err_text:
                    err_text = (
                        "Permission denied reading consolidated.json. "
                        "Please fix data folder ownership (clawd) and retry."
                    )
                if status_msg:
                    await status_msg.edit(content=f"❌ An error occurred while analyzing **{symbol}**: {err_text}")
                elif notify_channel:
                    await notify_channel.send(f"❌ Analysis failed for **{symbol}**: {err_text}")
                else:
                    await target_channel.send(f"❌ An error occurred: {err_text}")
            except Exception:
                pass


    async def cog_app_command_error(self, interaction, error):
        logger.error(f"[AnalysisCommands] {error}", exc_info=error)


async def setup(bot: commands.Bot):
    """Setup function called by bot when loading this cog"""
    await bot.add_cog(AnalysisCommands(bot))
    logger.info("AnalysisCommands cog loaded")
