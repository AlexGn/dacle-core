"""Loss Digest Cog — /loss_digest and /loss_attribution Discord commands.

Phase D3: Wires LossAttributor into Discord for on-demand and scheduled
loss analysis. Shows primary causes, violated learnings, and preventability.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.bot.utils.interaction_response import safe_defer, safe_send
from src.learning.loss_attribution import LossAttributor, get_loss_attributor
from src.utils.logger import get_logger

logger = get_logger(__name__)

AUDIT_DIR = Path("data/audit")


class LossDigestCog(commands.Cog):
    """Loss attribution digest — daily and on-demand analysis."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.attributor = get_loss_attributor()
        self._digest_channel_id = int(os.environ.get("LOSS_DIGEST_CHANNEL_ID", "0") or "0")
        logger.info("LossDigestCog initialized")

    @app_commands.command(name="loss_digest", description="Loss attribution digest for recent trades")
    @app_commands.describe(hours="Lookback window in hours (default: 24)")
    async def loss_digest(self, interaction: discord.Interaction, hours: int = 24):
        """Show loss attribution summary for recent trades."""
        await safe_defer(interaction, ephemeral=True)

        trades = self._load_recent_trades(hours)
        loss_trades = [t for t in trades if self._is_loss(t)]

        if not loss_trades:
            await safe_send(interaction, "No losing trades found in the last {}h.".format(hours))
            return

        analyses = self.attributor.analyze_all_losses(loss_trades)

        if not analyses:
            await safe_send(interaction, "No loss attribution results for {} trades.".format(len(loss_trades)))
            return

        # Build summary
        total_losses = len(analyses)
        preventable = sum(1 for a in analyses if a.preventable)
        primary_causes = {}
        all_violations = []

        for a in analyses:
            cause = a.primary_cause or "UNKNOWN"
            primary_causes[cause] = primary_causes.get(cause, 0) + 1
            all_violations.extend(a.violations or [])

        top_causes = sorted(primary_causes.items(), key=lambda x: -x[1])[:5]

        embed = discord.Embed(
            title=f"Loss Digest — {hours}h",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Total Losses", value=str(total_losses), inline=True)
        embed.add_field(name="Preventable", value=f"{preventable}/{total_losses} ({100*preventable/max(total_losses,1):.0f}%)", inline=True)

        causes_text = "\n".join(f"• {c}: {n}" for c, n in top_causes) or "None"
        embed.add_field(name="Top Primary Causes", value=causes_text, inline=False)

        # Top violated learnings
        violation_counts = {}
        for v in all_violations:
            vid = getattr(v, "learning_id", str(v)) or "UNKNOWN"
            violation_counts[vid] = violation_counts.get(vid, 0) + 1
        top_violations = sorted(violation_counts.items(), key=lambda x: -x[1])[:5]
        violations_text = "\n".join(f"• {vid}: {n}" for vid, n in top_violations) or "None"
        embed.add_field(name="Most Violated Learnings", value=violations_text, inline=False)

        # Actionable lessons (from first 3 analyses)
        lessons = []
        for a in analyses[:3]:
            for lesson in (a.lessons or [])[:2]:
                if lesson and lesson not in lessons:
                    lessons.append(lesson)
        lessons_text = "\n".join(f"• {l}" for l in lessons[:5]) or "None"
        embed.add_field(name="Key Lessons", value=lessons_text, inline=False)

        await safe_send(interaction, embed=embed)

    @app_commands.command(name="loss_attribution", description="Detailed loss attribution for a specific trade")
    @app_commands.describe(trade_id="Trade ID to analyze")
    async def loss_attribution(self, interaction: discord.Interaction, trade_id: str):
        """Show detailed loss attribution for a specific trade."""
        await safe_defer(interaction, ephemeral=True)

        trades = self._load_recent_trades(168)  # 7 days
        trade = next((t for t in trades if str(t.get("trade_id", "")) == trade_id), None)

        if not trade:
            await safe_send(interaction, f"Trade `{trade_id}` not found in last 7 days.")
            return

        analysis = self.attributor.analyze_loss_trade(trade)

        embed = discord.Embed(
            title=f"Loss Attribution — {trade_id}",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Primary Cause", value=analysis.primary_cause or "UNKNOWN", inline=False)
        embed.add_field(name="Preventable", value="Yes" if analysis.preventable else "No", inline=True)
        embed.add_field(name="Confidence", value=f"{analysis.confidence:.0%}", inline=True)

        violations_text = "\n".join(
            f"• {getattr(v, 'learning_id', v)} [{getattr(v, 'violation_type', '?')}] — {getattr(v, 'severity', '?')}"
            for v in (analysis.violations or [])[:5]
        ) or "None"
        embed.add_field(name="Violations", value=violations_text, inline=False)

        lessons_text = "\n".join(f"• {l}" for l in (analysis.lessons or [])[:5]) or "None"
        embed.add_field(name="Lessons", value=lessons_text, inline=False)

        await safe_send(interaction, embed=embed)

    def _load_recent_trades(self, hours: int):
        """Load round-trip trades from audit files within lookback window."""
        trades = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_ts = cutoff.timestamp()

        for path in sorted(AUDIT_DIR.glob("round_trips_*.jsonl")):
            try:
                for line in path.read_text().strip().splitlines():
                    record = json.loads(line)
                    entry_ts = record.get("entry_ts") or record.get("entry_time") or 0
                    if isinstance(entry_ts, (int, float)) and entry_ts >= cutoff_ts:
                        trades.append(record)
                    elif isinstance(entry_ts, str):
                        try:
                            if datetime.fromisoformat(entry_ts).timestamp() >= cutoff_ts:
                                trades.append(record)
                        except (ValueError, TypeError):
                            pass
            except Exception:
                continue
        return trades

    @staticmethod
    def _is_loss(trade: dict) -> bool:
        """Check if a trade record is a loss."""
        pnl = trade.get("pnl_usd") or trade.get("pnl_pct") or 0
        try:
            return float(pnl) < 0
        except (TypeError, ValueError):
            return False


async def setup(bot: commands.Bot):
    """Discord cog registration hook."""
    await bot.add_cog(LossDigestCog(bot))