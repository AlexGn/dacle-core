"""
Whales Market OTC Discord Commands

Slash commands for scanning pre-market OTC data and analyzing TGE opportunities.
"""

from src.utils.logger import get_logger
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.integrations.whalesmarket.scanner import WhalesMarketScanner
from src.knowledge.supabase_client import get_knowledge_base

logger = get_logger(__name__)


class OTCCommands(commands.Cog):
    """Cog for Whales Market pre-market OTC tracking via Discord slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.kb = get_knowledge_base()

        # Initialize OTC scanner
        self.otc_scanner = WhalesMarketScanner(self.kb)

    @app_commands.command(
        name="scan-otc", description="Scan Whales Market for pre-market OTC tokens"
    )
    @app_commands.describe(max_results="Maximum number of tokens to scan (default: 50)")
    async def scan_otc(self, interaction: discord.Interaction, max_results: Optional[int] = 50):
        """Scan Whales Market for pre-market OTC tokens and log them to database."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Run scan
            stats = self.otc_scanner.scan_and_log(max_results=max_results)

            # Format response
            embed = discord.Embed(
                title="🐳 Whales Market OTC Scan Complete",
                color=discord.Color.blue(),
                timestamp=datetime.now(),
            )

            embed.add_field(
                name="📊 Scan Results",
                value=(
                    f"**Found:** {stats['found']} tokens\n"
                    f"**Logged:** {stats['logged']} tokens\n"
                    f"**Signals:** {stats['signals_generated']} trading signals"
                ),
                inline=False,
            )

            # Show strong signals
            if stats.get("strong_signals"):
                signal_text = ""
                for signal in stats["strong_signals"][:5]:  # Limit to 5
                    signal_text += f"**{signal['symbol']}**: {signal['signal_type']}\n"

                embed.add_field(name="🎯 Strong Signals", value=signal_text, inline=False)

            embed.add_field(
                name="📝 Next Steps",
                value="Use `/otc-price <symbol>` to check specific tokens\n"
                "Use `/otc-signals` to see all trading signals",
                inline=False,
            )

            embed.set_footer(text="Powered by Whales Market (whales.market)")

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(
                f"✅ {interaction.user.name} scanned OTC: "
                f"{stats['found']} found, {stats['logged']} logged"
            )

        except Exception as e:
            logger.error(f"❌ Error scanning OTC: {e}")
            await interaction.followup.send(
                f"❌ **Error scanning OTC market:** {str(e)}", ephemeral=True
            )

    @app_commands.command(name="otc-price", description="Check pre-market OTC price for a token")
    @app_commands.describe(symbol="Token symbol (e.g., MET, SOL, BTC)")
    async def otc_price(self, interaction: discord.Interaction, symbol: str):
        """Check pre-market OTC price and data for a specific token."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Query database for token
            symbol = symbol.strip().upper()

            result = (
                self.kb.client.table("otc_premarket_data")
                .select("*")
                .eq("project_symbol", symbol)
                .eq("source", "whales_market")
                .order("last_updated", desc=True)
                .limit(1)
                .execute()
            )

            if not result.data:
                await interaction.followup.send(
                    f"❌ **{symbol}** not found in OTC market.\n"
                    f"Run `/scan-otc` to refresh data.",
                    ephemeral=True,
                )
                return

            token = result.data[0]

            # Calculate signals
            signals = self.otc_scanner.calculate_signals(token)

            # Format as embed
            embed = discord.Embed(
                title=f"🐳 {token['project_symbol']} - Pre-Market OTC Data",
                description=token.get("project_name", token["project_symbol"]),
                color=self._get_signal_color(signals["signal_type"]),
                timestamp=datetime.now(),
            )

            # Price data
            if token.get("otc_price"):
                price_field = f"💰 **${token['otc_price']:.6f}**"

                if token.get("metadata", {}).get("last_price_change"):
                    price_field += f"\n📈 Change: {token['metadata']['last_price_change']}"

                embed.add_field(name="Current OTC Price", value=price_field, inline=True)

            # Volume data
            if token.get("volume_24h") or token.get("volume_total"):
                volume_field = ""
                if token.get("volume_24h"):
                    volume_field += f"24h: ${token['volume_24h']:,.0f}\n"
                if token.get("volume_total"):
                    volume_field += f"Total: ${token['volume_total']:,.0f}\n"

                embed.add_field(name="Trading Volume", value=volume_field, inline=True)

            # Market data
            market_field = ""
            if token.get("implied_fdv"):
                market_field += f"**FDV:** {token['implied_fdv']}\n"
            if token.get("settlement_date"):
                settlement_date = datetime.fromisoformat(token["settlement_date"])
                days_until = (settlement_date - datetime.now()).days
                market_field += (
                    f"**TGE:** {settlement_date.strftime('%b %d, %Y')} ({days_until} days)\n"
                )

            if market_field:
                embed.add_field(name="Market Data", value=market_field, inline=False)

            # Trading signals
            signal_emoji = self._get_signal_emoji(signals["signal_type"])
            signal_field = f"{signal_emoji} **{signals['signal_type'].replace('_', ' ')}**\n"
            signal_field += f"📊 Conviction: {signals['conviction_score']:.1f}/10\n\n"

            if signals["bullish_signals"]:
                signal_field += "**✅ Bullish Signals:**\n"
                for s in signals["bullish_signals"]:
                    signal_field += f"• {s}\n"

            if signals["bearish_signals"]:
                signal_field += "\n**⚠️ Bearish Signals:**\n"
                for s in signals["bearish_signals"]:
                    signal_field += f"• {s}\n"

            embed.add_field(name="Trading Signals", value=signal_field, inline=False)

            # Last updated
            last_updated = datetime.fromisoformat(token["last_updated"])
            embed.set_footer(
                text=f"Whales Market • Last updated: {last_updated.strftime('%H:%M UTC')}"
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(f"✅ {interaction.user.name} checked OTC price for {symbol}")

        except Exception as e:
            logger.error(f"❌ Error checking OTC price: {e}")
            await interaction.followup.send(
                f"❌ **Error checking OTC price:** {str(e)}", ephemeral=True
            )

    @app_commands.command(
        name="otc-signals", description="View tokens with strong OTC trading signals"
    )
    @app_commands.describe(
        signal_type="Filter by signal type (BUY/SHORT/ALL)",
        min_conviction="Minimum conviction score (default: 6.0)",
    )
    async def otc_signals(
        self,
        interaction: discord.Interaction,
        signal_type: Optional[str] = "ALL",
        min_conviction: Optional[float] = 6.0,
    ):
        """View tokens with strong OTC trading signals."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Query all OTC tokens
            result = (
                self.kb.client.table("otc_premarket_data")
                .select("*")
                .eq("source", "whales_market")
                .order("last_updated", desc=True)
                .limit(50)
                .execute()
            )

            if not result.data:
                await interaction.followup.send(
                    "📭 No OTC data available.\nRun `/scan-otc` to discover tokens.", ephemeral=True
                )
                return

            # Calculate signals for all tokens
            signals_list = []
            for token in result.data:
                signal = self.otc_scanner.calculate_signals(token)

                # Apply filters
                if signal_type.upper() != "ALL":
                    if signal_type.upper() not in signal["signal_type"]:
                        continue

                if signal["conviction_score"] < min_conviction:
                    continue

                # Add token data to signal
                signal["token"] = token
                signals_list.append(signal)

            if not signals_list:
                await interaction.followup.send(
                    f"📭 No signals found matching criteria:\n"
                    f"• Type: {signal_type}\n"
                    f"• Min Conviction: {min_conviction}/10",
                    ephemeral=True,
                )
                return

            # Sort by conviction score
            signals_list.sort(key=lambda x: x["conviction_score"], reverse=True)

            # Format as embed
            embed = discord.Embed(
                title=f"🎯 OTC Trading Signals (Top {len(signals_list)})",
                description=f"Filtered: {signal_type} signals with ≥{min_conviction} conviction",
                color=discord.Color.gold(),
                timestamp=datetime.now(),
            )

            for signal in signals_list[:10]:  # Limit to 10
                token = signal["token"]
                signal_emoji = self._get_signal_emoji(signal["signal_type"])

                field_value = f"{signal_emoji} **{signal['signal_type'].replace('_', ' ')}**\n"
                field_value += f"📊 Conviction: {signal['conviction_score']:.1f}/10\n"

                if token.get("otc_price"):
                    field_value += f"💰 OTC: ${token['otc_price']:.6f}\n"

                if token.get("volume_24h"):
                    field_value += f"📈 24h Vol: ${token['volume_24h']:,.0f}\n"

                # Top signal
                if signal["bullish_signals"]:
                    field_value += f"✅ {signal['bullish_signals'][0]}\n"
                if signal["bearish_signals"]:
                    field_value += f"⚠️ {signal['bearish_signals'][0]}\n"

                embed.add_field(
                    name=f"{token['project_symbol']} - {token.get('project_name', '')}",
                    value=field_value,
                    inline=False,
                )

            embed.set_footer(text="Whales Market • Use /otc-price <symbol> for details")

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(f"✅ {interaction.user.name} viewed OTC signals")

        except Exception as e:
            logger.error(f"❌ Error fetching OTC signals: {e}")
            await interaction.followup.send(
                f"❌ **Error fetching signals:** {str(e)}", ephemeral=True
            )

    def _get_signal_color(self, signal_type: str) -> discord.Color:
        """Get embed color based on signal type."""
        if "BUY" in signal_type:
            return discord.Color.green()
        elif "SHORT" in signal_type:
            return discord.Color.red()
        else:
            return discord.Color.light_gray()

    def _get_signal_emoji(self, signal_type: str) -> str:
        """Get emoji for signal type."""
        if signal_type == "STRONG_BUY":
            return "🚀"
        elif signal_type == "BUY":
            return "✅"
        elif signal_type == "STRONG_SHORT":
            return "🔻"
        elif signal_type == "SHORT":
            return "⚠️"
        else:
            return "➖"


async def setup(bot: commands.Bot):
    """Setup function to load the cog."""
    await bot.add_cog(OTCCommands(bot))
