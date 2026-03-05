"""
Performance Commands — Discord Slash Commands for Behavioral Analysis

Session 434: Discord commands for David's trading performance analysis.
- /performance [period] — Behavioral profile summary for a month
- /compounding on|off|status — Toggle compounding mode
- /discipline — Quick discipline score with breakdown
"""

import json
import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from typing import Optional

from src.bot.utils.interaction_response import safe_defer, safe_send
from src.utils.logger import get_logger

logger = get_logger(__name__)

TRADE_LOG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "trades" / "trade_log.json"


def _load_trades() -> list:
    """Load trades from trade_log.json."""
    if not TRADE_LOG_PATH.exists():
        return []
    try:
        with open(TRADE_LOG_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data.get("trades", [])
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load trade log: {e}")
    return []


class PerformanceCommands(commands.Cog):
    """Discord commands for trading performance analysis."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="performance",
        description="View behavioral performance profile",
    )
    @app_commands.describe(period="Month to analyze (e.g. 2026-02). Default: last 3 months")
    async def performance_slash(self, interaction: discord.Interaction, period: Optional[str] = None):
        """Show behavioral performance profile."""
        await safe_defer(
            interaction,
            command_name="performance",
            logger=logger,
        )

        try:
            from src.risk.behavioral_analyzer import get_full_profile

            trades = _load_trades()
            if not trades:
                await safe_send(
                    interaction,
                    command_name="performance",
                    logger=logger,
                    content="No trade data available.",
                )
                return

            profile = get_full_profile(trades, months=3)

            embed = discord.Embed(
                title="Trading Performance Profile",
                color=self._score_color(profile["discipline_score"]),
            )

            # Discipline score
            score = profile["discipline_score"]
            score_bar = self._score_bar(score)
            embed.add_field(
                name="Discipline Score",
                value=f"**{score}/100** {score_bar}",
                inline=False,
            )

            # Penalties
            if profile["penalties"]:
                penalties_text = "\n".join(f"- {p}" for p in profile["penalties"])
                embed.add_field(name="Deductions", value=penalties_text, inline=False)

            # Streaks
            streaks = profile["streaks"]
            streak_type = streaks.get("current_streak_type", "None")
            streak_count = streaks.get("current_streak", 0)
            embed.add_field(
                name="Current Streak",
                value=f"{streak_count} {streak_type}" if streak_type else "None",
                inline=True,
            )

            # Revenge trades
            revenge = profile["revenge_trades"]
            embed.add_field(
                name="Revenge Trades",
                value=f"{revenge['count']} detected (0% historical WR)",
                inline=True,
            )

            # Feedback coverage
            fb = profile["feedback_coverage"]
            embed.add_field(
                name="Feedback Coverage",
                value=f"{fb['overall_pct']:.0f}%",
                inline=True,
            )

            # Size correlation
            corr = profile["size_correlation"]
            if corr.get("correlation") == "INVERSE":
                embed.add_field(
                    name="Size vs Win Rate",
                    value="INVERSE — smaller positions win more",
                    inline=False,
                )

            # Compounding simulation
            sim = profile["compounding_simulation"]
            if sim.get("improvement", 0) != 0:
                embed.add_field(
                    name="Compounding Simulation",
                    value=(
                        f"Actual: ${sim['actual_pnl']:.2f}\n"
                        f"Disciplined: ${sim['disciplined_pnl']:.2f}\n"
                        f"Difference: **${sim['improvement']:+.2f}**"
                    ),
                    inline=False,
                )

            embed.set_footer(text=f"Based on {profile['trade_count']} trades over {profile['months_analyzed']} months")

            await safe_send(
                interaction,
                command_name="performance",
                logger=logger,
                embed=embed,
            )

        except Exception as e:
            logger.error(f"Performance command failed: {e}", exc_info=True)
            await safe_send(
                interaction,
                command_name="performance",
                logger=logger,
                content=f"Failed to compute performance profile: {e}",
            )

    @app_commands.command(
        name="compounding",
        description="Toggle or check compounding mode",
    )
    @app_commands.describe(action="on, off, or status")
    async def compounding_slash(self, interaction: discord.Interaction, action: str = "status"):
        """Toggle or check compounding mode."""
        await safe_defer(
            interaction,
            command_name="compounding",
            logger=logger,
        )

        try:
            from src.risk.compounding_mode import CompoundingMode

            cm = CompoundingMode()
            action_lower = action.lower().strip()

            if action_lower == "on":
                status = cm.activate()
                emoji = "ON"
            elif action_lower == "off":
                status = cm.deactivate()
                emoji = "OFF"
            else:
                status = cm.get_status()
                emoji = "ON" if status["active"] else "OFF"

            embed = discord.Embed(
                title=f"Compounding Mode: {emoji}",
                color=discord.Color.green() if status["active"] else discord.Color.greyple(),
            )

            if status["active"]:
                pnl = status.get("week_pnl", 0)
                target = status.get("weekly_target", 30)
                progress = min(pnl / target * 100, 100) if target > 0 else 0
                embed.add_field(
                    name="Weekly Progress",
                    value=f"${pnl:.2f} / ${target:.2f} ({progress:.0f}%)",
                    inline=True,
                )
                embed.add_field(
                    name="Max Position",
                    value=f"${status.get('max_position', 150)}",
                    inline=True,
                )
                embed.add_field(
                    name="Max Leverage",
                    value=f"{status.get('max_leverage', 2)}x",
                    inline=True,
                )
                embed.add_field(
                    name="Trades This Week",
                    value=str(status.get("trades_this_week", 0)),
                    inline=True,
                )

            await safe_send(
                interaction,
                command_name="compounding",
                logger=logger,
                embed=embed,
            )

        except Exception as e:
            logger.error(f"Compounding command failed: {e}", exc_info=True)
            await safe_send(
                interaction,
                command_name="compounding",
                logger=logger,
                content=f"Failed: {e}",
            )

    @app_commands.command(
        name="discipline",
        description="Quick discipline score with breakdown",
    )
    async def discipline_slash(self, interaction: discord.Interaction):
        """Show quick discipline score."""
        await safe_defer(
            interaction,
            command_name="discipline",
            logger=logger,
        )

        try:
            from src.risk.behavioral_analyzer import get_full_profile

            trades = _load_trades()
            if not trades:
                await safe_send(
                    interaction,
                    command_name="discipline",
                    logger=logger,
                    content="No trade data available.",
                )
                return

            profile = get_full_profile(trades, months=1)
            score = profile["discipline_score"]
            streaks = profile["streaks"]

            lines = [
                f"**Discipline Score: {score}/100** {self._score_bar(score)}",
                "",
            ]

            if profile["penalties"]:
                for p in profile["penalties"]:
                    lines.append(f"  {p}")
            else:
                lines.append("  No penalties — excellent discipline!")

            lines.append("")
            streak_type = streaks.get("current_streak_type")
            streak_count = streaks.get("current_streak", 0)
            if streak_type:
                lines.append(f"Current Streak: {streak_count} {streak_type}")

            revenge_count = profile["revenge_trades"]["count"]
            if revenge_count > 0:
                lines.append(f"Revenge Trades: {revenge_count}")

            fb_pct = profile["feedback_coverage"]["overall_pct"]
            lines.append(f"Feedback Coverage: {fb_pct:.0f}%")

            await safe_send(
                interaction,
                command_name="discipline",
                logger=logger,
                content="\n".join(lines),
            )

        except Exception as e:
            logger.error(f"Discipline command failed: {e}", exc_info=True)
            await safe_send(
                interaction,
                command_name="discipline",
                logger=logger,
                content=f"Failed: {e}",
            )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle errors in this cog's slash commands."""
        logger.error(f"Performance command error: {error}", exc_info=True)
        await safe_send(
            interaction,
            command_name="performance",
            logger=logger,
            content=f"Command failed: {error}",
            ephemeral=True,
        )

    @staticmethod
    def _score_color(score: int) -> discord.Color:
        """Get embed color based on discipline score."""
        if score >= 80:
            return discord.Color.green()
        elif score >= 60:
            return discord.Color.gold()
        elif score >= 40:
            return discord.Color.orange()
        return discord.Color.red()

    @staticmethod
    def _score_bar(score: int) -> str:
        """Generate a visual score bar."""
        filled = score // 10
        empty = 10 - filled
        return "[" + "#" * filled + "-" * empty + "]"


async def setup(bot: commands.Bot):
    """Register the PerformanceCommands cog."""
    await bot.add_cog(PerformanceCommands(bot))
