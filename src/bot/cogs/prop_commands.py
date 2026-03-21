import asyncio
import logging
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

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
        self.project_root = Path(__file__).resolve().parents[3]

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

    def _report_dir(self) -> Path:
        return self.project_root / "reports" / "top_50_dacle"

    def _latest_report_path(self) -> Optional[Path]:
        report_dir = self._report_dir()
        if not report_dir.exists():
            return None
        reports = sorted(report_dir.glob("scan_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return reports[0] if reports else None

    @staticmethod
    def _chunk_discord_message(text: str, limit: int = 1900) -> list[str]:
        if len(text) <= limit:
            return [text]

        chunks: list[str] = []
        current = []
        current_len = 0
        for line in text.splitlines(keepends=True):
            if current and current_len + len(line) > limit:
                chunks.append("".join(current).rstrip())
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += len(line)
        if current:
            chunks.append("".join(current).rstrip())
        return chunks

    async def _send_report_chunks(self, channel: discord.abc.Messageable, report: str) -> None:
        for chunk in self._chunk_discord_message(report):
            await channel.send(chunk)

    @staticmethod
    def _format_report_message(results: list[dict[str, Any]]) -> Optional[str]:
        if not results:
            return None

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        header = (
            "**🎯 Prop Firm Top 10 High-Conviction Setups**\n"
            f"*Dacle Cipher + Genesis Macro Alignment | {now}*\n\n"
        )

        body = ""
        for r in results[:10]:
            is_bullish = "BULLISH" in str(r.get("genesis") or "")
            setup_type = "LONG CONTINUATION" if is_bullish else "SHORT CONTINUATION"
            emoji = "🚀" if is_bullish else "📉"
            rsi = float(r.get("rsi") or 50.0)
            verdict = "APPROVE"
            reversal_risk = 2.0

            if is_bullish:
                if rsi > 75:
                    reversal_risk += (rsi - 75) / 5 * 2.0
                if r.get("has_red_dot"):
                    reversal_risk += 3.0
                if rsi > 85:
                    verdict = "SKIP"
            else:
                if rsi < 25:
                    reversal_risk += (25 - rsi) / 5 * 2.0
                if r.get("has_green_dot"):
                    reversal_risk += 3.0
                if rsi < 15:
                    verdict = "SKIP"

            reversal_risk = min(max(reversal_risk, 0.0), 9.9)
            conviction = float(r.get("conviction") or 0.0)
            ratio = (conviction / 10.0) * 0.5 + 0.1
            change_str = f"{float(r.get('change_24h') or 0.0):+.1f}%"
            vol_m = float(r.get("volume_usd") or 0.0) / 1e6
            symbol = str(r.get("symbol") or "?")

            line1 = (
                f"✅ `{symbol:8s}` {change_str} | {emoji} {setup_type} | "
                f"C {conviction:.1f} | R {reversal_risk:.1f} | Vol ${vol_m:.0f}M\n"
            )

            bias = "Bullish" if is_bullish else "Bearish"
            if r.get("has_green_dot"):
                bias += " (🟢 Green Dot)"
            elif r.get("has_red_dot"):
                bias += " (🔴 Red Dot)"

            ta_align = "LONG ALIGNED" if is_bullish else "SHORT ALIGNED"
            line2 = (
                f"    *{bias}, RSI {rsi:.0f}, TA {ta_align}* | "
                f"Verdict **{verdict}** ({ratio:.2f})\n"
            )
            body += line1 + line2

        return header + body if body else None

    def _load_latest_report_message(self) -> Optional[str]:
        report_path = self._latest_report_path()
        if report_path is None:
            return None
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load /show report %s: %s", report_path, exc)
            return None

        if not isinstance(data, list):
            return None
        return self._format_report_message(data)

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
            
            # Execute the scanner using the virtual environment's python to ensure ccxt is available
            venv_python = os.path.join(os.getcwd(), "venv/bin/python3")
            child_env = os.environ.copy()
            child_env["DISABLE_PROP_FIRM_WEBHOOK"] = "1"
            process = await asyncio.create_subprocess_exec(
                venv_python, "scripts/scanners/top_50_dacle_scanner.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                report_message = self._load_latest_report_message()
                if report_message and interaction.channel is not None:
                    await self._send_report_chunks(interaction.channel, report_message)
                    await interaction.followup.send("✅ **/show completed** - Results posted in this channel.")
                else:
                    await interaction.followup.send(
                        "✅ **/show completed** - Scanner finished, but no report could be rendered from the latest artifact."
                    )
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
