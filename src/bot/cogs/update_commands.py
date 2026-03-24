"""Manual refresh slash command for on-demand futures/discovery updates."""

from __future__ import annotations

import os
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from scripts.cron.daily_futures_movers import format_discord_message
from src.bot.utils.safe_task import safe_create_task
from src.ops.discord_channel_contract import get_discord_channel_contract
from src.bot.runtime_routing import get_bot_api_base_url
from src.utils.logger import get_logger

# Direct backend logic imports
from src.ops.futures_refresh import (
    request_manual_refresh,
    get_manual_refresh_run,
    get_latest_manual_refresh_run,
)
from api.routers.macro import get_market_direction, get_long_alignment
from api.routers.futures import get_latest_futures_movers

logger = get_logger(__name__)

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "TIMED_OUT"}


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_utc(value: Any) -> str:
    dt = _parse_iso(value)
    if not dt:
        return "unknown"
    return dt.astimezone(timezone.utc).strftime("%b %d, %Y %H:%M UTC")


class UpdateCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.owner_id = self._parse_int(os.getenv("DISCORD_OWNER_ID"))
        self.discovery_channel_id = self._resolve_discovery_channel_id()
        self.poll_interval_seconds = self._parse_int(
            os.getenv("MANUAL_REFRESH_POLL_INTERVAL_SECONDS"), default=5
        )
        self.poll_timeout_seconds = self._parse_int(
            os.getenv("MANUAL_REFRESH_MAX_RUNTIME_SECONDS"), default=2400
        ) + 120
        self._watcher_keys: set[str] = set()

    @staticmethod
    def _parse_int(value: Optional[str], default: int = 0) -> int:
        if not value:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_float(value: Optional[str], default: float = 0.0) -> float:
        if not value:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _resolve_discovery_channel_id() -> Optional[int]:
        try:
            contract = get_discord_channel_contract()
            cid = contract.id_for("discovery")
            return int(cid)
        except Exception:
            return None

    def _is_owner(self, user_id: int) -> bool:
        return self.owner_id is not None and user_id == self.owner_id

    @staticmethod
    def _looks_like_discovery_channel(name: Optional[str]) -> bool:
        return bool(name and "discovery" in name.lower())

    def _is_discovery_context(self, interaction: discord.Interaction) -> bool:
        if self.discovery_channel_id is not None and interaction.channel_id == self.discovery_channel_id:
            return True

        channel = interaction.channel
        if channel is None:
            return False

        if self._looks_like_discovery_channel(getattr(channel, "name", None)):
            return True

        if isinstance(channel, discord.Thread):
            if (
                self.discovery_channel_id is not None
                and channel.parent_id == self.discovery_channel_id
            ):
                return True
            parent = channel.parent
            if self._looks_like_discovery_channel(getattr(parent, "name", None)):
                return True

        return False

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        if self._is_owner(interaction.user.id):
            return True
        guild = getattr(interaction, "guild", None)
        if guild is not None and getattr(guild, "owner_id", None) == interaction.user.id:
            return True
        perms = getattr(interaction.user, "guild_permissions", None)
        if bool(getattr(perms, "administrator", False)):
            return True
        return self._is_discovery_context(interaction)

    @staticmethod
    def _normalize_refresh_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Accept legacy and envelope-style responses for refresh endpoints."""
        if not isinstance(payload, dict):
            return {}

        data = payload.get("data")
        if isinstance(data, dict):
            merged = dict(payload)
            for key in ("request_status", "run", "cooldown_until", "remaining_seconds", "message", "reason"):
                if key in data and key not in merged:
                    merged[key] = data[key]
            return merged

        return payload

    @staticmethod
    def _resolve_request_status(payload: Dict[str, Any]) -> str:
        """Resolve request status from explicit fields or legacy run-only payloads."""
        explicit = payload.get("request_status")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip().lower()

        status = payload.get("status")
        if isinstance(status, str):
            lowered = status.strip().lower()
            if lowered in {"started", "already_running", "cooldown", "blocked"}:
                return lowered

        run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
        run_status = str(run.get("request_status") or run.get("status") or "").strip().lower()
        if run_status in {"started", "already_running", "cooldown", "blocked"}:
            return run_status
        if run_status == "running":
            return "already_running"

        remaining_raw = payload.get("remaining_seconds")
        if remaining_raw is None:
            remaining_raw = run.get("remaining_cooldown_seconds")
        try:
            if int(remaining_raw or 0) > 0:
                return "cooldown"
        except (TypeError, ValueError):
            pass

        cooldown_until = _parse_iso(payload.get("cooldown_until") or run.get("cooldown_until"))
        if cooldown_until and cooldown_until > datetime.now(timezone.utc):
            return "cooldown"

        reason = payload.get("reason")
        if isinstance(reason, str) and reason.strip():
            if "block" in reason.lower():
                return "blocked"

        return ""

    @staticmethod
    def _extract_error_detail(payload: Dict[str, Any]) -> str:
        for key in ("error", "detail", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _remaining_cooldown_seconds(run: Dict[str, Any]) -> int:
        try:
            remaining = int(run.get("remaining_cooldown_seconds") or 0)
        except (TypeError, ValueError):
            remaining = 0
        if remaining > 0:
            return remaining
        cooldown_until = _parse_iso(run.get("cooldown_until"))
        if not cooldown_until:
            return 0
        return int(max(0.0, (cooldown_until - datetime.now(timezone.utc)).total_seconds()))

    async def _defer_interaction(self, interaction: discord.Interaction) -> bool:
        try:
            if interaction.response.is_done():
                return True
            await interaction.response.defer(ephemeral=False, thinking=True)
            return True
        except discord.NotFound as e:
            logger.warning("/discovery interaction expired before defer: %s", e)
            return False
        except discord.HTTPException as e:
            logger.warning("/discovery defer failed: %s", e)
            return False

    async def _send_followup(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool = False,
    ) -> bool:
        try:
            await interaction.followup.send(content, ephemeral=ephemeral)
            return True
        except discord.NotFound as e:
            logger.warning("/discovery followup failed (interaction expired): %s", e)
            return False
        except discord.HTTPException as e:
            logger.warning("/discovery followup failed: %s", e)
            return False

    async def _handle_start_failure(self, interaction: discord.Interaction, error_detail: str = "") -> None:
        try:
            # Direct backend logic call instead of HTTP GET
            run = get_latest_manual_refresh_run()
        except Exception as e:
            logger.warning(f"Failed to fetch latest manual refresh run: {e}")
            run = None

        if isinstance(run, dict):
            status = str(run.get("status") or "").upper()
            if status == "RUNNING":
                await self._send_followup(interaction, self._already_running_message(run))
                return

            if self._remaining_cooldown_seconds(run) > 0:
                await self._send_followup(interaction, self._cooldown_message(run, run))
                channel = interaction.channel
                if channel is not None:
                    report = await self._build_discovery_report_message()
                    if report:
                        await self._send_report_chunks(
                            channel,
                            report,
                            prefix="📌 Latest available Futures Movers report (refresh cooldown active):",
                        )
                return

        msg = "❌ Failed to start refresh. API unavailable or rejected request."
        if error_detail:
            msg += f"\n`Detail: {error_detail}`"
            
        await self._send_followup(
            interaction,
            msg,
            ephemeral=True,
        )

    def _started_message(self, run: Dict[str, Any]) -> str:
        return (
            f"🔄 **/discovery started**\n"
            f"Run: `{run.get('run_id', '?')}`\n"
            f"Started: {_fmt_utc(run.get('started_at'))}\n"
            f"Status: {run.get('status', 'RUNNING')}"
        )

    def _already_running_message(self, run: Dict[str, Any]) -> str:
        return (
            f"⏳ A refresh is already running.\n"
            f"Run: `{run.get('run_id', '?')}`\n"
            f"Started: {_fmt_utc(run.get('started_at'))}\n"
            f"Status: {run.get('status', 'RUNNING')}"
        )

    def _cooldown_message(self, run: Dict[str, Any], payload: Dict[str, Any]) -> str:
        remaining = int(payload.get("remaining_seconds") or run.get("remaining_cooldown_seconds") or 0)
        mins = max(1, remaining // 60) if remaining else 0
        return (
            f"🕒 Refresh cooldown is active.\n"
            f"Last run: `{run.get('run_id', '?')}` ({run.get('status', 'unknown')})\n"
            f"Cooldown until: {_fmt_utc(payload.get('cooldown_until') or run.get('cooldown_until'))}\n"
            f"Try again in about {mins} minute(s)."
        )

    def _blocked_message(self, payload: Dict[str, Any]) -> str:
        reason = str(payload.get("reason") or "unknown_block").strip()
        detail = str(payload.get("message") or "Manual refresh is temporarily blocked.").strip()
        return (
            "🚫 Manual refresh is currently blocked.\n"
            f"Reason: `{reason}`\n"
            f"{detail}"
        )

    def _as_setup_obj(self, raw: Dict[str, Any]) -> Optional[SimpleNamespace]:
        symbol = raw.get("symbol")
        if not symbol:
            return None
        defaults = {
            "setup_type": "MONITOR",
            "setup_score": 0.0,
            "move_direction": "PUMP",
            "change_24h_pct": 0.0,
            "volume_24h_usd": 0.0,
            "price": 0.0,
            "funding_rate": None,
            "ta_bias": "NEUTRAL",
            "ta_confidence": 0.0,
            "rsi_14": 50.0,
            "market_structure": "unknown",
            "continuation_score": 0.0,
            "reversal_risk_score": 0.0,
            "decision_label": "MONITOR",
            "agent_verdict": "UNSET",
            "agent_confidence": 0.0,
            "risk_flags": [],
            "prep_actions": [],
            "score_breakdown": {},
            "reasoning": "",
        }
        payload = {"symbol": symbol, **defaults, **raw}
        try:
            payload["setup_score"] = float(payload.get("setup_score") or 0.0)
            payload["change_24h_pct"] = float(payload.get("change_24h_pct") or 0.0)
            payload["volume_24h_usd"] = float(payload.get("volume_24h_usd") or 0.0)
            payload["price"] = float(payload.get("price") or 0.0)
            payload["ta_confidence"] = float(payload.get("ta_confidence") or 0.0)
            payload["rsi_14"] = float(payload.get("rsi_14") or 50.0)
            payload["continuation_score"] = float(payload.get("continuation_score") or payload["setup_score"])
            payload["reversal_risk_score"] = float(payload.get("reversal_risk_score") or 0.0)
            payload["agent_confidence"] = float(payload.get("agent_confidence") or 0.0)
            if payload.get("funding_rate") is not None:
                payload["funding_rate"] = float(payload["funding_rate"])
        except (TypeError, ValueError):
            return None
        return SimpleNamespace(**payload)

    async def _build_discovery_report_message(self) -> Optional[str]:
        try:
            # Direct backend logic calls instead of HTTP GET
            movers = await get_latest_futures_movers()
            macro = await get_market_direction()
            long_alignment = await get_long_alignment()
        except Exception as e:
            logger.warning(f"Failed to fetch backend data for discovery report: {e}")
            return None

        setup_objs = []
        for raw in movers.get("setups") or []:
            if not isinstance(raw, dict):
                continue
            setup = self._as_setup_obj(raw)
            if setup is not None:
                setup_objs.append(setup)
        if not setup_objs:
            return None

        scan_metadata = {
            "total_pairs_scanned": movers.get("total_pairs_scanned", "?"),
            "movers_found": movers.get("movers_found", len(setup_objs)),
            "ta_analyzed": movers.get("ta_analyzed", len(setup_objs)),
            "board_size": 5,
        }
        regime = {
            "bias": macro.get("bias", "UNKNOWN"),
            "confidence_pct": int(macro.get("confidence_pct") or 0),
            "score": float(macro.get("score") or 0.0),
            "timestamp": macro.get("timestamp"),
            "stale": False,
            "source": "live_api",
            "l088_aligned": bool(long_alignment.get("aligned", True)),
        }

        try:
            return format_discord_message(
                setup_objs,
                scan_metadata,
                regime=regime,
                prepared=None,
            )
        except Exception as e:
            logger.warning(f"Failed to format full discovery report for /discovery: {e}")
            return None

    @staticmethod
    def _chunk_discord_message(text: str, limit: int = 1900) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks = []
        current = []
        current_len = 0
        for line in text.splitlines(keepends=True):
            if current_len + len(line) > limit and current:
                chunks.append("".join(current).rstrip())
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += len(line)
        if current:
            chunks.append("".join(current).rstrip())
        return chunks

    async def _send_report_chunks(
        self,
        channel: discord.abc.Messageable,
        report: str,
        prefix: Optional[str] = None,
    ) -> None:
        chunks = self._chunk_discord_message(report)
        if not chunks:
            return
        if prefix:
            await channel.send(prefix)
        for chunk in chunks:
            await channel.send(chunk)

    def _build_failure_message(self, run: Dict[str, Any]) -> str:
        return (
            f"❌ **/discovery failed**\n"
            f"Run: `{run.get('run_id', '?')}`\n"
            f"Status: {run.get('status', 'FAILED')}\n"
            f"Error: {run.get('error') or 'unknown'}\n"
            f"Log: `{run.get('log_path') or 'unknown'}`"
        )

    async def _watch_run_completion(self, run_id: str, channel: discord.abc.Messageable) -> None:
        deadline = datetime.now(timezone.utc).timestamp() + float(self.poll_timeout_seconds)
        while datetime.now(timezone.utc).timestamp() < deadline:
            try:
                # Direct backend logic call instead of HTTP GET
                run_raw = get_manual_refresh_run(run_id)
                payload = self._normalize_refresh_payload({"run": run_raw} if run_raw else {})
            except Exception as e:
                logger.warning(f"Failed to poll manual refresh status for {run_id}: {e}")
                payload = {}

            if payload and isinstance(payload.get("run"), dict):
                run = payload["run"]
                status = str(run.get("status") or "").upper()
                if status in TERMINAL_STATUSES:
                    if status == "COMPLETED":
                        report = await self._build_discovery_report_message()
                        if report:
                            await self._send_report_chunks(channel, report)
                        else:
                            await channel.send(
                                f"✅ **/discovery completed**\n"
                                f"Run: `{run.get('run_id', '?')}` in {run.get('duration_seconds', '?')}s\n"
                                "⚠️ Could not render full discovery report. "
                                "Use `/scan` for current snapshot."
                            )
                    else:
                        await channel.send(self._build_failure_message(run))
                    return
            await asyncio.sleep(max(2, self.poll_interval_seconds))

        await channel.send(
            f"⚠️ `/discovery` watcher timed out waiting for run `{run_id}`. "
            f"Check `/discovery` again for current status."
        )

    def _schedule_run_watcher(
        self,
        run_id: Optional[str],
        channel: Optional[discord.abc.Messageable],
    ) -> None:
        if not run_id or channel is None:
            return
        channel_id = str(getattr(channel, "id", "unknown"))
        key = f"{run_id}:{channel_id}"
        if key in self._watcher_keys:
            return
        self._watcher_keys.add(key)

        async def _watch() -> None:
            try:
                await self._watch_run_completion(run_id, channel)
            finally:
                self._watcher_keys.discard(key)

        safe_create_task(
            _watch(),
            logger=logger,
            error_channel=channel,
            name=f"discovery-watch-{run_id}",
        )

    @app_commands.command(
        name="discovery",
        description="Run full manual refresh and post fresh market snapshot",
    )
    async def update(self, interaction: discord.Interaction):
        if not self._is_authorized(interaction):
            logger.warning(
                "/discovery unauthorized user=%s channel_id=%s channel_name=%s discovery_channel_id=%s",
                interaction.user.id,
                interaction.channel_id,
                getattr(interaction.channel, "name", "unknown"),
                self.discovery_channel_id,
            )
            await interaction.response.send_message(
                "❌ You are not authorized to run `/discovery` outside owner/discovery scope.",
                ephemeral=True,
            )
            return

        if not await self._defer_interaction(interaction):
            return

        try:
            # Direct backend logic call instead of HTTP POST
            result = request_manual_refresh(
                triggered_by=str(interaction.user),
                requested_channel_id=str(interaction.channel_id),
            )
        except Exception as e:
            logger.error(f"Failed to request manual refresh: {e}", exc_info=True)
            await self._handle_start_failure(interaction, error_detail=str(e))
            return

        if not result:
            await self._handle_start_failure(interaction)
            return

        # Normalize and resolve status
        result = self._normalize_refresh_payload(result)
        request_status = self._resolve_request_status(result)
        run = result.get("run") if isinstance(result.get("run"), dict) else {}

        if request_status == "started":
            await self._send_followup(interaction, self._started_message(run))
            self._schedule_run_watcher(str(run.get("run_id", "")), interaction.channel)
            return

        if request_status == "already_running":
            await self._send_followup(interaction, self._already_running_message(run))
            self._schedule_run_watcher(str(run.get("run_id", "")), interaction.channel)
            return

        if request_status == "cooldown":
            await self._send_followup(interaction, self._cooldown_message(run, result))
            channel = interaction.channel
            if channel is not None:
                report = await self._build_discovery_report_message()
                if report:
                    await self._send_report_chunks(
                        channel,
                        report,
                        prefix="📌 Latest available Futures Movers report (refresh cooldown active):",
                    )
            return

        if request_status == "blocked":
            await self._send_followup(interaction, self._blocked_message(result), ephemeral=True)
            return

        error_detail = self._extract_error_detail(result)
        if request_status in {"error", "failed"} or error_detail:
            await self._handle_start_failure(
                interaction,
                error_detail=error_detail or "Backend error",
            )
            return

        await self._send_followup(
            interaction,
            "⚠️ Unexpected refresh response. "
            f"(status={request_status or 'unknown'}) Please try again.",
        )

    async def cog_app_command_error(self, interaction, error):
        logger.error(f"[UpdateCommands] {error}", exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(UpdateCommands(bot))
