"""
CryptoRank Discord Commands

Slash commands for scanning TGEs and token unlocks from CryptoRank.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from together import Together

from src.integrations.cryptorank.tge_scanner import TGEScanner
from src.integrations.cryptorank.unlock_monitor import UnlockMonitor
from src.knowledge.supabase_client import get_knowledge_base
from src.utils.config import get_together_config

logger = logging.getLogger(__name__)


class CryptoRankCommands(commands.Cog):
    """Cog for CryptoRank TGE and unlock monitoring via Discord slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.kb = get_knowledge_base()

        # Initialize Together client for LLM extraction
        together_config = get_together_config()
        self.together_client = Together(api_key=together_config.api_key)

        # Initialize scanners
        self.tge_scanner = TGEScanner(self.kb, self.together_client)
        self.unlock_monitor = UnlockMonitor(self.kb, self.together_client)

    @app_commands.command(
        name="scan-tges", description="Scan CryptoRank for upcoming TGE/ICO/IDO events"
    )
    @app_commands.describe(max_results="Maximum number of TGEs to scan (default: 50)")
    async def scan_tges(self, interaction: discord.Interaction, max_results: Optional[int] = 50):
        """Scan CryptoRank for upcoming TGE events and log them to database."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Run scan
            stats = self.tge_scanner.scan_and_log(max_results=max_results)

            # Format response
            embed = discord.Embed(
                title="🚀 CryptoRank TGE Scan Complete",
                color=discord.Color.green(),
                timestamp=datetime.now(),
            )

            embed.add_field(
                name="📊 Scan Results",
                value=(
                    f"**Found:** {stats['found']} TGEs\n"
                    f"**Logged:** {stats['logged']} new TGEs\n"
                    f"**Skipped:** {stats['skipped']} duplicates"
                ),
                inline=False,
            )

            embed.add_field(
                name="📝 Next Steps",
                value="Use `/upcoming-tges` to view upcoming opportunities",
                inline=False,
            )

            embed.set_footer(text="Powered by CryptoRank.io")

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(
                f"✅ {interaction.user.name} scanned TGEs: "
                f"{stats['found']} found, {stats['logged']} logged"
            )

        except Exception as e:
            logger.error(f"❌ Error scanning TGEs: {e}")
            await interaction.followup.send(f"❌ **Error scanning TGEs:** {str(e)}", ephemeral=True)

    @app_commands.command(name="upcoming-tges", description="View upcoming TGE/ICO/IDO events")
    @app_commands.describe(days="How many days ahead to look (default: 7)")
    async def upcoming_tges(self, interaction: discord.Interaction, days: Optional[int] = 7):
        """View upcoming TGE events from database."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Query database for upcoming TGEs
            cutoff_date = (datetime.now() + timedelta(days=days)).isoformat()

            result = (
                self.kb.client.table("project_mentions")
                .select("*")
                .eq("source", "cryptorank")
                .gte("ido_date", datetime.now().isoformat())
                .lte("ido_date", cutoff_date)
                .order("ido_date", desc=False)
                .limit(10)
                .execute()
            )

            if not result.data:
                await interaction.followup.send(
                    f"📭 No upcoming TGEs found in the next {days} days.\n"
                    f"Run `/scan-tges` to discover new opportunities.",
                    ephemeral=True,
                )
                return

            # Format as embed
            embed = discord.Embed(
                title=f"🚀 Upcoming TGEs (Next {days} Days)",
                description=f"Found **{len(result.data)}** upcoming launches",
                color=discord.Color.blue(),
                timestamp=datetime.now(),
            )

            for tge in result.data[:10]:  # Limit to 10 to avoid hitting embed limits
                # Format date
                ido_date = datetime.fromisoformat(tge["ido_date"].replace("Z", "+00:00"))
                date_str = ido_date.strftime("%b %d, %Y")
                days_until = (ido_date - datetime.now().replace(tzinfo=ido_date.tzinfo)).days

                # Build field value
                field_value = f"📅 **{date_str}** ({days_until} days)\n"

                if tge.get("sale_price"):
                    field_value += f"💰 Sale Price: ${tge['sale_price']}\n"

                if tge.get("launchpad"):
                    field_value += f"🎯 Launchpad: {tge['launchpad']}\n"

                if tge.get("blockchain"):
                    field_value += f"⛓️ Chain: {tge['blockchain']}\n"

                if tge.get("conviction_score"):
                    field_value += f"🎯 Conviction: {tge['conviction_score']}/10\n"

                embed.add_field(
                    name=f"{tge['project_symbol']} - {tge['project_name']}",
                    value=field_value,
                    inline=False,
                )

            embed.set_footer(text="Powered by CryptoRank.io")

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(f"✅ {interaction.user.name} viewed {len(result.data)} upcoming TGEs")

        except Exception as e:
            logger.error(f"❌ Error fetching upcoming TGEs: {e}")
            await interaction.followup.send(f"❌ **Error fetching TGEs:** {str(e)}", ephemeral=True)

    @app_commands.command(
        name="scan-unlocks", description="Scan CryptoRank for token unlock events"
    )
    @app_commands.describe(
        days="How many days ahead to scan (default: 30)",
        min_percentage="Minimum unlock percentage to include (default: 5.0)",
    )
    async def scan_unlocks(
        self,
        interaction: discord.Interaction,
        days: Optional[int] = 30,
        min_percentage: Optional[float] = 5.0,
    ):
        """Scan CryptoRank for upcoming token unlocks (shorting opportunities)."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Run scan
            stats = self.unlock_monitor.scan_and_log(days_ahead=days, min_percentage=min_percentage)

            # Format response
            embed = discord.Embed(
                title="🔓 Token Unlock Scan Complete",
                color=discord.Color.orange(),
                timestamp=datetime.now(),
            )

            embed.add_field(
                name="📊 Scan Results",
                value=(
                    f"**Found:** {stats['found']} unlocks\n"
                    f"**Logged:** {stats['logged']} new unlocks\n"
                    f"**Skipped:** {stats['skipped']} duplicates"
                ),
                inline=False,
            )

            embed.add_field(
                name="📝 Next Steps",
                value="Use `/upcoming-unlocks` to view shorting opportunities",
                inline=False,
            )

            embed.set_footer(text="Powered by CryptoRank.io")

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(
                f"✅ {interaction.user.name} scanned unlocks: "
                f"{stats['found']} found, {stats['logged']} logged"
            )

        except Exception as e:
            logger.error(f"❌ Error scanning unlocks: {e}")
            await interaction.followup.send(
                f"❌ **Error scanning unlocks:** {str(e)}", ephemeral=True
            )

    @app_commands.command(name="upcoming-unlocks", description="View upcoming token unlock events")
    @app_commands.describe(days="How many days ahead to look (default: 7)")
    async def upcoming_unlocks(self, interaction: discord.Interaction, days: Optional[int] = 7):
        """View upcoming token unlock events from database."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get upcoming unlocks
            unlocks = self.unlock_monitor.get_upcoming_unlocks(days_ahead=days)

            if not unlocks:
                await interaction.followup.send(
                    f"📭 No significant unlocks found in the next {days} days.\n"
                    f"Run `/scan-unlocks` to discover shorting opportunities.",
                    ephemeral=True,
                )
                return

            # Format as embed
            embed = discord.Embed(
                title=f"🔓 Upcoming Token Unlocks (Next {days} Days)",
                description=f"Found **{len(unlocks)}** unlock events",
                color=discord.Color.red(),
                timestamp=datetime.now(),
            )

            for unlock in unlocks[:10]:  # Limit to 10
                # Format date
                unlock_date = datetime.fromisoformat(unlock["unlock_date"].replace("Z", "+00:00"))
                date_str = unlock_date.strftime("%b %d, %Y")
                days_until = (unlock_date - datetime.now().replace(tzinfo=unlock_date.tzinfo)).days

                # Build field value
                field_value = f"📅 **{date_str}** ({days_until} days)\n"

                if unlock.get("unlock_percentage"):
                    field_value += f"📊 Unlock: {unlock['unlock_percentage']}% of supply\n"

                if unlock.get("unlock_amount"):
                    field_value += f"💰 Amount: {unlock['unlock_amount']:,.0f} tokens\n"

                if unlock.get("unlock_type"):
                    field_value += f"🏷️ Type: {unlock['unlock_type'].title()}\n"

                # Color code by severity
                emoji = "🔴" if unlock.get("unlock_percentage", 0) >= 10 else "🟡"

                embed.add_field(
                    name=f"{emoji} {unlock['project_symbol']} - {unlock.get('project_name', 'N/A')}",
                    value=field_value,
                    inline=False,
                )

            embed.set_footer(text="Powered by CryptoRank.io • 🔴 Major (>10%) 🟡 Moderate")

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(f"✅ {interaction.user.name} viewed {len(unlocks)} upcoming unlocks")

        except Exception as e:
            logger.error(f"❌ Error fetching upcoming unlocks: {e}")
            await interaction.followup.send(
                f"❌ **Error fetching unlocks:** {str(e)}", ephemeral=True
            )


async def setup(bot: commands.Bot):
    """Load the CryptoRank commands cog."""
    await bot.add_cog(CryptoRankCommands(bot))
