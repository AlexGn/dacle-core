"""
DACLE Discord Bot
Main bot implementation for monitoring Discord messages and tracking project mentions
"""

import sys
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.knowledge.supabase_client import get_knowledge_base
from src.monitoring.health import HealthCheckServer, get_health_status, run_periodic_health_checks
from src.utils.config import get_discord_config, load_config
from src.utils.logger import get_logger

# Load configuration explicitly at startup
load_config()

# Logger will be initialized in run_bot() after config is loaded
logger = None


class DACLEBot(commands.Bot):
    """
    DACLE Discord Bot
    Monitors Discord messages to track crypto project mentions from researchers
    """

    def __init__(self):
        """Initialize the DACLE bot with proper intents"""
        # Configure intents - we need messages and guild members
        intents = discord.Intents.default()
        intents.message_content = True  # Required to read message text
        intents.members = True  # Required to track researcher info
        intents.guilds = True  # Required for guild/server info

        # Initialize bot with command prefix
        super().__init__(
            # Session 396: Support mentions for "analyze" command
            command_prefix=commands.when_mentioned_or("!dacle "),
            intents=intents,
            help_command=None,  # We'll create custom help
        )

        # Load Discord config with error handling
        try:
            self.config = get_discord_config()
            self.private_server_id = int(self.config.private_server_id)
        except ValueError as e:
            logger.error(f"❌ Failed to load Discord config: {e}")
            logger.error("Ensure DISCORD_BOT_TOKEN and DISCORD_PRIVATE_SERVER_ID are set in .env")
            raise
        except Exception as e:
            logger.error(f"❌ Unexpected error loading config: {e}")
            raise

        logger.info("DACLE Bot initialized")

    async def setup_hook(self):
        """
        Called when the bot is starting up
        Load cogs (extensions) here
        """
        logger.info("Running setup hook...")

        # Load message monitor cog
        try:
            await self.load_extension("src.bot.cogs.monitor")
            logger.info("✅ Loaded monitor cog")
        except Exception as e:
            logger.error(f"❌ Failed to load monitor cog: {e}")

        # Load trade commands cog
        try:
            await self.load_extension("src.bot.cogs.trades")
            logger.info("✅ Loaded trades cog")
        except Exception as e:
            logger.error(f"❌ Failed to load trades cog: {e}")

        # Load CryptoRank commands cog
        try:
            await self.load_extension("src.bot.cogs.cryptorank_commands")
            logger.info("✅ Loaded cryptorank_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load cryptorank_commands cog: {e}")

        # Load Whales Market OTC commands cog
        try:
            await self.load_extension("src.bot.cogs.otc_commands")
            logger.info("✅ Loaded otc_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load otc_commands cog: {e}")

        # Load daily briefing cog
        try:
            await self.load_extension("src.bot.cogs.briefing")
            logger.info("✅ Loaded briefing cog")
        except Exception as e:
            logger.error(f"❌ Failed to load briefing cog: {e}")

        # Load macro slash commands cog
        try:
            await self.load_extension("src.bot.cogs.macro_commands")
            logger.info("✅ Loaded macro_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load macro_commands cog: {e}")

        # Load Analysis commands (Python-native analyze command)
        try:
            await self.load_extension("src.bot.cogs.analysis_commands")
            logger.info("✅ Loaded analysis_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load analysis_commands cog: {e}")

        # Load Trade Router (Deterministic parsing)
        try:
            await self.load_extension("src.bot.cogs.trade_router")
            logger.info("✅ Loaded trade_router cog")
        except Exception as e:
            logger.error(f"❌ Failed to load trade_router cog: {e}")

        # Load Scout Commands (Self-Evolution Audit)
        try:
            await self.load_extension("src.bot.cogs.scout_commands")
            logger.info("✅ Loaded scout_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load scout_commands cog: {e}")

        # Sync slash commands with Discord
        try:
            # Fast guild sync for private server (avoid global duplicates)
            guild = discord.Object(id=self.private_server_id)

            # Capture current commands before clearing globals
            current_commands = list(self.tree.get_commands())

            # Clear global commands on Discord by syncing an empty global set
            self.tree.clear_commands(guild=None)
            cleared_global = await self.tree.sync()
            logger.info(f"🧹 Cleared {len(cleared_global)} global slash command(s)")

            # Clear guild commands to avoid duplicate entries (guild + global residue)
            self.tree.clear_commands(guild=guild)
            cleared_guild = await self.tree.sync(guild=guild)
            logger.info(f"🧹 Cleared {len(cleared_guild)} guild slash command(s)")

            # Re-add commands as guild-scoped only, then sync to guild
            for cmd in current_commands:
                self.tree.add_command(cmd, guild=guild)

            guild_synced = await self.tree.sync(guild=guild)
            logger.info(f"✅ Synced {len(guild_synced)} guild slash command(s)")
        except Exception as e:
            logger.error(f"❌ Failed to sync commands: {e}")

        logger.info("Setup complete")

    async def on_ready(self):
        """Called when the bot has successfully connected to Discord"""
        logger.info(f"✅ Bot connected as {self.user} (ID: {self.user.id})")
        logger.info(f"📡 Connected to {len(self.guilds)} guild(s)")

        # Mark bot as ready for health checks (HIGH-REL-001)
        get_health_status().set_bot_ready(True)

        # Verify we're in the private server
        private_server = self.get_guild(self.private_server_id)
        if private_server:
            logger.info(f"✅ Found private server: {private_server.name}")

            # List all channels the bot can see
            logger.info(f"📋 Channels in {private_server.name}:")
            for channel in private_server.text_channels:
                perms = channel.permissions_for(private_server.me)
                logger.info(
                    f"  - #{channel.name}: "
                    f"view={perms.view_channel}, "
                    f"read_history={perms.read_message_history}, "
                    f"send={perms.send_messages}"
                )
        else:
            logger.warning(f"⚠️  Private server (ID: {self.private_server_id}) not found!")

        # Set bot presence/status
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="crypto signals 📊")
        )

        # Start periodic health checks for database and Redis
        logger.info("Starting periodic health checks...")
        kb = get_knowledge_base()
        # Pass the actual Supabase client for health checks
        self.loop.create_task(run_periodic_health_checks(kb.client, redis_client=None))

    async def on_message(self, message: discord.Message):
        """
        Custom on_message handler to handle double spaces after mentions
        and other formatting issues that break command parsing.
        """
        # Ignore bot messages
        if message.author.bot:
            return

        # If the bot is mentioned, clean up extra spaces after the mention
        if self.user.mentioned_in(message):
            mention_str = f"<@{self.user.id}>"
            mention_nick_str = f"<@!{self.user.id}>"
            
            content = message.content
            if content.startswith(mention_str):
                # Replace mention + any number of spaces with mention + single space
                import re
                content = re.sub(rf"^{re.escape(mention_str)}\s+", f"{mention_str} ", content)
                message.content = content
            elif content.startswith(mention_nick_str):
                import re
                content = re.sub(rf"^{re.escape(mention_nick_str)}\s+", f"{mention_nick_str} ", content)
                message.content = content

        # Process commands
        await self.process_commands(message)

    async def on_error(self, event: str, *args, **kwargs):
        """Handle errors"""
        logger.error(f"Error in {event}", exc_info=True)


def run_bot():
    """Main entry point to run the bot"""
    # Initialize logger now that config is loaded
    global logger
    logger = get_logger(__name__)

    logger.info("Starting DACLE Discord Bot...")

    # Start health check HTTP server (HIGH-REL-001)
    health_server = HealthCheckServer(host="0.0.0.0", port=8081)
    health_server.start()
    logger.info("Health check server started on http://0.0.0.0:8081")

    try:
        # Create bot instance (will load config in __init__)
        bot = DACLEBot()

        # Get Discord token
        config = get_discord_config()

        # Run the bot
        bot.run(config.bot_token, log_handler=None)  # We use our own logger
    except ValueError as e:
        logger.error(f"❌ Configuration error: {e}")
        logger.error("Please check your .env file and ensure all required variables are set")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        health_server.stop()
    except Exception as e:
        logger.error(f"Bot crashed: {e}", exc_info=True)
        health_server.stop()
        raise
    finally:
        # Mark bot as not ready on shutdown
        get_health_status().set_bot_ready(False)


if __name__ == "__main__":
    run_bot()
