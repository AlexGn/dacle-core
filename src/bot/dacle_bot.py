"""
DACLE Discord Bot
Main bot implementation for monitoring Discord messages and tracking project mentions
"""

import sys
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import get_discord_config
from utils.logger import get_logger

logger = get_logger(__name__)


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
            command_prefix="!dacle ",  # Commands start with !dacle
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
            await self.load_extension("bot.cogs.monitor")
            logger.info("✅ Loaded monitor cog")
        except Exception as e:
            logger.error(f"❌ Failed to load monitor cog: {e}")

        # Load trade commands cog
        try:
            await self.load_extension("bot.cogs.trades")
            logger.info("✅ Loaded trades cog")
        except Exception as e:
            logger.error(f"❌ Failed to load trades cog: {e}")

        # Load CryptoRank commands cog
        try:
            await self.load_extension("bot.cogs.cryptorank_commands")
            logger.info("✅ Loaded cryptorank_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load cryptorank_commands cog: {e}")

        # Load Whales Market OTC commands cog
        try:
            await self.load_extension("bot.cogs.otc_commands")
            logger.info("✅ Loaded otc_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load otc_commands cog: {e}")

        # Load daily briefing cog
        try:
            await self.load_extension("bot.cogs.briefing")
            logger.info("✅ Loaded briefing cog")
        except Exception as e:
            logger.error(f"❌ Failed to load briefing cog: {e}")

        # Sync slash commands with Discord
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ Synced {len(synced)} slash command(s)")
        except Exception as e:
            logger.error(f"❌ Failed to sync commands: {e}")

        logger.info("Setup complete")

    async def on_ready(self):
        """Called when the bot has successfully connected to Discord"""
        logger.info(f"✅ Bot connected as {self.user} (ID: {self.user.id})")
        logger.info(f"📡 Connected to {len(self.guilds)} guild(s)")

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
            logger.warning(
                f"⚠️  Private server (ID: {self.private_server_id}) not found!"
            )

        # Set bot presence/status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="crypto signals 📊"
            )
        )

    async def on_message(self, message: discord.Message):
        """
        Called whenever a message is sent in any channel the bot can see

        Args:
            message: The Discord message object
        """
        # Ignore messages from the bot itself
        if message.author.bot:
            return

        # Log ALL messages for debugging (changed from debug to info)
        logger.info(
            f"📨 Message received from {message.author.name} in "
            f"{'#' + message.channel.name if message.guild else 'DM'}: {message.content[:100]}"
        )

        # Only process messages from the private server
        if message.guild and message.guild.id == self.private_server_id:
            logger.info(f"✅ Message is from private server, will be processed")
        else:
            logger.warning(f"⚠️  Message NOT from private server (guild_id: {message.guild.id if message.guild else 'None'})")

        # Process commands (if any)
        await self.process_commands(message)

    async def on_error(self, event: str, *args, **kwargs):
        """Handle errors"""
        logger.error(f"Error in {event}", exc_info=True)


def run_bot():
    """Main entry point to run the bot"""
    logger.info("Starting DACLE Discord Bot...")

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
    except Exception as e:
        logger.error(f"Bot crashed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    run_bot()
