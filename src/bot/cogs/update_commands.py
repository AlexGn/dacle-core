"""Manual refresh slash command for on-demand futures/discovery updates."""

from __future__ import annotations

import os
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from scripts.cron.daily_futures_movers import format_discord_message
from src.bot.utils.safe_task import safe_create_task
from src.ops.discord_channel_contract import get_discord_channel_contract
from src.utils.logger import get_logger

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
        self.api_url = os.getenv("DACLE_API_URL", "http://localhost:8000").rstrip("/")
        self.api_key = os.getenv("DACLE_API_KEY", "").strip()
        self.owner_id = self._parse_int(os.getenv("DISCORD_OWNER_ID"))
        self.discovery_channel_id = self._resolve_discovery_channel_id()
        self.poll_interval_seconds = self._parse_int(
            os.getenv("MANUAL_REFRESH_POLL_INTERVAL_SECONDS"), default=5
        )
        self.poll_timeout_seconds = self._parse_int(
            os.getenv("MANUAL_REFRESH_MAX_RUNTIME_SECONDS"), default=2400
        ) + 120

    @staticmethod
    def _parse_int(value: Optional[str], default: int = 0) -> int:
        if not value:
            return default
        try:
            return int(value)
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
        return self._is_discovery_context(interaction)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    async def _api_post(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self.api_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    return data if isinstance(data, dict) else None
                logger.warning(f"/update POST {path} failed: HTTP {resp.status_code}")
                return None
        except Exception as e:
            logger.warning(f"/update POST {path} failed: {e}")
            return None

    async def _api_get(self, path: str) -> Optional[Dict[str, Any]]:
        url = f"{self.api_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    return data if isinstance(data, dict) else None
                logger.warning(f"/update GET {path} failed: HTTP {resp.status_code}")
                return None
        except Exception as e:
            logger.warning(f"/update GET {path} failed: {e}")
            return None

    def _started_message(self, run: Dict[str, Any]) -> str:
        return (
            f"🔄 **/update started**\n"
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
        movers = await self._api_get("/api/futures/movers/latest") or {}
        macro = await self._api_get("/api/macro/market-direction") or {}
        long_alignment = await self._api_get("/api/macro/long-alignment") or {}

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
            logger.warning(f"Failed to format full discovery report for /update: {e}")
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

    def _build_failure_message(self, run: Dict[str, Any]) -> str:
        return (
            f"❌ **/update failed**\n"
            f"Run: `{run.get('run_id', '?')}`\n"
            f"Status: {run.get('status', 'FAILED')}\n"
            f"Error: {run.get('error') or 'unknown'}\n"
            f"Log: `{run.get('log_path') or 'unknown'}`"
        )

    async def _watch_run_completion(self, run_id: str, channel: discord.abc.Messageable) -> None:
        deadline = datetime.now(timezone.utc).timestamp() + float(self.poll_timeout_seconds)
        while datetime.now(timezone.utc).timestamp() < deadline:
            payload = await self._api_get(f"/api/futures/refresh/run/{run_id}")
            if payload and isinstance(payload.get("run"), dict):
                run = payload["run"]
                status = str(run.get("status") or "").upper()
                if status in TERMINAL_STATUSES:
                    if status == "COMPLETED":
                        report = await self._build_discovery_report_message()
                        if report:
                            for chunk in self._chunk_discord_message(report):
                                await channel.send(chunk)
                        else:
                            await channel.send(
                                f"✅ **/update completed**\n"
                                f"Run: `{run.get('run_id', '?')}` in {run.get('duration_seconds', '?')}s\n"
                                "⚠️ Could not render full discovery report. "
                                "Use `/scan` for current snapshot."
                            )
                    else:
                        await channel.send(self._build_failure_message(run))
                    return
            await asyncio.sleep(max(2, self.poll_interval_seconds))

        await channel.send(
            f"⚠️ `/update` watcher timed out waiting for run `{run_id}`. "
            f"Check `/update` again for current status."
        )

    @app_commands.command(
        name="update",
        description="Run full manual refresh and post fresh market snapshot",
    )
    async def update(self, interaction: discord.Interaction):
        if not self._is_authorized(interaction):
            logger.warning(
                "/update unauthorized user=%s channel_id=%s channel_name=%s discovery_channel_id=%s",
                interaction.user.id,
                interaction.channel_id,
                getattr(interaction.channel, "name", "unknown"),
                self.discovery_channel_id,
            )
            await interaction.response.send_message(
                "❌ You are not authorized to run `/update` outside owner/discovery scope.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=False, thinking=True)

        payload = {
            "triggered_by": str(interaction.user),
            "requested_channel_id": str(interaction.channel_id),
        }
        result = await self._api_post("/api/futures/refresh/run", payload)
        if not result:
            await interaction.followup.send(
                "❌ Failed to start refresh. API unavailable or rejected request.",
                ephemeral=True,
            )
            return

        request_status = str(result.get("request_status", "")).lower()
        run = result.get("run") if isinstance(result.get("run"), dict) else {}

        if request_status == "started":
            await interaction.followup.send(self._started_message(run))
            channel = interaction.channel
            if channel is not None:
                safe_create_task(
                    self._watch_run_completion(str(run.get("run_id", "")), channel),
                    logger=logger,
                    error_channel=channel,
                    name=f"update-watch-{run.get('run_id', 'unknown')}",
                )
            return

        if request_status == "already_running":
            await interaction.followup.send(self._already_running_message(run))
            return

        if request_status == "cooldown":
            await interaction.followup.send(self._cooldown_message(run, result))
            return

        await interaction.followup.send("⚠️ Unexpected refresh response. Please try again.")

    async def cog_app_command_error(self, interaction, error):
        logger.error(f"[UpdateCommands] {error}", exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(UpdateCommands(bot))
