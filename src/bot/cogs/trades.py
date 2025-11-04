"""
Trade Commands Cog

Discord slash commands for logging trades:
- /trade-entry - Log a trade entry
- /trade-exit - Log a trade exit
- /trades-open - List open positions
- /trades-stats - Show performance metrics
"""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from knowledge.supabase_client import get_knowledge_base
from knowledge.trade_logger import TradeLogger

logger = logging.getLogger(__name__)


class TradeCommands(commands.Cog):
    """
    Cog for trade logging via Discord slash commands
    """

    def __init__(self, bot: commands.Bot):
        """Initialize the trades cog"""
        self.bot = bot
        self.kb = get_knowledge_base()
        self.trade_logger = TradeLogger(self.kb)
        logger.info("TradeCommands cog initialized")

    @app_commands.command(name="trade-entry", description="Log a trade entry")
    @app_commands.describe(
        symbol="Project symbol (e.g., SOL, BTC, ETH)",
        entry_price="Entry price in USD",
        position_size="Position size in USD",
        conviction="Optional: Conviction score 1-10",
        notes="Optional: Entry notes",
    )
    async def trade_entry(
        self,
        interaction: discord.Interaction,
        symbol: str,
        entry_price: float,
        position_size: float,
        conviction: Optional[float] = None,
        notes: Optional[str] = None,
    ):
        """Log a trade entry via Discord command."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Validate inputs
            if entry_price <= 0:
                await interaction.followup.send(
                    "❌ Entry price must be greater than 0", ephemeral=True
                )
                return

            if position_size <= 0:
                await interaction.followup.send(
                    "❌ Position size must be greater than 0", ephemeral=True
                )
                return

            if conviction and (conviction < 1 or conviction > 10):
                await interaction.followup.send(
                    "❌ Conviction score must be between 1-10", ephemeral=True
                )
                return

            # Log the trade
            trade_id = self.trade_logger.log_entry(
                project_symbol=symbol.upper(),
                entry_price=entry_price,
                position_size_usd=position_size,
                conviction_score=conviction,
                notes=notes,
            )

            # Create success embed
            embed = discord.Embed(
                title="✅ Trade Entry Logged",
                description=f"Successfully logged entry for **{symbol.upper()}**",
                color=discord.Color.green(),
            )

            embed.add_field(name="Trade ID", value=f"`{trade_id[:13]}...`", inline=False)
            embed.add_field(name="Symbol", value=symbol.upper(), inline=True)
            embed.add_field(name="Entry Price", value=f"${entry_price:,.2f}", inline=True)
            embed.add_field(
                name="Position Size", value=f"${position_size:,.2f}", inline=True
            )

            if conviction:
                embed.add_field(name="Conviction", value=f"{conviction}/10", inline=True)

            if notes:
                embed.add_field(name="Notes", value=notes, inline=False)

            embed.set_footer(
                text=f"To exit: /trade-exit trade_id:{trade_id[:13]}... <exit_price>"
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(
                f"Trade entry logged via Discord: {symbol} @ ${entry_price} "
                f"by {interaction.user.name}"
            )

        except Exception as e:
            logger.error(f"Error logging trade entry: {e}")
            logger.exception("Full traceback:")
            await interaction.followup.send(
                f"❌ Error logging trade: {str(e)}", ephemeral=True
            )

    @app_commands.command(name="trade-exit", description="Log a trade exit")
    @app_commands.describe(
        trade_id="Trade ID from entry (first 8+ characters work)",
        exit_price="Exit price in USD",
        reason="Exit reason (e.g., target_hit, stop_loss, manual)",
        notes="Optional: Exit notes",
    )
    async def trade_exit(
        self,
        interaction: discord.Interaction,
        trade_id: str,
        exit_price: float,
        reason: str = "manual",
        notes: Optional[str] = None,
    ):
        """Log a trade exit via Discord command."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Validate inputs
            if exit_price <= 0:
                await interaction.followup.send(
                    "❌ Exit price must be greater than 0", ephemeral=True
                )
                return

            # Find full trade ID if partial provided
            if len(trade_id) < 36:  # Partial ID provided
                result = (
                    self.kb.client.table("trades")
                    .select("id")
                    .eq("status", "open")
                    .execute()
                )

                matching_trades = [
                    t["id"] for t in result.data if t["id"].startswith(trade_id)
                ]

                if len(matching_trades) == 0:
                    await interaction.followup.send(
                        f"❌ No open trade found starting with `{trade_id}`",
                        ephemeral=True,
                    )
                    return
                elif len(matching_trades) > 1:
                    await interaction.followup.send(
                        f"❌ Multiple trades match `{trade_id}`. Please provide more characters.",
                        ephemeral=True,
                    )
                    return

                trade_id = matching_trades[0]

            # Log the exit
            result = self.trade_logger.log_exit(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_reason=reason,
                notes=notes,
            )

            # Create success embed
            outcome_emoji = "🎉" if result["outcome"] == "win" else "😞" if result["outcome"] == "loss" else "😐"
            outcome_color = (
                discord.Color.green()
                if result["outcome"] == "win"
                else discord.Color.red()
                if result["outcome"] == "loss"
                else discord.Color.greyple()
            )

            embed = discord.Embed(
                title=f"{outcome_emoji} Trade Exit Logged",
                description=f"Trade closed: **{result['outcome'].upper()}**",
                color=outcome_color,
            )

            embed.add_field(
                name="Entry → Exit",
                value=f"${result['entry_price']:,.2f} → ${result['exit_price']:,.2f}",
                inline=False,
            )
            embed.add_field(
                name="Return", value=f"{result['return_pct']:+.2f}%", inline=True
            )
            embed.add_field(name="P/L", value=f"${result['pnl_usd']:+,.2f}", inline=True)
            embed.add_field(
                name="Held", value=f"{result['holding_days']:.1f} days", inline=True
            )
            embed.add_field(name="Exit Reason", value=reason, inline=True)

            if notes:
                embed.add_field(name="Notes", value=notes, inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(
                f"Trade exit logged via Discord: {trade_id[:8]}... "
                f"{result['outcome']} ({result['return_pct']:+.1f}%) "
                f"by {interaction.user.name}"
            )

        except ValueError as e:
            await interaction.followup.send(f"❌ {str(e)}", ephemeral=True)
        except Exception as e:
            logger.error(f"Error logging trade exit: {e}")
            logger.exception("Full traceback:")
            await interaction.followup.send(
                f"❌ Error logging exit: {str(e)}", ephemeral=True
            )

    @app_commands.command(name="trades-open", description="List all open positions")
    async def trades_open(self, interaction: discord.Interaction):
        """List all open trades."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get open trades
            result = (
                self.kb.client.table("trades")
                .select("*, projects(name, symbol)")
                .eq("status", "open")
                .order("entered_at", desc=True)
                .limit(20)
                .execute()
            )

            if not result.data:
                await interaction.followup.send(
                    "📊 No open positions currently.", ephemeral=True
                )
                return

            trades = result.data

            # Create embed
            embed = discord.Embed(
                title=f"📊 Open Positions ({len(trades)})",
                description="Current open trades",
                color=discord.Color.blue(),
            )

            total_invested = sum(t.get("position_size_usd", 0) for t in trades)
            embed.add_field(
                name="Total Invested",
                value=f"${total_invested:,.2f}",
                inline=False,
            )

            # Add each trade
            for i, trade in enumerate(trades[:10], 1):  # Limit to 10 for display
                project = trade.get("projects", {})
                symbol = project.get("symbol", "???")
                entry_price = trade.get("entry_price", 0)
                position_size = trade.get("position_size_usd", 0)
                conviction = trade.get("data", {}).get("conviction_score")

                trade_info = f"Entry: ${entry_price:,.2f}\n"
                trade_info += f"Size: ${position_size:,.2f}"
                if conviction:
                    trade_info += f"\nConviction: {conviction}/10"

                embed.add_field(
                    name=f"{i}. {symbol}",
                    value=trade_info,
                    inline=True,
                )

            if len(trades) > 10:
                embed.set_footer(text=f"Showing 10 of {len(trades)} open positions")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error listing open trades: {e}")
            logger.exception("Full traceback:")
            await interaction.followup.send(
                f"❌ Error listing trades: {str(e)}", ephemeral=True
            )

    @app_commands.command(name="trades-stats", description="Show trading performance stats")
    @app_commands.describe(
        researcher="Optional: Filter by researcher (Austin, Phobia, Sebastien)"
    )
    async def trades_stats(
        self, interaction: discord.Interaction, researcher: Optional[str] = None
    ):
        """Show trading performance statistics."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get performance metrics
            metrics = self.trade_logger.get_trade_performance(
                researcher_name=researcher
            )

            if metrics.get("total_trades", 0) == 0:
                msg = "📊 No trades found"
                if researcher:
                    msg += f" from {researcher}"
                await interaction.followup.send(msg, ephemeral=True)
                return

            # Create embed
            title = "📊 Trading Performance"
            if researcher:
                title += f" - {researcher}"

            color = discord.Color.green() if metrics.get("win_rate", 0) >= 50 else discord.Color.red()

            embed = discord.Embed(
                title=title,
                description="Overall performance metrics",
                color=color,
            )

            # Trade counts
            embed.add_field(
                name="Total Trades",
                value=f"{metrics['total_trades']}",
                inline=True,
            )
            embed.add_field(
                name="Open",
                value=f"{metrics.get('open_trades', 0)}",
                inline=True,
            )
            embed.add_field(
                name="Closed",
                value=f"{metrics.get('closed_trades', 0)}",
                inline=True,
            )

            # Performance metrics (only if have closed trades)
            if metrics.get("closed_trades", 0) > 0:
                embed.add_field(
                    name="Wins",
                    value=f"{metrics.get('wins', 0)}",
                    inline=True,
                )
                embed.add_field(
                    name="Losses",
                    value=f"{metrics.get('losses', 0)}",
                    inline=True,
                )
                embed.add_field(
                    name="Win Rate",
                    value=f"{metrics['win_rate']}%",
                    inline=True,
                )
                embed.add_field(
                    name="Avg Return",
                    value=f"{metrics['avg_return']:+.2f}%",
                    inline=True,
                )
                embed.add_field(
                    name="Total P/L",
                    value=f"${metrics['total_pnl']:+,.2f}",
                    inline=True,
                )
                embed.add_field(
                    name="Avg Hold Time",
                    value=f"{metrics['avg_holding_days']:.1f} days",
                    inline=True,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error getting trade stats: {e}")
            logger.exception("Full traceback:")
            await interaction.followup.send(
                f"❌ Error getting stats: {str(e)}", ephemeral=True
            )


async def setup(bot: commands.Bot):
    """Setup function called by bot when loading this cog"""
    await bot.add_cog(TradeCommands(bot))
    logger.info("TradeCommands cog loaded")
