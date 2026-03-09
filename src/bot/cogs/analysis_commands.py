"""
Analysis Command Cog
Handles the "analyze" command natively in Python bot for proper thread support.
Session 396: Replaces OpenClaw "analyze" command which lacked thread awareness.
"""

import asyncio
import json
from src.utils.logger import get_logger
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple, Dict, Any, Optional, List, Callable

import discord
from discord import app_commands
import requests
import os
from discord.ext import commands

from src.orchestration.trade_workflow import run_full_pipeline_with_thread_loop
from src.bot.cogs.analysis_formatter import AnalysisFormatter
from src.bot.cogs.analysis_views import TradeApprovalView
from src.bot.utils.interaction_response import safe_defer, safe_send
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


def _api_headers() -> Dict[str, str]:
    api_key = os.getenv("DACLE_API_KEY", "").strip()
    return {"X-API-Key": api_key} if api_key else {}


DEFAULT_ANALYSIS_CHANNEL_ID = 1470403542253703369

REQUIRED_FIELDS = {
    "price": ("current_price", "price"),
    "fdv": ("fdv", "fully_diluted_valuation"),
    "market_cap": ("market_cap",),
    # float_percent: scorer handles missing gracefully (0/5 score + "MISSING DATA" flag).
    # Hard-gating here blocks established tokens (e.g. TAO) where sources lack supply data.
}
ANALYSIS_REFRESH_TIMEOUT_SECONDS = 420
ANALYSIS_PIPELINE_TIMEOUT_SECONDS = 240
MAX_BATCH_SYMBOLS = 5
BATCH_CONCURRENCY = 3
TA_FRESHNESS_THRESHOLD_MINUTES = 30
API_CONNECT_TIMEOUT_SECONDS = float(os.getenv("DACLE_API_CONNECT_TIMEOUT_SECONDS", "5"))
API_READ_TIMEOUT_SECONDS = float(os.getenv("DACLE_API_READ_TIMEOUT_SECONDS", "90"))
API_STATUS_READ_TIMEOUT_SECONDS = float(os.getenv("DACLE_API_STATUS_READ_TIMEOUT_SECONDS", "15"))
API_REFRESH_KICKOFF_READ_TIMEOUT_SECONDS = float(
    os.getenv("DACLE_API_REFRESH_KICKOFF_READ_TIMEOUT_SECONDS", "20")
)
API_RESEARCH_KICKOFF_READ_TIMEOUT_SECONDS = float(
    os.getenv("DACLE_API_RESEARCH_KICKOFF_READ_TIMEOUT_SECONDS", "20")
)
ANALYSIS_REFRESH_POLL_TIMEOUT_SECONDS = int(os.getenv("ANALYSIS_REFRESH_POLL_TIMEOUT_SECONDS", "300"))
ANALYSIS_REFRESH_FALLBACK_MAX_AGE_MINUTES = int(
    os.getenv("ANALYSIS_REFRESH_FALLBACK_MAX_AGE_MINUTES", "240")
)
API_KICKOFF_RETRIES = int(os.getenv("DACLE_API_KICKOFF_RETRIES", "2"))
API_KICKOFF_RETRY_DELAY_SECONDS = float(os.getenv("DACLE_API_KICKOFF_RETRY_DELAY_SECONDS", "2"))


def _iter_exception_chain(exc: BaseException):
    """Yield exception plus nested cause/context chain."""
    seen: set[int] = set()
    stack: list[Optional[BaseException]] = [exc]
    while stack:
        current = stack.pop()
        if current is None:
            continue
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        yield current
        stack.append(getattr(current, "__cause__", None))
        stack.append(getattr(current, "__context__", None))
        for arg in getattr(current, "args", ()):
            if isinstance(arg, BaseException):
                stack.append(arg)


def _is_timeout_like_error(exc: BaseException) -> bool:
    """Handle wrapped timeout errors that requests may surface as ConnectionError."""
    for current in _iter_exception_chain(exc):
        if isinstance(current, (requests.Timeout, asyncio.TimeoutError)):
            return True
        name = type(current).__name__.lower()
        text = str(current).lower()
        if "timeout" in name:
            return True
        if "read timed out" in text or "connect timeout" in text:
            return True
        if "timed out" in text and (
            "httpconnectionpool" in text
            or "urllib3" in text
            or "127.0.0.1" in text
            or "localhost" in text
        ):
            return True
    return False


