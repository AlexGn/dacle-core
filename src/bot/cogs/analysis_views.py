"""
Trade Approval View
Discord UI components (buttons) for approving or vetoing trade candidates.
"""

import json
import os
import hashlib
from pathlib import Path
from typing import Any, Optional

import aiohttp
import discord

from src.utils.logger import get_logger
from src.utils.lifecycle_id import generate_lifecycle_id
from src.utils.lifecycle_store import record_setup
from src.bot.runtime_routing import get_bot_api_base_url, get_channel_id

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TOKENS_DIR = PROJECT_ROOT / "data" / "tokens"
TRADES_CHANNEL_ID = get_channel_id("trades")
API_BASE_URL = get_bot_api_base_url()


def _api_headers() -> dict:
    api_key = os.getenv("DACLE_API_KEY", "").strip()
    return {"X-API-Key": api_key} if api_key else {}


def _load_execution_state(symbol: str, direction: str) -> Optional[dict]:
    """Load playbook execution state for a token."""
    token_dir = TOKENS_DIR / symbol.upper()
    playbooks_dir = token_dir / "playbooks"
    if not playbooks_dir.exists():
        return None

    candidates = [
        playbooks_dir / f"{symbol.upper()}_{direction.lower()}_execution_state.json",
        playbooks_dir / f"{symbol.upper()}_execution_state.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                continue
    return None


def _load_discord_levels(symbol: str, direction: str) -> Optional[dict]:
    """Load David's manual levels from /levels command.

    Reads from discord_levels.json audit trail (survives data consolidation),
    with fallback to consolidated.json fields.
    Returns dict with {entry, stop_loss, target} or None if not found/expired.
    """
    from datetime import datetime, timezone
    token_dir = TOKENS_DIR / symbol.upper()

    # Primary: read from discord_levels.json audit trail (not wiped by consolidator)
    audit_path = token_dir / "discord_levels.json"
    if audit_path.exists():
        try:
            audit_list = json.loads(audit_path.read_text())
            if isinstance(audit_list, list) and audit_list:
                # Get most recent entry matching direction
                for raw in reversed(audit_list):
                    # Audit trail uses "recommendation" field, not "direction"
                    entry_dir = raw.get("direction", "") or raw.get("recommendation", "")
                    if entry_dir.upper() != direction.upper():
                        continue
                    expires = raw.get("expires_at", "")
                    if expires:
                        exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                        if exp_dt <= datetime.now(timezone.utc):
                            continue
                    # Normalize audit trail format to canonical {entry, stop_loss, target}
                    entry_val = raw.get("entry")
                    if entry_val is None:
                        entry_levels = raw.get("entry_levels", [])
                        entry_val = entry_levels[0] if entry_levels else None
                    target_val = raw.get("target")
                    if target_val is None:
                        take_profits = raw.get("take_profits", [])
                        target_val = take_profits[0] if take_profits else None
                    return {
                        "direction": direction.upper(),
                        "entry": entry_val,
                        "stop_loss": raw.get("stop_loss"),
                        "target": target_val,
                        "expires_at": expires,
                    }
        except Exception:
            pass

    # Fallback: consolidated.json (may be stale if /analyze ran after /levels)
    consolidated_path = token_dir / "consolidated.json"
    if consolidated_path.exists():
        try:
            cdata = json.loads(consolidated_path.read_text())
            dl = cdata.get("latest_discord_levels") or cdata.get("latest_discord_setup")
            if not dl or dl.get("direction", "").upper() != direction.upper():
                return None
            expires = dl.get("expires_at", "")
            if expires:
                exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if exp_dt <= datetime.now(timezone.utc):
                    return None
            return dl
        except Exception:
            pass

    return None


def _format_setup_message(symbol: str, direction: str, exec_state: dict) -> str:
    """Format a canonical setup message from execution state or discord levels."""
    # Check for David's manual /levels first
    discord_levels = _load_discord_levels(symbol, direction)
    if discord_levels:
        entry = discord_levels.get("entry")
        stop_loss = discord_levels.get("stop_loss")
        target = discord_levels.get("target")
        parts = [f"TAKE {direction.upper()} ${symbol.upper()}"]
        if entry:
            parts.append(f"Entry: {entry}")
        if stop_loss:
            parts.append(f"SL: {stop_loss}")
        if target:
            parts.append(f"Target: {target}")
        return "\n".join(parts)

    # Fall back to playbook execution levels
    levels = exec_state.get("execution_levels", {})
    entry_low = levels.get("entry_low")
    entry_high = levels.get("entry_high")
    stop_loss = levels.get("stop_loss")
    target = levels.get("target_1")

    entry_str = ""
    if entry_low and entry_high:
        entry_str = f"Entry: {entry_low} - {entry_high}"
    elif entry_low:
        entry_str = f"Entry: {entry_low}"

    parts = [f"TAKE {direction.upper()} ${symbol.upper()}"]
    if entry_str:
        parts.append(entry_str)
    if stop_loss:
        parts.append(f"SL: {stop_loss}")
    if target:
        parts.append(f"Target: {target}")

    return "\n".join(parts)


class SetLevelsModal(discord.ui.Modal):
    """Modal for entering Entry/SL/TP levels from the /analyze result."""

    def __init__(self, symbol: str, direction: str):
        title = f"Set Levels — {symbol} {direction}"
        if len(title) > 45:
            title = title[:45]
        super().__init__(title=title)
        self.symbol = symbol
        self.direction = direction

        self.entry_input = discord.ui.TextInput(
            label="Entry Price",
            placeholder="e.g. 1.28",
            required=True,
            style=discord.TextStyle.short,
        )
        self.sl_input = discord.ui.TextInput(
            label="Stop Loss",
            placeholder="e.g. 1.50",
            required=True,
            style=discord.TextStyle.short,
        )
        self.tp_input = discord.ui.TextInput(
            label="Take Profit (optional)",
            placeholder="e.g. 0.85",
            required=False,
            style=discord.TextStyle.short,
        )
        self.add_item(self.entry_input)
        self.add_item(self.sl_input)
        self.add_item(self.tp_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            entry = float(self.entry_input.value.strip())
            sl = float(self.sl_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "Entry and SL must be valid numbers.", ephemeral=True
            )
            return

        tp = None
        tp_str = self.tp_input.value.strip()
        if tp_str:
            try:
                tp = float(tp_str)
            except ValueError:
                await interaction.response.send_message(
                    "Take Profit must be a valid number.", ephemeral=True
                )
                return

        await interaction.response.defer(ephemeral=False)

        payload = {
            "token": self.symbol,
            "direction": self.direction,
            "entry": entry,
            "sl": sl,
        }
        if tp is not None:
            payload["tp"] = tp

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{API_BASE_URL}/api/execution/levels"
                async with session.post(url, json=payload, headers=_api_headers()) as response:
                    if response.status == 422:
                        error_data = await response.json()
                        detail = error_data.get("detail", "Validation error")
                        await interaction.followup.send(f"{detail}")
                        return
                    if response.status != 200:
                        await interaction.followup.send(
                            f"API error ({response.status}). Check DACLE API logs."
                        )
                        return
                    api_result = await response.json()
        except Exception as e:
            logger.error(f"Set Levels API call failed: {e}")
            await interaction.followup.send("DACLE API unavailable.")
            return

        ptc = api_result.get("pre_trade_check") or {}
        confluence = api_result.get("confluence") or {}
        rr_ratio = api_result.get("rr_ratio", 0)
        approved = ptc.get("approved", False) if isinstance(ptc, dict) else False
        formatted = api_result.get("formatted_response") or ""

        status_emoji = "\u2705" if approved else "\U0001f6d1"
        status_word = "APPROVED" if approved else "BLOCKED"
        color = discord.Color.green() if approved else discord.Color.red()

        embed = discord.Embed(
            title=f"{status_emoji} {self.symbol} {self.direction} — {status_word}",
            color=color,
        )

        conf_count = confluence.get("confluence_count", 0)
        conf_sources = confluence.get("matching_sources", [])
        conf_quality = confluence.get("quality", "NO_DATA")
        display_sources = [s for s in conf_sources if s not in ("sl_confirmed", "tp1_confirmed")]
        if conf_count > 0:
            conf_line = f"{conf_count} confluences ({', '.join(display_sources)}) — {conf_quality}"
        else:
            conf_line = f"0 confluences — {conf_quality}"
        embed.add_field(name="Confluence", value=conf_line, inline=False)

        embed.add_field(name="Entry", value=f"${entry:g}", inline=True)
        embed.add_field(name="SL", value=f"${sl:g}", inline=True)
        if tp is not None:
            embed.add_field(name="TP", value=f"${tp:g}", inline=True)
            embed.add_field(name="R:R", value=f"{rr_ratio}:1", inline=True)
        else:
            embed.add_field(name="TP", value="Not set", inline=True)

        if formatted:
            lines = formatted.split("\n")
            if lines and ("APPROVED" in lines[0] or "BLOCKED" in lines[0]):
                formatted = "\n".join(lines[1:]).lstrip("\n")
            if len(formatted) > 4000:
                formatted = formatted[:3997] + "..."
            embed.description = formatted

        embed.set_footer(text="Levels stored (expires 24h)")
        await interaction.followup.send(embed=embed)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"SetLevelsModal error: {error}", exc_info=error)
        try:
            await interaction.response.send_message(
                "An error occurred processing your levels.", ephemeral=True
            )
        except Exception:
            pass


class TradeApprovalView(discord.ui.View):
    """
    Interactive buttons for #analysis-updates candidates.
    """
    def __init__(self, symbol: str, conviction: float, direction: Optional[str] = None):
        super().__init__(timeout=86400)  # 24h timeout
        self.symbol = symbol
        self.conviction = conviction
        self.direction = direction
        self._executed = False

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        direction = self.direction or "SHORT"
        token_upper = self.symbol.upper()
        if self._executed:
            if interaction.response.is_done():
                await interaction.followup.send("⚠️ Already processing this approval.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ Already processing this approval.", ephemeral=True)
            return
        self._executed = True

        # Disable controls before backend call so concurrent clicks are inert.
        for child in self.children:
            child.disabled = True
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(view=self)
            else:
                await interaction.response.edit_message(view=self)
        except Exception as e:
            logger.warning("Failed to pre-disable approval buttons: %s", e)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=False)

        # 2. Load playbook execution state
        exec_state = _load_execution_state(self.symbol, direction)
        if not exec_state:
            await interaction.followup.send(
                f"✅ **Trade Approved**: No playbook found for **{self.symbol}** {direction}. "
                f"Run `/setup {self.symbol} {direction}` to post a setup to #trades.",
                ephemeral=False,
            )
            return

        levels = exec_state.get("execution_levels", {})
        entry = levels.get("entry") or levels.get("entry_low")
        sl = levels.get("stop_loss")
        tp = levels.get("target_1")

        if not all([entry, sl, tp]):
            await interaction.followup.send(f"⚠️ **Approval Aborted**: Setup levels incomplete for **{self.symbol}**.")
            return

        # 3. Call v2 Approve-and-Execute Orchestration
        from api.routers.execution_v2 import approve_and_execute_v2
        from src.execution.v2_models import ApproveAndExecuteRequestV2, ExecutionState

        account_id = str(os.getenv("EXECUTION_DEFAULT_ACCOUNT_ID", "primary") or "").strip() or "primary"
        # Deterministic key prevents duplicate orders from repeated button clicks/retries.
        key_material = (
            f"{account_id}:{interaction.message.id}:{interaction.user.id}:"
            f"{token_upper}:{direction}:{entry}:{sl}:{tp}"
        )
        idempotency_key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:32]
        
        request = ApproveAndExecuteRequestV2(
            setup_id=f"discord_{interaction.message.id}",
            account_id=account_id,
            approval_id=str(interaction.user.id),
            idempotency_key=idempotency_key,
            symbol=token_upper,
            side=direction.lower(),
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            size_usd=1500.0, # Default Phase 2 size
            dry_run=True # Force Dry-Run for initial rollout
        )

        try:
            result = await approve_and_execute_v2(request)
            
            if result.state == ExecutionState.VETOED:
                reasons = ", ".join([r.value for r in result.veto_reasons])
                await interaction.followup.send(
                    f"🚫 **TRADE VETOED** (Freshness Snap Failed)\n"
                    f"**Reason**: {reasons}\n"
                    f"**Drift**: {result.revalidation_snapshot.price_drift_pct:.2f}%\n"
                    f"**Action**: Setup invalidated by market movement. Wait for next cluster.",
                    ephemeral=False
                )
                return

            if result.state == ExecutionState.SUBMITTED:
                order_id = result.order_ids[0] if result.order_ids else "DRY_RUN"
                await interaction.followup.send(
                    f"🚀 **EXECUTION SUBMITTED** (v2 Bridge)\n"
                    f"**Token**: {token_upper} {direction}\n"
                    f"**Price**: {entry}\n"
                    f"**Order ID**: `{order_id}`\n"
                    f"**Effective Size**: ${result.effective_size_usd:.0f}\n"
                    f"**Status**: {'[DRY RUN - No real order]' if request.dry_run else '[LIVE]'}",
                    ephemeral=False
                )
                
        except Exception as e:
            logger.error(f"Execution orchestration failed: {e}")
            await interaction.followup.send(f"❌ **System Error**: Execution failed during revalidation: {e}")

    @discord.ui.button(label="Set Levels", style=discord.ButtonStyle.blurple, emoji="\U0001f4d0")
    async def set_levels(self, interaction: discord.Interaction, button: discord.ui.Button):
        direction = self.direction or "SHORT"
        modal = SetLevelsModal(self.symbol, direction)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.grey, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{API_BASE_URL}/api/tokens/research/{self.symbol}/refresh"
                async with session.post(url, headers=_api_headers()) as resp:
                    if resp.status in (200, 202):
                        await interaction.followup.send(
                            f"🔄 Data refresh triggered for **{self.symbol}**. "
                            f"Re-run `/analyze {self.symbol}` in ~30s for updated results.",
                            ephemeral=True
                        )
                    elif resp.status == 423:
                        await interaction.followup.send(
                            f"🔒 Refresh already in progress for **{self.symbol}**. "
                            f"Try `/analyze {self.symbol}` in ~30s.",
                            ephemeral=True
                        )
                    else:
                        await interaction.followup.send(
                            f"❌ Refresh failed ({resp.status}). Try `/analyze {self.symbol}` manually.",
                            ephemeral=True
                        )
        except Exception as e:
            logger.error(f"Refresh failed for {self.symbol}: {e}")
            await interaction.followup.send(
                f"❌ Refresh failed: {e}",
                ephemeral=True
            )

    @discord.ui.button(label="Veto", style=discord.ButtonStyle.red, emoji="❌")
    async def veto(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"❌ **Trade Vetoed**: {self.symbol} moved to archive.",
            ephemeral=False
        )
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)


