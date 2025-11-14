"""
Daily Briefing Discord Cog

Provides:
- /briefing command - Generate briefing on demand
- Scheduled 8:00 AM EST daily delivery via DM
- /briefing-subscribe - Subscribe to daily briefing DMs
- /briefing-unsubscribe - Unsubscribe from daily briefing
"""

import logging
from datetime import datetime, time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from supabase import create_client

from src.briefing.daily_briefing import DailyBriefingGenerator
from src.briefing.discord_formatter import BriefingDiscordFormatter
from src.briefing.macro_events import MacroEventsTracker
from src.conviction.tge_pipeline import PipelineResult
from src.knowledge.supabase_client import get_knowledge_base
from src.tge.alert_generator import TGEAlertGenerator
from src.utils.config import SupabaseConfig

logger = logging.getLogger(__name__)


class BriefingCog(commands.Cog):
    """
    Daily briefing system for DACLE.

    Features:
    - On-demand briefing generation (/briefing)
    - Scheduled daily delivery at 8:00 AM EST
    - Subscriber management
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Initialize briefing components
        self.kb = get_knowledge_base()
        self.generator = DailyBriefingGenerator(self.kb)
        self.formatter = BriefingDiscordFormatter()

        # Initialize TGE alert generator (Session 30)
        self.alert_generator = TGEAlertGenerator()

        # Initialize macro events tracker
        config = SupabaseConfig.from_env()
        supabase_client = create_client(config.url, config.key)
        self.macro_tracker = MacroEventsTracker(supabase_client)

        # Subscriber list (in-memory for Phase 1, DB in Phase 2)
        # Format: {user_id: {"subscribed_at": datetime, "dm_channel": discord.DMChannel}}
        self.subscribers = {}

        logger.info("BriefingCog initialized with TGE alert capabilities")

    async def cog_load(self):
        """Called when cog is loaded - start scheduled tasks."""
        # Start daily briefing scheduler
        self.daily_briefing_task.start()
        logger.info("Daily briefing scheduler started (8:00 AM EST)")

    async def cog_unload(self):
        """Called when cog is unloaded - stop scheduled tasks."""
        self.daily_briefing_task.cancel()
        logger.info("Daily briefing scheduler stopped")

    @tasks.loop(time=time(hour=13, minute=0))  # 8:00 AM EST = 13:00 UTC
    async def daily_briefing_task(self):
        """
        Scheduled task that runs daily at 8:00 AM EST.

        Sends briefing DM to all subscribers.
        """
        logger.info("Daily briefing task triggered - generating briefing")

        try:
            # Generate briefing
            briefing = self.generator.generate_briefing(
                hours_lookback=24,
                max_opportunities=5,
                min_conviction=6.0,
            )

            # Add macro events
            macro_events = self.macro_tracker.get_upcoming_events(days_ahead=7)
            briefing["macro_events"] = macro_events

            # Format as Discord embeds
            embeds = self.formatter.format_briefing(briefing)

            # Add macro events embed if any
            if macro_events:
                macro_embed = self._create_macro_events_embed(macro_events)
                embeds.append(macro_embed)

            # Send to all subscribers
            sent_count = 0
            failed_count = 0

            for user_id, sub_info in self.subscribers.items():
                try:
                    user = await self.bot.fetch_user(user_id)
                    dm_channel = await user.create_dm()

                    # Send embeds
                    await dm_channel.send(embeds=embeds)
                    sent_count += 1

                    logger.info(f"Sent daily briefing to user {user_id}")

                except Exception as e:
                    logger.error(f"Failed to send briefing to user {user_id}: {e}")
                    failed_count += 1

            logger.info(f"Daily briefing sent: {sent_count} succeeded, {failed_count} failed")

        except Exception as e:
            logger.error(f"Error in daily briefing task: {e}")
            logger.exception("Full traceback:")

    @daily_briefing_task.before_loop
    async def before_daily_briefing_task(self):
        """Wait for bot to be ready before starting scheduler."""
        await self.bot.wait_until_ready()
        logger.info("Bot ready - daily briefing scheduler can start")

    @app_commands.command(
        name="briefing", description="Generate your daily crypto trading briefing"
    )
    @app_commands.describe(
        hours="How many hours back to scan for mentions (default: 24)",
        max_opportunities="Maximum opportunities to show (default: 5)",
        min_conviction="Minimum conviction score to include (default: 6.0)",
    )
    async def briefing_command(
        self,
        interaction: discord.Interaction,
        hours: Optional[int] = 24,
        max_opportunities: Optional[int] = 5,
        min_conviction: Optional[float] = 6.0,
    ):
        """
        Generate daily briefing on demand.

        Args:
            interaction: Discord interaction
            hours: Hours lookback (default 24)
            max_opportunities: Max opportunities to show (default 5)
            min_conviction: Min conviction score (default 6.0)
        """
        await interaction.response.defer(thinking=True)

        try:
            logger.info(
                f"Briefing requested by {interaction.user.name} "
                f"(hours={hours}, max={max_opportunities}, min_conv={min_conviction})"
            )

            # Generate briefing
            briefing = self.generator.generate_briefing(
                hours_lookback=hours,
                max_opportunities=max_opportunities,
                min_conviction=min_conviction,
            )

            # Add macro events
            macro_events = self.macro_tracker.get_upcoming_events(days_ahead=7)
            briefing["macro_events"] = macro_events

            # Format as Discord embeds
            embeds = self.formatter.format_briefing(briefing)

            # Add macro events embed
            if macro_events:
                macro_embed = self._create_macro_events_embed(macro_events)
                embeds.append(macro_embed)

            # Send embeds (split if too many)
            # Discord limit: 10 embeds per message
            if len(embeds) <= 10:
                await interaction.followup.send(embeds=embeds)
            else:
                # Send in batches of 10
                for i in range(0, len(embeds), 10):
                    batch = embeds[i : i + 10]
                    if i == 0:
                        await interaction.followup.send(embeds=batch)
                    else:
                        await interaction.channel.send(embeds=batch)

            logger.info(f"Briefing sent successfully with {len(embeds)} embeds")

        except Exception as e:
            logger.error(f"Error generating briefing: {e}")
            logger.exception("Full traceback:")

            await interaction.followup.send(
                "❌ Error generating briefing. Check logs for details.", ephemeral=True
            )

    @app_commands.command(
        name="briefing-subscribe", description="Subscribe to daily briefing DMs at 8:00 AM EST"
    )
    async def briefing_subscribe_command(self, interaction: discord.Interaction):
        """
        Subscribe to daily briefing DMs.

        Args:
            interaction: Discord interaction
        """
        user_id = interaction.user.id

        if user_id in self.subscribers:
            await interaction.response.send_message(
                "✅ You're already subscribed to daily briefings!", ephemeral=True
            )
            return

        # Add subscriber
        self.subscribers[user_id] = {
            "subscribed_at": datetime.now(),
            "username": interaction.user.name,
        }

        logger.info(f"User {interaction.user.name} ({user_id}) subscribed to daily briefing")

        await interaction.response.send_message(
            "✅ **Subscribed to Daily Briefing!**\n\n"
            "You'll receive a crypto trading briefing via DM every day at **8:00 AM EST**.\n\n"
            "The briefing includes:\n"
            "• Top 3-5 opportunities (conviction 6-10)\n"
            "• Your open positions\n"
            "• Execution reminders (high-conviction signals you haven't acted on)\n"
            "• Upcoming macro events\n\n"
            "Use `/briefing-unsubscribe` anytime to stop receiving briefings.",
            ephemeral=True,
        )

    @app_commands.command(
        name="briefing-unsubscribe", description="Unsubscribe from daily briefing DMs"
    )
    async def briefing_unsubscribe_command(self, interaction: discord.Interaction):
        """
        Unsubscribe from daily briefing DMs.

        Args:
            interaction: Discord interaction
        """
        user_id = interaction.user.id

        if user_id not in self.subscribers:
            await interaction.response.send_message(
                "ℹ️ You're not subscribed to daily briefings.", ephemeral=True
            )
            return

        # Remove subscriber
        del self.subscribers[user_id]

        logger.info(f"User {interaction.user.name} ({user_id}) unsubscribed from daily briefing")

        await interaction.response.send_message(
            "✅ **Unsubscribed from Daily Briefing**\n\n"
            "You'll no longer receive daily briefing DMs.\n"
            "You can still use `/briefing` anytime to generate a briefing on demand.",
            ephemeral=True,
        )

    @app_commands.command(
        name="briefing-subscribers", description="[Admin] View current briefing subscribers"
    )
    async def briefing_subscribers_command(self, interaction: discord.Interaction):
        """
        Show current briefing subscribers (admin only).

        Args:
            interaction: Discord interaction
        """
        # Check if user is admin (you can add proper role check here)
        # For now, just show to everyone

        if not self.subscribers:
            await interaction.response.send_message("ℹ️ No subscribers yet.", ephemeral=True)
            return

        sub_list = []
        for user_id, info in self.subscribers.items():
            username = info.get("username", "Unknown")
            subscribed_at = info.get("subscribed_at").strftime("%Y-%m-%d")
            sub_list.append(f"• {username} (subscribed: {subscribed_at})")

        message = f"📊 **Daily Briefing Subscribers ({len(self.subscribers)})**\n\n"
        message += "\n".join(sub_list)

        await interaction.response.send_message(message, ephemeral=True)

    def _create_macro_events_embed(self, events: list) -> discord.Embed:
        """Create embed for macro events."""
        embed = discord.Embed(
            title="📅 Upcoming Macro Events",
            description=f"Important events in the next 7 days",
            color=discord.Color.purple(),
            timestamp=datetime.now(),
        )

        for event in events[:10]:  # Max 10 events
            emoji = event["emoji"]
            date = event["event_date"]
            desc = event["description"]
            time_str = event.get("event_time", "")

            value = f"{emoji} {desc}"
            if time_str:
                value += f"\n⏰ {time_str}"

            projects = event.get("projects_affected")
            if projects:
                value += f"\n💎 Affects: {', '.join(projects[:3])}"

            embed.add_field(name=date, value=value, inline=False)

        if len(events) > 10:
            embed.set_footer(text=f"Showing 10 of {len(events)} events")
        else:
            embed.set_footer(text="Stay informed about market-moving events")

        return embed

    async def send_tge_alert(
        self, pipeline_result: PipelineResult, exchange_availability: Optional[dict] = None
    ) -> dict:
        """
        Send TGE alert to all briefing subscribers.

        This is called by the TGE scanner when a high-conviction opportunity
        is detected (conviction ≥8.0/10).

        Args:
            pipeline_result: Full TGE analysis from pipeline
            exchange_availability: Optional exchange status dict
                {
                    "mexc": bool,
                    "hyperliquid": bool,
                    "blofin": bool,
                    "recommended": str
                }

        Returns:
            {
                "sent": int,  # Number of successful sends
                "failed": int,  # Number of failures
                "total_subscribers": int
            }

        Example:
            result = await briefing_cog.send_tge_alert(pipeline_result)
            logger.info(f"Alert sent to {result['sent']} subscribers")
        """
        logger.info(
            f"🚨 Sending TGE alert for {pipeline_result.token_name} "
            f"(conviction: {pipeline_result.final_conviction}/10)"
        )

        try:
            # Generate alert embeds
            embeds = self.alert_generator.generate_alert(pipeline_result, exchange_availability)

            logger.info(f"Generated {len(embeds)} alert embeds")

            # Send to all subscribers
            sent_count = 0
            failed_count = 0

            for user_id, sub_info in self.subscribers.items():
                try:
                    user = await self.bot.fetch_user(user_id)
                    dm_channel = await user.create_dm()

                    # Add urgent prefix message
                    await dm_channel.send(
                        f"🚨 **HIGH CONVICTION TGE ALERT** 🚨\n"
                        f"**{pipeline_result.token_name}** scored **{pipeline_result.final_conviction}/10**\n"
                        f"⏱️ **Execute or skip within 2 hours**"
                    )

                    # Send embeds
                    await dm_channel.send(embeds=embeds)
                    sent_count += 1

                    logger.info(
                        f"Sent TGE alert to user {sub_info.get('username', user_id)} "
                        f"({user_id})"
                    )

                except Exception as e:
                    logger.error(f"Failed to send alert to user {user_id}: {e}")
                    failed_count += 1

            result = {
                "sent": sent_count,
                "failed": failed_count,
                "total_subscribers": len(self.subscribers),
            }

            logger.info(
                f"TGE alert sent: {sent_count} succeeded, {failed_count} failed "
                f"(total subscribers: {len(self.subscribers)})"
            )

            return result

        except Exception as e:
            logger.error(f"Error in send_tge_alert: {e}")
            logger.exception("Full traceback:")

            return {
                "sent": 0,
                "failed": 0,
                "total_subscribers": len(self.subscribers),
                "error": str(e),
            }


async def setup(bot: commands.Bot):
    """Setup function called by Discord.py when loading cog."""
    await bot.add_cog(BriefingCog(bot))
    logger.info("BriefingCog loaded successfully")