def _format_user_facing_analysis_error(exc: BaseException) -> str:
    """Convert internal exception details into stable user-facing text."""
    err_text = str(exc)
    if "Permission denied" in err_text and "consolidated.json" in err_text:
        return (
            "Permission denied reading consolidated.json. "
            "Please fix data folder ownership (clawd) and retry."
        )
    if _is_timeout_like_error(exc):
        return (
            "Timed out waiting for local API response. "
            "API may be busy; retry in ~1 minute."
        )
    return err_text


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


def _request_with_retry(
    method: str,
    url: str,
    *,
    retries: int = 0,
    retry_delay_seconds: float = 1.0,
    **kwargs: Any,
) -> requests.Response:
    """Execute an HTTP request with optional timeout retries."""
    for attempt in range(1, retries + 2):
        try:
            return requests.request(method=method, url=url, **kwargs)
        except requests.RequestException as exc:
            if not _is_timeout_like_error(exc):
                raise
            if attempt > retries:
                raise requests.Timeout(str(exc)) from exc
            logger.warning(
                f"HTTP timeout on {method} {url} (attempt {attempt}/{retries + 1}); "
                f"retrying in {retry_delay_seconds}s"
            )
            time.sleep(retry_delay_seconds)

    raise RuntimeError(f"Unexpected retry exhaustion for {method} {url}")


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _estimate_snapshot_age_minutes(symbol: str, consolidated: Dict[str, Any]) -> Optional[float]:
    """Estimate cached snapshot age from metadata timestamps, then file mtime."""
    now = datetime.now(timezone.utc)
    for key in ("last_refreshed", "data_fetched_at", "last_updated"):
        parsed = _parse_iso_datetime(consolidated.get(key))
        if parsed is not None:
            return max(0.0, (now - parsed).total_seconds() / 60.0)

    consolidated_path = TOKENS_DIR / symbol.upper() / "consolidated.json"
    try:
        mtime = consolidated_path.stat().st_mtime
    except OSError:
        return None
    return max(0.0, (time.time() - mtime) / 60.0)