class AuditExecutionView(discord.ui.View):
    """
    Unified Execution View for #audit-token.
    Allows David to execute a trade directly from the strategic brief.
    """
    def __init__(self, symbol: str, direction: str, conviction: float):
        super().__init__(timeout=86400)
        self.symbol = symbol.upper()
        self.direction = direction.upper()
        self.conviction = conviction

    @discord.ui.button(label="CONFIRM EXECUTION", style=discord.ButtonStyle.green, emoji="🛡️")
    async def confirm_execution(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Final execution trigger: posts setup and engages the Watcher."""
        await interaction.response.defer(ephemeral=False)
        
        # 1. Load Setup Data
        exec_state = _load_execution_state(self.symbol, self.direction)
        if not exec_state:
            # Fallback to general TA if playbook missing
            ta_path = TOKENS_DIR / self.symbol / "ta" / "latest.json"
            if ta_path.exists():
                try:
                    ta_data = json.loads(ta_path.read_text())
                    exec_state = {
                        "execution_levels": {
                            "entry_low": ta_data.get("entry_price"),
                            "stop_loss": ta_data.get("stop_loss"),
                            "target_1": ta_data.get("target_price")
                        }
                    }
                except: pass

        if not exec_state:
            await interaction.followup.send(f"❌ Cannot execute: No technical setup found for ${self.symbol}.")
            return

        setup_msg = _format_setup_message(self.symbol, self.direction, exec_state)

        # Generate lifecycle_id for audit execution
        lifecycle_id = generate_lifecycle_id(self.symbol, self.direction)
        try:
            record_setup(lifecycle_id, self.symbol, self.direction)
        except Exception as e:
            logger.warning(f"Failed to record lifecycle for audit execution: {e}")

        # 2. Post to #trades
        trades_channel = interaction.client.get_channel(TRADES_CHANNEL_ID)
        if trades_channel:
            sent_msg = await trades_channel.send(f"🚀 **EXECUTION CONFIRMED** (via Audit)\n{setup_msg}")
            
            # 3. Engage The Watcher (Ping the trigger)
            await trades_channel.send(f"🛡️ **WATCHER ENGAGED**: Capital protection active for ${self.symbol} {self.direction}. 4h persistent monitoring enabled.")
            
            # 4. Record 'OPEN' feedback automatically to start tracking
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{API_BASE_URL}/api/feedback/simplified-submit"
                    payload = {
                        "token": self.symbol,
                        "result": "WATCHING", # Special status for active trades
                        "key_feedback": f"Auto-executed via Deep Audit. Conviction: {self.conviction}/10"
                    }
                    async with session.post(url, data=payload, headers=_api_headers()) as resp:
                        pass
            except: pass

            await interaction.followup.send(f"✅ **Trade Executed!** Setup posted to <#{TRADES_CHANNEL_ID}> and Watcher engaged.")
        else:
            await interaction.followup.send(f"❌ Failed to find #trades channel.")

        # Disable button after use
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)
