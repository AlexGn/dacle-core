"""Manual refresh slash command for on-demand futures/discovery updates."""

from __future__ import annotations

import os
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import discord
import httpx
from discord import app_commands
from discord.ext import commands

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

    def _is_authorized(self, interaction: discord.Interaction) -> bool:
        if self._is_owner(interaction.user.id):
            return True
        if self.discovery_channel_id is None:
            return False
        return interaction.channel_id == self.discovery_channel_id

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

    async def _build_success_message(self, run: Dict[str, Any]) -> str:
        movers = await self._api_get("/api/futures/movers/latest") or {}
        macro = await self._api_get("/api/macro/market-direction") or {}

        scan_time = movers.get("scan_time")
        pairs = movers.get("total_pairs_scanned")
        movers_found = movers.get("movers_found")
        ta_analyzed = movers.get("ta_analyzed")
        setups = movers.get("setups") or []
        bias = macro.get("bias")
        conf = macro.get("confidence_pct")
        macro_line = "Macro: unknown"
        if bias:
            if conf is not None:
                macro_line = f"Macro: {bias} ({int(conf)}% confidence)"
            else:
                macro_line = f"Macro: {bias}"

        counts_line_parts = []
        if pairs is not None:
            counts_line_parts.append(f"{pairs} pairs")
        if movers_found is not None:
            counts_line_parts.append(f"{movers_found} movers")
        if ta_analyzed is not None:
            counts_line_parts.append(f"{ta_analyzed} TA")
        if not counts_line_parts:
            counts_line_parts.append(f"{len(setups)} setups")

        return (
            f"✅ **/update completed**\n"
            f"Run: `{run.get('run_id', '?')}` in {run.get('duration_seconds', '?')}s\n"
            f"Data timestamp: {_fmt_utc(scan_time)}\n"
            f"Snapshot: {' | '.join(counts_line_parts)}\n"
            f"{macro_line}"
        )

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
                        await channel.send(await self._build_success_message(run))
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