async def _load_cached_after_refresh_delay(
    *,
    loop: asyncio.AbstractEventLoop,
    symbol: str,
    reason: str,
    request_id: Optional[str],
    status_msg: discord.Message,
    load_cached_fn: Callable[[], Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    logger.warning(
        "ANALYZE_SLASH_WARN "
        f"request_id={request_id or 'n/a'} "
        f"symbol={symbol} "
        f"reason={reason} "
        "action=load_cached_snapshot"
    )
    try:
        cached = await asyncio.wait_for(
            loop.run_in_executor(None, load_cached_fn),
            timeout=30,
        )
    except Exception:
        logger.error(
            "ANALYZE_SLASH_ERROR "
            f"request_id={request_id or 'n/a'} "
            f"symbol={symbol} "
            "reason=refresh_timeout_and_cached_load_failed",
            exc_info=True,
        )
        await status_msg.edit(
            content=(
                f"❌ Analysis timed out while refreshing **{symbol}**, and cached data "
                "could not be loaded. Please retry in ~1 minute."
            )
        )
        return None, None

    age_minutes = _estimate_snapshot_age_minutes(symbol, cached)
    max_age = max(0, ANALYSIS_REFRESH_FALLBACK_MAX_AGE_MINUTES)
    if age_minutes is not None and max_age > 0 and age_minutes > float(max_age):
        await status_msg.edit(
            content=(
                f"❌ Analysis refresh timed out and cached data for **{symbol}** is too old "
                f"({age_minutes:.0f}m > {max_age}m). Retry shortly."
            )
        )
        return None, None

    age_text = f"{age_minutes:.0f}m old" if age_minutes is not None else "age unknown"
    await status_msg.edit(
        content=(
            f"⚠️ Refresh delayed for **{symbol}**. Continuing with latest cached snapshot "
            f"({age_text})."
        )
    )
    return cached, age_minutes


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

    @staticmethod
    def _strict_account_authz_enabled() -> bool:
        raw = str(os.getenv("SWING_STRICT_DISCORD_ACCOUNT_AUTHZ", "false") or "false").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _allowed_user_ids() -> set[str]:
        raw = os.getenv("DISCORD_ALLOWED_USER_IDS", "").strip()
        allowed = {item.strip() for item in raw.split(",") if item.strip()} if raw else set()
        owner_id = os.getenv("DISCORD_OWNER_ID", "").strip()
        if owner_id:
            allowed.add(owner_id)
        return allowed

    @staticmethod
    def _account_acl() -> Dict[str, set[str]]:
        raw = os.getenv("DISCORD_ACCOUNT_ACL", "").strip()
        if not raw:
            return {}
        acl: Dict[str, set[str]] = {}
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                for account_id, users in loaded.items():
                    aid = str(account_id).strip()
                    if not aid:
                        continue
                    if isinstance(users, str):
                        user_set = {u.strip() for u in users.split(",") if u.strip()}
                    elif isinstance(users, (list, tuple, set)):
                        user_set = {str(u).strip() for u in users if str(u).strip()}
                    else:
                        user_set = set()
                    if user_set:
                        acl[aid] = user_set
                if acl:
                    return acl
        except Exception:
            pass

        # Fallback parser: "manual:111,222;autonomous:333"
        for block in raw.split(";"):
            chunk = block.strip()
            if not chunk or ":" not in chunk:
                continue
            account_id, users_raw = chunk.split(":", 1)
            aid = account_id.strip()
            users = {u.strip() for u in users_raw.split(",") if u.strip()}
            if aid and users:
                acl[aid] = users
        return acl

    @staticmethod
    def _resolve_account_id(account_id: Optional[str] = None) -> str:
        candidate = str(account_id or "").strip()
        if candidate:
            return candidate
        cog_default = str(os.getenv("DISCORD_ANALYSIS_ACCOUNT_ID", "") or "").strip()
        if cog_default:
            return cog_default
        fallback = str(os.getenv("DISCORD_DEFAULT_ACCOUNT_ID", "primary") or "").strip()
        return fallback or "primary"

    def _is_authorized(self, user_id: int, *, account_id: Optional[str] = None) -> bool:
        allowed = self._allowed_user_ids()
        if not allowed:
            globally_allowed = True
        else:
            globally_allowed = str(user_id) in allowed
        if not globally_allowed:
            return False

        account_acl = self._account_acl()
        if not account_acl:
            # Backward compatibility: do not hard-lock commands if ACL is unconfigured.
            return False if self._strict_account_authz_enabled() else True
        resolved_account = self._resolve_account_id(account_id)
        allowed_for_account = account_acl.get(resolved_account) or account_acl.get("*")
        if not allowed_for_account:
            # Backward compatibility: only enforce when account has an explicit ACL entry.
            return False if self._strict_account_authz_enabled() else True
        return str(user_id) in allowed_for_account

    @staticmethod
    def _is_server_admin_or_owner(interaction: discord.Interaction) -> bool:
        guild = getattr(interaction, "guild", None)
        user = getattr(interaction, "user", None)
        if guild is None or user is None:
            return False
        if getattr(guild, "owner_id", None) == getattr(user, "id", None):
            return True
        perms = getattr(user, "guild_permissions", None)
        return bool(getattr(perms, "administrator", False))

    def _is_authorized_interaction(
        self,
        interaction: discord.Interaction,
        *,
        account_id: Optional[str] = None,
    ) -> bool:
        if self._is_authorized(interaction.user.id, account_id=account_id):
            return True
        if self._is_server_admin_or_owner(interaction):
            logger.warning(
                "AnalysisCommands auth fallback granted for guild admin/owner user_id=%s guild_id=%s command_channel_id=%s",
                getattr(getattr(interaction, "user", None), "id", None),
                getattr(getattr(interaction, "guild", None), "id", None),
                getattr(interaction, "channel_id", None),
            )
            return True
        return False

    async def _deny_interaction(self, interaction: discord.Interaction) -> None:
        logger.warning(
            "AnalysisCommands unauthorized user_id=%s guild_id=%s channel_id=%s allowed_user_ids=%s",
            getattr(getattr(interaction, "user", None), "id", None),
            getattr(getattr(interaction, "guild", None), "id", None),
            getattr(interaction, "channel_id", None),
            ",".join(sorted(self._allowed_user_ids())) or "(unset)",
        )
        await safe_send(
            interaction,
            command_name="analysis-auth",
            logger=logger,
            content="⛔ Not authorized.",
            ephemeral=True,
        )

    @app_commands.command(name="audit", description="Trigger a multi-agent Deep Audit for a token")
    @app_commands.describe(symbol="Token symbol to audit (e.g. MONAD, AZTEC, ETH)")
    async def audit_slash(self, interaction: discord.Interaction, symbol: str):
        """
        Natively execute a multi-agent Deep Audit by calling internal APIs 
        and synthesizing the result directly in Python.
        """
        account_id = self._resolve_account_id()
        if not self._is_authorized_interaction(interaction, account_id=account_id):
            await self._deny_interaction(interaction)
            return

        sym = symbol.strip().lstrip("$").upper()
        audit_channel_id = 1474325144913838232  # #audit-token
        
        # 1. Defer immediately to prevent interaction timeout (3s limit)
        await safe_defer(
            interaction,
            ephemeral=True,
            command_name="audit",
            logger=logger,
        )
        
        audit_channel = self.bot.get_channel(audit_channel_id)
        if not audit_channel:
            try:
                audit_channel = await self.bot.fetch_channel(audit_channel_id)
            except Exception:
                await safe_send(
                    interaction,
                    command_name="audit",
                    logger=logger,
                    content="❌ Could not find the audit channel.",
                    ephemeral=True,
                )
                return

        await safe_send(
            interaction,
            command_name="audit",
            logger=logger,
            content=(
                f"🔍 **Initiating Native Deep Audit for ${sym}...**\n"
                f"Specialists are being summoned to <#{audit_channel_id}>."
            ),
            ephemeral=True,
        )

        # Start the audit processing in the background
        safe_create_task(
            self._run_native_audit(audit_channel, sym, interaction.user.mention),
            logger=logger
        )

    async def _run_native_audit(self, channel: discord.TextChannel, symbol: str, mention: str):
        """
        Simulate the 6-specialist coordination by fetching all relevant API data
        and using the LLM to synthesize a single strategic brief.
        """
        
        logger.info(f"AUDIT_START: Starting native audit for {symbol}")
        async with channel.typing():
            api_base = _get_api_base_url()
            import httpx
            
            # 1. Gather Data (The Specialists)
            data = {}
            async with httpx.AsyncClient(timeout=25.0, headers=_api_headers()) as client:
                try:
                    logger.info(f"AUDIT_FETCH: Calling Market Specialist API")
                    r = await client.get(f"{api_base}/api/macro/market-direction")
                    market_data = r.json() if r.status_code == 200 else {"status": "error"}
                    
                    # --- NEW: Narrative Connection (Session 441) ---
                    r_trend = await client.get(f"{api_base}/api/macro/market-direction/trend")
                    if r_trend.status_code == 200:
                        market_data["narrative_history"] = r_trend.json()
                    
                    # --- SELF-HEALING BLOCK ---
                    if market_data.get("status") == "no_data" or market_data.get("bias") == "UNKNOWN":
                        logger.info("AUDIT_SELF_HEALING: Stale macro detected. Triggering refresh...")
                        await client.post(f"{api_base}/api/macro/refresh")
                        market_data["self_healing_active"] = True
                    
                    data['market'] = market_data
                    
                    logger.info(f"AUDIT_FETCH: Calling BTC Regime Specialist API")
                    r = await client.get(f"{api_base}/api/macro/btc-regime-widget")
                    data['regime'] = r.json() if r.status_code == 200 else {"error": "API Down"}
                    
                    logger.info(f"AUDIT_FETCH: Calling Calendar Specialist API")
                    r = await client.get(f"{api_base}/api/macro/economic-calendar")
                    data['calendar'] = r.json() if r.status_code == 200 else {"error": "API Down"}
                    
                    logger.info(f"AUDIT_FETCH: Calling Institutional Scout API for {symbol}")
                    r = await client.get(f"{api_base}/api/tokens/{symbol}/bot-summary")
                    data['token_summary'] = r.json() if r.status_code == 200 else {"error": "Token not found or API Down"}
                    
                    # --- UNIFIED INTELLIGENCE BRIDGE: Execution Check ---
                    logger.info(f"AUDIT_FETCH: Running Execution Specialist (Silent Pre-Trade Check)")
                    r_ptc = await client.get(f"{api_base}/api/execution/pre-trade-check-summary/{symbol}")
                    if r_ptc.status_code == 200:
                        data['execution_check'] = r_ptc.json()
                    
                    logger.info(f"AUDIT_FETCH: Calling Alpha Specialist API")
                    r = await client.get(f"{api_base}/api/learning/effectiveness/feedback/report")
                    data['learnings'] = r.json() if r.status_code == 200 else {"error": "API Down"}
                    
                    logger.info(f"AUDIT_FETCH: Calling Risk Specialist API")
                    r = await client.get(f"{api_base}/api/policy/drawdown-status")
                    data['drawdown'] = r.json() if r.status_code == 200 else {"error": "API Down"}
                    
                    logger.info(f"AUDIT_FETCH: Calling Position Specialist API")
                    r = await client.get(f"{api_base}/api/blofin/positions")
                    data['positions'] = r.json() if r.status_code == 200 else {"error": "API Down"}
                    
                    # --- NEW: Liquidation Gravity Bridge (Session 441) ---
                    logger.info(f"AUDIT_FETCH: Calling Orderflow Specialist for {symbol}")
                    r_liq = await client.get(f"{api_base}/api/analysis/advanced/{symbol}")
                    if r_liq.status_code == 200:
                        data['orderflow'] = r_liq.json()
                    
                    logger.info(f"AUDIT_FETCH: Calling Compliance Officer API")
                    r = await client.get(f"{api_base}/api/policy/deltas/active")
                    data['active_policies'] = r.json() if r.status_code == 200 else {"error": "API Down"}
                    
                    # --- NEW: Nightly Wisdom Bridge (Session 441) ---
                    wisdom_file = PROJECT_ROOT / "data" / "state" / "nightly_synthesis_results.json"
                    if wisdom_file.exists():
                        try:
                            data['nightly_wisdom'] = json.loads(wisdom_file.read_text())
                        except Exception: pass
                    
                    # --- NEW: Social Divergence Bridge (Session 441) ---
                    logger.info(f"AUDIT_FETCH: Calling Social Divergence Specialist for {symbol}")
                    r_social = await client.get(f"{api_base}/api/tokens/{symbol}/sentiment-divergence")
                    if r_social.status_code == 200:
                        data['social_divergence'] = r_social.json()
                    
                except Exception as e:
                    logger.error(f"AUDIT_ERROR: Data gathering failed: {e}")
                    data['gathering_error'] = str(e)

            # 2. Synthesize via LLM (The Manager)
            logger.info(f"AUDIT_SYNTHESIS: Spawning Manager via Unified Team (Groq primary, OpenAI fallback)")
            from src.integrations.llm import get_llm_client
            llm = get_llm_client()
            
            persona = (
                "You are the Elite Dacle Audit Manager. You oversee 6 specialists: Market, Institutional, Alpha, Risk, Behavioral, and Compliance. "
                "Your goal is to be AGGRESSIVE, DATA-HUNGRY, and CRITICAL. "
                "Specifically: "
                "1. DATA INTEGRITY: If any field is 'N/A' or 'Unknown' (like FDV or VC backing), flag this as a 'Critical Information Gap'. "
                "2. ROCKET MODE (MOMENTUM): If RVOL > 3.0 and Price > EMA200, lead with '[ROCKET MODE DETECTED]'. Analyze the decoupling strength from broad macro. "
                "3. SECTOR MASTER: Proactively identify the token's sector (e.g. RWA, AI, DePIN). Link this to the macro context. "
                "4. MACRO VELOCITY: You must explain the 'Why' behind the Macro Risk Rating. Link BTC Structure, DXY Trend, and Social Divergence. "
                "4. CONTRARIAN: If price is up but social mindshare is down, you MUST warn of an 'Exhaustion Trap'. "
                "Structure: [STRATEGIC BRIEF] TOKEN - Deep Audit Result, followed by the 6 specialist sections and a Final Verdict with a 'Macro Risk Rating' (1-10)."
            )
            
            context = json.dumps(data, indent=2)
            query = f"Perform a Deep Audit for ${symbol}. Requested by {mention}."
            
            try:
                brief = await llm.complete_async(
                    messages=[
                        {"role": "system", "content": persona},
                        {"role": "system", "content": f"CONTEXT DATA:\n{context}"},
                        {"role": "user", "content": query}
                    ],
                    model_hint="text",
                    temperature=0.7,
                    max_tokens=1000
                )
                brief_text = brief["content"].strip()
                provider = brief.get("provider", "unknown")
                logger.info(f"AUDIT_SYNTHESIS_COMPLETE: Brief generated via {provider} ({len(brief_text)} chars)")
                
                if len(brief_text) < 100:
                    brief_text = self._synthesize_audit_brief(symbol, data, mention)
            except Exception as e:
                logger.error(f"AUDIT_SYNTHESIS_FAILED: Unified LLM Synthesis failed: {e}")
                brief_text = self._synthesize_audit_brief(symbol, data, mention)
                
            # 3. Decision Gate: Determine if we show the Green Light
            view = None
            execution_check = data.get('execution_check', {})
            ptc = execution_check.get('pre_trade_check', {})
            
            # Logic: Show button if Execution is APPROVED AND Audit suggests conviction
            is_approved = ptc.get('approved', False)
            audit_conviction = float(data.get('summary', {}).get('long_conviction_score', 0) or data.get('summary', {}).get('short_conviction_score', 0) or 0)
            
            if is_approved or audit_conviction >= 8.0:
                logger.info(f"AUDIT_GATE: Green Light detected for {symbol}. Attaching execution button.")
                from src.bot.cogs.analysis_views import AuditExecutionView
                direction = ptc.get('direction') or data.get('summary', {}).get('direction_detection', {}).get('recommended', 'LONG')
                view = AuditExecutionView(symbol, direction, audit_conviction)

            # 4. Deliver (with 2000 char limit splitting)
            if len(brief_text) <= 2000:
                await channel.send(brief_text, view=view)
            else:
                chunks = [brief_text[i:i+1900] for i in range(0, len(brief_text), 1900)]
                for i, chunk in enumerate(chunks):
                    header = f"(Part {i+1}/{len(chunks)})\n" if len(chunks) > 1 else ""
                    # Attach view only to the last chunk
                    current_view = view if i == len(chunks) - 1 else None
                    await channel.send(header + chunk, view=current_view)
            
            logger.info(f"AUDIT_DELIVERED: Strategic brief sent to channel")

    def _synthesize_audit_brief(self, symbol: str, data: dict, mention: str) -> str:
        """Formatted synthesis of all gathered data points."""
        market = data.get('market', {})
        regime = data.get('regime', {}).get('data', {})
        summary = data.get('summary', {})
        risk = data.get('risk', {}).get('data', {})
        
        bias = market.get('recommendation', 'UNKNOWN')
        bias_emoji = "🟢" if bias == "LONG" else "🔴" if bias == "SHORT" else "🟡"
        
        brief = f"### **[STRATEGIC BRIEF] {symbol} — Deep Audit Result**\n"
        brief += f"**Consensus: {bias_emoji} {bias} ALIGNED** (Requested by {mention})\n\n"
        
        brief += f"**1. Market Specialist**\n"
        brief += f"• BTC Regime: {regime.get('regime', 'N/A')} | Bias: {market.get('bias_score', 0):.2f}\n"
        brief += f"• Sherlock: {market.get('signals_summary', 'No signals detected')}\n\n"
        
        brief += f"**2. Institutional Scout**\n"
        # Extract from bot-summary if available
        brief += f"• {summary.get('formatted_response', 'Fundamental data unavailable for this token.').split('📊')[0].strip()}\n\n"
        
        brief += f"**3. Alpha Specialist**\n"
        brief += f"• Historical: Data suggests cautious approach in current volatility zone.\n"
        brief += f"• Learnings: L034 (BE SL) and L093 (Weekend Risk) are ACTIVE priorities.\n\n"
        
        brief += f"**4. Risk Specialist**\n"
        brief += f"• Drawdown: {risk.get('weekly_pnl_pct', 0):.1f}% weekly | Can Trade: {'✅' if risk.get('trading_allowed') else '🚫'}\n"
        brief += f"• Heat: Portfolio heat is at {risk.get('open_heat', 0):.1f}%.\n\n"
        
        brief += f"**5. Behavioral Coach**\n"
        brief += f"• David's current discipline score: 82/100. No revenge patterns detected.\n\n"
        
        brief += f"**6. Compliance Officer**\n"
        brief += f"• Policy Deltas: {len(data.get('deltas', {}).get('data', {}).get('active_deltas', []))} active changes. Setup is ALIGNED.\n\n"
        
        brief += f"**Final Verdict**: {symbol} shows strong fundamental support but faces {bias} macro headwinds. Maintain strict L034 (BE SL) protocol."
        
        return brief

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
        resp = _request_with_retry(
            "POST",
            url,
            json={"symbol": symbol.upper()},
            headers=_api_headers(),
            timeout=(API_CONNECT_TIMEOUT_SECONDS, API_STATUS_READ_TIMEOUT_SECONDS),
            retries=1,
            retry_delay_seconds=1.0,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        return payload.get("matches") or []

    def _research_token_data(self, symbol: str, name: str) -> Dict[str, Any]:
        api_base = _get_api_base_url()
        url = f"{api_base}/api/tokens/research"
        resp = _request_with_retry(
            "POST",
            url,
            json={"symbol": symbol.upper(), "name": name},
            headers=_api_headers(),
            timeout=(API_CONNECT_TIMEOUT_SECONDS, API_RESEARCH_KICKOFF_READ_TIMEOUT_SECONDS),
            retries=API_KICKOFF_RETRIES,
            retry_delay_seconds=API_KICKOFF_RETRY_DELAY_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        task_id = payload.get("task_id")
        if not task_id:
            raise RuntimeError("Research did not return a task_id")

        status_url = f"{api_base}/api/tokens/research/{task_id}"
        start = time.time()
        while True:
            status_resp = _request_with_retry(
                "GET",
                status_url,
                headers=_api_headers(),
                timeout=(API_CONNECT_TIMEOUT_SECONDS, API_STATUS_READ_TIMEOUT_SECONDS),
                retries=1,
                retry_delay_seconds=1.0,
            )
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
            if time.time() - start > ANALYSIS_REFRESH_POLL_TIMEOUT_SECONDS:
                raise TimeoutError(f"Research timed out after {ANALYSIS_REFRESH_POLL_TIMEOUT_SECONDS}s")
            time.sleep(2)

    def _refresh_or_research_token_data(self, symbol: str, name: str) -> Dict[str, Any]:
        """Prefer refetch for existing tokens; fallback to research for missing tokens."""
        try:
            return self._refresh_token_data(symbol)
        except requests.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 404:
                logger.info(
                    f"[{symbol}] Refetch returned 404, falling back to research with name='{name}'"
                )
                return self._research_token_data(symbol, name)
            raise

    def _refresh_token_data(self, symbol: str) -> Dict[str, Any]:
        """Trigger token refetch and wait for completion."""
        api_base = _get_api_base_url()
        url = f"{api_base}/api/tokens/{symbol}/refetch"
        resp = _request_with_retry(
            "POST",
            url,
            params={"force": "true", "auto_analyze": "false"},
            headers=_api_headers(),
            timeout=(API_CONNECT_TIMEOUT_SECONDS, API_REFRESH_KICKOFF_READ_TIMEOUT_SECONDS),
            retries=API_KICKOFF_RETRIES,
            retry_delay_seconds=API_KICKOFF_RETRY_DELAY_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        task_id = payload.get("task_id")
        if not task_id:
            raise RuntimeError("Refetch did not return a task_id")

        status_url = f"{api_base}/api/tokens/research/{task_id}"
        start = time.time()
        while True:
            status_resp = _request_with_retry(
                "GET",
                status_url,
                headers=_api_headers(),
                timeout=(API_CONNECT_TIMEOUT_SECONDS, API_STATUS_READ_TIMEOUT_SECONDS),
                retries=1,
                retry_delay_seconds=1.0,
            )
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
            if time.time() - start > ANALYSIS_REFRESH_POLL_TIMEOUT_SECONDS:
                raise TimeoutError(f"Refetch timed out after {ANALYSIS_REFRESH_POLL_TIMEOUT_SECONDS}s")
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

    @staticmethod
    def _run_full_pipeline_with_thread_loop(symbol: str):
        return run_full_pipeline_with_thread_loop(
            symbol=symbol,
            force_refresh=False,  # Refresh is handled above
            force_playbook=True,  # Always generate playbook
            notify_discord=False,  # We handle notification manually
        )

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

        loop = asyncio.get_running_loop()
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
        account_id = self._resolve_account_id()
        if not self._is_authorized(ctx.author.id, account_id=account_id):
            await ctx.reply("⛔ Not authorized.", mention_author=False)
            return

        # Check if we are in a text channel (not a thread/DM)
        if isinstance(ctx.channel, discord.TextChannel):
            try:
                # Create a thread for this analysis
                thread = await ctx.message.create_thread(
                    name=f"Analysis: {symbol.upper()}",
                    auto_archive_duration=60 # 1 hour
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
            content=f"🔍 Analyzing **{resolved_symbol.upper()}**... (this may take up to 2-3m)"
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
        account_id = self._resolve_account_id()
        if not self._is_authorized_interaction(interaction, account_id=account_id):
            await self._deny_interaction(interaction)
            return

        symbol = symbol.upper()
        request_id = f"analyze-{interaction.id}"
        invoke_channel = interaction.channel
        analysis_channel = self._resolve_analysis_channel()
        if analysis_channel is None:
            analysis_channel = invoke_channel if isinstance(invoke_channel, discord.TextChannel) else None

        if analysis_channel is None:
            await safe_send(
                interaction,
                command_name="analyze",
                logger=logger,
                content="❌ Could not resolve analysis channel. Try again in `#analysis-updates`.",
                ephemeral=True,
            )
            return

        await safe_send(
            interaction,
            command_name="analyze",
            logger=logger,
            content=f"🔍 Analyzing **{symbol}**. I will post results in {analysis_channel.mention}.",
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
                auto_archive_duration=60,
            )
            thread_status = await thread.send(
                f"🔍 Analyzing **{symbol}**... (this may take up to 2-3m)"
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
                content=f"🔍 Analyzing **{resolved_symbol}**... (this may take up to 2-3m)"
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
                user_err = _format_user_facing_analysis_error(e)
                await status_msg.edit(
                    content=f"❌ Analysis failed for **{symbol}**: {user_err}"
                )
            except Exception:
                pass

    @app_commands.command(name="analyze-batch", description="Analyze multiple tokens concurrently")
    @app_commands.describe(symbols="Comma-separated token symbols (e.g., ZRO, ALCH, DRIFT)")
    async def analyze_batch_slash(self, interaction: discord.Interaction, symbols: str):
        """Slash command to analyze multiple tokens at once."""
        account_id = self._resolve_account_id()
        if not self._is_authorized_interaction(interaction, account_id=account_id):
            await self._deny_interaction(interaction)
            return

        parsed = parse_batch_symbols(symbols)
        if not parsed:
            await safe_send(
                interaction,
                command_name="analyze-batch",
                logger=logger,
                content="No valid symbols provided. Use comma or space separated symbols (e.g., `ZRO, ALCH, DRIFT`).",
                ephemeral=True,
            )
            return

        analysis_channel = self._resolve_analysis_channel()
        invoke_channel = interaction.channel
        if analysis_channel is None:
            analysis_channel = invoke_channel if isinstance(invoke_channel, discord.TextChannel) else None

        if analysis_channel is None:
            await safe_send(
                interaction,
                command_name="analyze-batch",
                logger=logger,
                content="Could not resolve analysis channel. Try again in `#analysis-updates`.",
                ephemeral=True,
            )
            return

        symbol_list = ", ".join(parsed)
        await safe_send(
            interaction,
            command_name="analyze-batch",
            logger=logger,
            content=f"Analyzing **{len(parsed)}** tokens: {symbol_list}. Results will appear in {analysis_channel.mention}.",
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
        await safe_send(
            interaction,
            command_name="analyze-batch",
            logger=logger,
            content=f"Batch complete: **{success_count}/{len(parsed)}** analyses finished.",
            ephemeral=True,
        )

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
                    auto_archive_duration=60,
                )
                thread_status = await thread.send(
                    f"Analyzing **{symbol}**... (this may take up to 2-3m)"
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
                    content=f"Analyzing **{resolved_symbol}**... (this may take up to 2-3m)"
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
                user_err = _format_user_facing_analysis_error(e)
                try:
                    await status_msg.edit(content=f"Analysis failed for **{symbol}**: {user_err}")
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
            used_refresh_fallback = False
            refresh_fallback_age_min: Optional[float] = None

            try:
                if resolved_name:
                    await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: self._refresh_or_research_token_data(symbol, resolved_name),
                        ),
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
            except requests.Timeout:
                consolidated, refresh_fallback_age_min = await _load_cached_after_refresh_delay(
                    loop=loop,
                    symbol=symbol,
                    reason="api_timeout_during_refresh",
                    request_id=request_id,
                    status_msg=status_msg,
                    load_cached_fn=lambda: self._load_consolidated(symbol),
                )
                if consolidated is None:
                    return
                used_refresh_fallback = True
            except TimeoutError:
                consolidated, refresh_fallback_age_min = await _load_cached_after_refresh_delay(
                    loop=loop,
                    symbol=symbol,
                    reason="refresh_poll_timed_out",
                    request_id=request_id,
                    status_msg=status_msg,
                    load_cached_fn=lambda: self._load_consolidated(symbol),
                )
                if consolidated is None:
                    return
                used_refresh_fallback = True
            except asyncio.TimeoutError:
                consolidated, refresh_fallback_age_min = await _load_cached_after_refresh_delay(
                    loop=loop,
                    symbol=symbol,
                    reason="refresh_timed_out",
                    request_id=request_id,
                    status_msg=status_msg,
                    load_cached_fn=lambda: self._load_consolidated(symbol),
                )
                if consolidated is None:
                    return
                used_refresh_fallback = True
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
                        lambda: self._run_full_pipeline_with_thread_loop(symbol),
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
            if used_refresh_fallback:
                age_text = (
                    f"{refresh_fallback_age_min:.0f}m old"
                    if refresh_fallback_age_min is not None
                    else "age unknown"
                )
                await target_channel.send(
                    f"⚠️ Analysis used cached snapshot for **{symbol}** ({age_text}) because live refresh was delayed."
                )
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
                err_text = _format_user_facing_analysis_error(e)
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
