"""
DACLE Discord Bot
Main bot implementation for monitoring Discord messages and tracking project mentions
"""

import sys
import asyncio
import os
import time
import re
import subprocess
import httpx
from datetime import datetime, timezone
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
from src.bot.health import bot_health_check
from src.bot.utils.memory_guard import (
    get_mem_available_mb,
    get_memory_alert_mb,
    should_skip_sync,
)
from src.bot.utils.interaction_response import safe_send
from src.bot.runtime_routing import get_bot_api_base_url

# Load configuration explicitly at startup if not already loaded
try:
    load_config()
except RuntimeError:
    pass

# Logger will be initialized in run_bot() after config is loaded
logger = None

STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "what", "when", "where",
    "which", "into", "about", "have", "how", "your", "does", "will", "would", "can",
    "could", "should", "are", "was", "were", "but", "you", "all", "any", "not", "use",
    "those", "these", "them", "then", "run", "do", "workflow", "process", "check",
}

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
            self._last_pulse_time = 0  # Session 470: Pulse Dot Heartbeat
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

        # Load scan cog (stripped-down briefing — /scan only)
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

        # Load Sync commands (owner-only slash sync)
        try:
            await self.load_extension("src.bot.cogs.sync_commands")
            logger.info("✅ Loaded sync_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load sync_commands cog: {e}")

        # Load manual refresh command (/update)
        try:
            await self.load_extension("src.bot.cogs.update_commands")
            logger.info("✅ Loaded update_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load update_commands cog: {e}")

        # Trade Router cog: on_message parsing is handled by Node.js (Session 408).
        # Python cog re-enabled for /rerun slash command only (no on_message listener).
        try:
            await self.load_extension("src.bot.cogs.trade_router")
            logger.info("✅ Loaded trade_router cog")
        except Exception as e:
            logger.error(f"❌ Failed to load trade_router cog: {e}")

        # Load Position Commands
        try:
            await self.load_extension("src.bot.cogs.position_commands")
            logger.info("✅ Loaded position_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load position_commands cog: {e}")

        # Load Performance Commands (behavioral analysis + compounding)
        try:
            await self.load_extension("src.bot.cogs.performance_commands")
            logger.info("✅ Loaded performance_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load performance_commands cog: {e}")

        # Load Heartbeat Action Cards cog (Sprint 1b)
        try:
            await self.load_extension("src.bot.cogs.heartbeat_cog")
            logger.info("✅ Loaded heartbeat_cog")
        except Exception as e:
            logger.error(f"❌ Failed to load heartbeat_cog: {e}")

        # Load TA Card Commands (unified /ta command — Session 440)
        try:
            await self.load_extension("src.bot.cogs.ta_commands")
            logger.info("✅ Loaded ta_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load ta_commands cog: {e}")

        # Load Scalper Commands (/scalper — Session 445)
        try:
            await self.load_extension("src.bot.cogs.scalper_commands")
            logger.info("✅ Loaded scalper_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load scalper_commands cog: {e}")

        # Load Prop Firm Commands (/show)
        try:
            await self.load_extension("src.bot.cogs.prop_commands")
            logger.info("✅ Loaded prop_commands cog")
        except Exception as e:
            logger.error(f"❌ Failed to load prop_commands cog: {e}")

        # Log app commands discovered (sync happens on_ready when guild is available)
        app_commands_list = list(self.tree.get_commands())
        logger.info(f"🔎 App commands discovered: {len(app_commands_list)}")
        if app_commands_list:
            names = ", ".join(cmd.name for cmd in app_commands_list)
            logger.info(f"🔎 App command names: {names}")
        else:
            logger.warning("⚠️ No app commands registered before sync")

        # Register global app_command error handler for visibility
        @self.tree.error
        async def on_app_command_error(interaction: discord.Interaction, error: Exception):
            command_name = interaction.command.name if interaction.command else "unknown"
            logger.error(
                f"APP_COMMAND_ERROR command={command_name} "
                f"user={interaction.user} error={error}",
                exc_info=error,
            )
            await safe_send(
                interaction,
                command_name=command_name,
                logger=logger,
                content=f"An error occurred: {error}",
                ephemeral=True,
            )

        logger.info("Setup complete")

    async def on_ready(self):
        """Called when the bot has successfully connected to Discord"""
        logger.info(f"✅ Bot connected as {self.user} (ID: {self.user.id})")
        logger.info(f"📡 Connected to {len(self.guilds)} guild(s)")
        try:
            guild_ids = ", ".join(str(guild.id) for guild in self.guilds)
            logger.info(f"📡 Guild IDs: {guild_ids}")
        except Exception:
            pass

        # Mark bot as ready for health checks (HIGH-REL-001)
        get_health_status().set_bot_ready(True)

        # Verify we're in the private server
        private_server = self.get_guild(self.private_server_id)
        if private_server:
            logger.info(f"✅ Found private server: {private_server.name}")
            # Sync slash commands in the background to avoid blocking on_ready
            self.loop.create_task(self._sync_guild_commands(private_server))
            # Start memory watchdog in background
            self.loop.create_task(self._memory_watchdog())
            # Start legacy clawdbot process watchdog in background
            self.loop.create_task(self._legacy_process_watchdog())

            # List all channels the bot can see
            logger.info(f"📋 Channels in {private_server.name}:")
            for channel in private_server.text_channels:
                perms = channel.permissions_for(private_server.me)
                logger.info(
                    f"  - #{channel.name} (ID: {channel.id}): "
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
        self.loop.create_task(run_periodic_health_checks(kb.client, redis_client=None))

        # --- NEW: Master Sentinel Orchestration (Watcher + Macro Mastery) ---
        self.loop.create_task(self._run_sentinel_pulse())

    async def _run_sentinel_pulse(self) -> None:
        """
        Master Sentinel Loop: Coordinates capital protection and Macro Mastery.
        """
        from src.monitoring.the_watcher import TheWatcher
        watcher = TheWatcher(dry_run=False)
        logger.info("🛡️ THE SENTINEL: Active and taking the lead (Capital Shield + Macro Mastery engaged).")
        
        while not self.is_closed():
            try:
                # 0. Heartbeat "Pulse Dot" (Every 60 minutes)
                now = time.time()
                last_pulse = getattr(self, "_last_pulse_time", 0)
                if (now - last_pulse) >= 3600:
                    try:
                        # Use logs channel from config (Session 522 Hardening)
                        config = getattr(self, "config", None)
                        logs_channel_id = getattr(config, 'logs_channel_id', None) if config else None
                        pulse_symbol = getattr(config, 'heartbeat_pulse_symbol', '🟢') if config else '🟢'
                        if logs_channel_id:
                            channel = self.get_channel(int(logs_channel_id))
                            if channel:
                                await channel.send(pulse_symbol)
                                self._last_pulse_time = now
                                logger.info(f"💓 SENTINEL: Heartbeat pulse ({pulse_symbol}) sent to logs channel.")
                    except Exception as pulse_err:
                        logger.warning(f"⚠️ Pulse dot failed: {pulse_err}")

                # A. Watcher: Sweep positions (Every cycle)
                await watcher.watch_cycle()
                
                # B. Sentinel: Scheduled tasks
                now = datetime.now(timezone.utc)
                
                # 1. Macro Refresh (Every 4 hours at :00)
                if now.hour % 4 == 0 and now.minute < 5:
                    logger.info("🛡️ SENTINEL: Refreshing 10-signal macro engine...")
                    subprocess.Popen(["python3", "scripts/ops/refresh_macro_levels.py"])
                
                # 2. Direction Shift Sentinel (Every hour at :05)
                if now.minute >= 5 and now.minute < 10:
                    logger.info("🛡️ SENTINEL: Checking for Macro-confirmed direction shifts...")
                    subprocess.Popen(["python3", "scripts/monitors/direction_shift_sentinel.py", "--post"])
                
                # 3. Nightly Synthesis (Once daily at 00:00)
                if now.hour == 0 and now.minute < 5:
                    logger.info("🛡️ SENTINEL: Starting Nightly Cognitive Synthesis...")
                    subprocess.Popen(["python3", "scripts/scheduled/nightly_synthesis.py"])
                
                # 4. Morning Intelligence (Once daily at 06:00)
                if now.hour == 6 and now.minute < 5:
                    logger.info("🛡️ SENTINEL: Generating Macro-Aware Morning picks...")
                    subprocess.Popen(["python3", "scripts/scheduled/macro_morning_pipeline.py", "--post"])

            except Exception as e:
                logger.error(f"🛡️ THE SENTINEL PULSE ERROR: {e}")
            
            # Pulse every 5 minutes
            await asyncio.sleep(300)

    async def _run_watcher_loop(self) -> None:
        """
        Backward-compatible watcher loop shim.
        Older runtime paths still schedule _run_watcher_loop from on_ready.
        """
        from src.monitoring.the_watcher import TheWatcher
        watcher = TheWatcher(dry_run=False)
        logger.info("🛡️ WATCHER LOOP shim active (legacy on_ready compatibility).")
        while not self.is_closed():
            try:
                await watcher.watch_cycle()
            except Exception as e:
                logger.error(f"🛡️ WATCHER LOOP ERROR: {e}")
            await asyncio.sleep(300)

    async def _memory_watchdog(self) -> None:
        """Periodic memory watchdog with optional Telegram alert."""
        last_alert_ts = 0.0
        alert_cooldown = 15 * 60  # 15 minutes
        alert_threshold = get_memory_alert_mb()
        while not self.is_closed():
            mem_available_mb = get_mem_available_mb()
            if mem_available_mb is not None and mem_available_mb < alert_threshold:
                now = time.time()
                if now - last_alert_ts > alert_cooldown:
                    last_alert_ts = now
                    msg = (
                        f"🚨 DACLE Bot low memory: {mem_available_mb}MB available "
                        f"(threshold {alert_threshold}MB)."
                    )
                    logger.warning(msg)
                    try:
                        from src.alerts.telegram_notifier import send_telegram_message
                        send_telegram_message(msg)
                    except Exception as e:
                        logger.error(f"Failed to send Telegram memory alert: {e}")
            await asyncio.sleep(60)

    @staticmethod
    def _extract_legacy_clawdbot_processes(ps_output: str) -> list[str]:
        """Return command lines that match legacy clawdbot process names."""
        matches = []
        for line in ps_output.splitlines():
            if not line.strip():
                continue
            parts = line.split(None, 10)
            cmd = parts[10] if len(parts) > 10 else line
            if re.search(r"\bclawdbot[\w\-]*\b", cmd, flags=re.IGNORECASE):
                matches.append(cmd.strip())
        return matches

    def _scan_legacy_clawdbot_processes(self) -> list[str]:
        """Scan current process table for legacy clawdbot* processes."""
        try:
            proc = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            return self._extract_legacy_clawdbot_processes(proc.stdout)
        except Exception as e:
            logger.warning(f"Legacy process watchdog scan failed: {e}")
            return []

    @staticmethod
    def _is_legacy_service_active() -> bool:
        """Check if the masked clawdbot-gateway.service has been re-enabled.

        The legitimate ClawdBot gateway runs via nohup (not systemd), so
        process-name matching alone produces false positives.  The real
        threat is someone unmasking and starting the systemd unit.
        """
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", "clawdbot-gateway.service"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            state = proc.stdout.strip().lower()
            return state in {"active", "activating", "reloading"}
        except Exception:
            return False

    async def _legacy_process_watchdog(self) -> None:
        """
        Alert when the retired clawdbot-gateway.service systemd unit becomes
        active.  Only fires when the *service* is running — the nohup-launched
        ClawdBot (OpenClaw/Pi-AI) gateway is expected and not flagged.
        """
        interval = int(os.getenv("LEGACY_PROCESS_WATCHDOG_INTERVAL_SEC", "60"))
        previously_active = False
        while not self.is_closed():
            currently_active = self._is_legacy_service_active()
            if currently_active and not previously_active:
                matches = self._scan_legacy_clawdbot_processes()
                sample = "; ".join(matches[:3]) if matches else "(no ps detail)"
                msg = (
                    "🚨 Legacy clawdbot-gateway.service is ACTIVE. "
                    f"Masked service was re-enabled! Processes: {sample}"
                )
                logger.error(msg)
                try:
                    from src.alerts.telegram_notifier import send_telegram_message
                    send_telegram_message(msg, parse_mode=None)
                except Exception as e:
                    logger.warning(f"Failed to send legacy-service alert: {e}")
            previously_active = currently_active
            await asyncio.sleep(max(interval, 1))

    def _get_owner_id(self) -> Optional[int]:
        owner_id = os.getenv("DISCORD_OWNER_ID")
        if not owner_id:
            return None
        try:
            return int(owner_id)
        except ValueError:
            return None

    def _is_owner(self, user_id: int) -> bool:
        owner_id = self._get_owner_id()
        return owner_id is not None and user_id == owner_id

    def _strip_leading_mention(self, content: str) -> str:
        if not self.user:
            return content.strip()
        pattern = rf"^\s*<@!?{self.user.id}>\s*"
        return re.sub(pattern, "", content, count=1).strip()

    def _extract_mention_command(self, content: str) -> Optional[str]:
        stripped = self._strip_leading_mention(content)
        if not stripped:
            return None
        return stripped.split()[0].lower()

    @staticmethod
    def _get_api_url() -> str:
        return get_bot_api_base_url()

    async def _query_agent_endpoint(
        self,
        query: str,
        user_id: int,
        channel_id: int,
        message_id: int,
    ) -> dict:
        """
        Call /api/agent/query for non-command mention messages.
        Returns dict with 'answer' or 'error' message.
        """
        url = f"{self._get_api_url()}/api/agent/query"
        payload = {
            "query": query,
            "user_id": user_id,
            "channel_id": channel_id,
            "message_id": message_id,
        }
        
        headers = {}
        api_key = os.getenv("DACLE_API_KEY")
        if api_key:
            headers["X-API-Key"] = api_key

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, json=payload, headers=headers)

                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict) and data.get("answer"):
                            return data
                        return {"error": "Gateway returned 200 but no answer was found."}

                    if resp.status_code == 401:
                        return {"error": "Authentication failed (401). Check if DACLE_API_KEY is correctly set in the bot's environment."}

                    if resp.status_code == 429:
                        return {"error": "AI rate limit reached upstream. Please retry in a few minutes."}

                    if resp.status_code == 500:
                        try:
                            err_detail = resp.json().get("detail", "Internal Server Error")
                            return {"error": f"Gateway Error (500): {err_detail}"}
                        except Exception:
                            return {"error": "Gateway Error (500): The AI service crashed or is offline."}

                    return {"error": f"Gateway returned error code {resp.status_code}."}

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < max_attempts:
                    await asyncio.sleep(0.6 * attempt)
                    continue
                if isinstance(e, httpx.ConnectError):
                    return {"error": "Could not connect to DACLE Gateway (API may be restarting). Please retry in a few seconds."}
                return {"error": "Gateway timeout. The AI is taking too long to respond."}
            except Exception as e:
                logger.warning(f"Agent query failed for message {message_id}: {e}")
                return {"error": f"Unexpected gateway error: {str(e)}"}

    async def _sync_guild_commands(self, guild: discord.Guild) -> None:
        """Sync slash commands to the given guild without blocking on_ready."""
        try:
            mem_available_mb = get_mem_available_mb()
            if should_skip_sync(mem_available_mb):
                logger.warning(
                    "⚠️ Skipping slash command sync due to low memory "
                    f"({mem_available_mb}MB available)"
                )
                return
            self.tree.copy_global_to(guild=guild)
            logger.info(f"🔄 Syncing guild slash commands in background to {guild.id}...")
            synced = await self.tree.sync(guild=guild)
            logger.info(f"✅ Synced {len(synced)} guild slash command(s) in background")
        except Exception as e:
            logger.error(f"❌ Failed to sync commands in background: {e}")

    async def on_message(self, message: discord.Message):
        """
        Custom on_message handler to handle double spaces after mentions
        and other formatting issues that break command parsing.
        """
        # --- 1. AUDIT INTERCEPT (Works for bots too) ---
        if message.content.startswith("!audit ") or message.content.lower().startswith("deep audit "):
            if message.content.startswith("!audit "):
                symbol = message.content.replace("!audit ", "").strip().upper()
            else:
                # Handle "deep audit SYMBOL ..."
                parts = message.content.split(" ")
                symbol = parts[2].upper() if len(parts) > 2 else ""
                symbol = re.sub(r"[^A-Z0-9]", "", symbol)

            if symbol and symbol not in STOP_WORDS:
                analysis_cog = self.get_cog("AnalysisCommands")
                if analysis_cog:
                    logger.info(f"AUDIT_TRIGGER: Intercepted trigger for {symbol} from {message.author}")
                    self.loop.create_task(analysis_cog._run_native_audit(message.channel, symbol, message.author.mention))
                return

        # Ignore other bot messages
        if message.author.bot:
            return

        # --- 2. MENTION & SYNC HANDLING ---
        if self.user.mentioned_in(message):
            mention_command = self._extract_mention_command(message.content)
            audit_channel_id = 1474325144913838232

            # Sync handling
            if "sync" in message.content.lower():
                is_owner = self._is_owner(message.author.id)
                is_audit_channel = message.channel.id == audit_channel_id
                
                if is_owner or is_audit_channel:
                    private_server = self.get_guild(self.private_server_id)
                    if private_server:
                        await message.channel.send("🔄 Audit-channel sync triggered...")
                        try:
                            await self._sync_guild_commands(private_server)
                            await message.channel.send("✅ Sync complete. /audit-full should be visible shortly.")
                        except Exception as e:
                            await message.channel.send(f"❌ Sync failed: {e}")
                    return
                else:
                    await message.channel.send("❌ Sync is restricted to owner or #audit-token channel.")
                    return

            # Clean up mentions
            mention_str = f"<@{self.user.id}>"
            mention_nick_str = f"<@!{self.user.id}>"
            content = message.content
            if content.startswith(mention_str):
                content = re.sub(rf"^{re.escape(mention_str)}\s+", f"{mention_str} ", content)
                message.content = content
            elif content.startswith(mention_nick_str):
                content = re.sub(rf"^{re.escape(mention_nick_str)}\s+", f"{mention_nick_str} ", content)
                message.content = content

            # Handle non-command mentions via Agent Endpoint
            known_text_commands = {"analyze", "ping", "sync", "status"}
            if mention_command not in known_text_commands:
                query_text = self._strip_leading_mention(message.content)
                async with message.channel.typing():
                    agent_result = await self._query_agent_endpoint(
                        query=query_text,
                        user_id=message.author.id,
                        channel_id=message.channel.id,
                        message_id=message.id,
                    )
                
                trace_id = agent_result.get("trace_id", f"msg-{message.id}")
                if "answer" in agent_result:
                    answer = str(agent_result["answer"]).strip()
                    msg = await message.channel.send(f"{answer}\n\nTrace: `{trace_id}`")
                    if "intent:ai_synthesis" in agent_result.get("actions_taken", []):
                        try:
                            await msg.add_reaction("👍")
                            await msg.add_reaction("👎")
                        except Exception: pass
                else:
                    error_msg = agent_result.get("error", "Unknown error")
                    await message.channel.send(f"❌ **AI Unavailable**: {error_msg}\n\nTrace: `{trace_id}`")
                return

        # --- 3. STANDARD COMMAND PROCESSING ---
        await self.process_commands(message)

    async def on_error(self, event: str, *args, **kwargs):
        """Handle errors"""
        logger.error(f"Error in {event}", exc_info=True)

    def is_healthy(self) -> dict:
        """Return bot health status for /api/health integration."""
        return bot_health_check(self)

    @commands.command(name="status")
    async def status(self, ctx: commands.Context):
        """Check if the bot is alive"""
        await ctx.send(f"✅ DACLE Bot is online and responsive!\n- Latency: {round(self.latency * 1000)}ms\n- Guilds: {len(self.guilds)}")

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle thumbs up/down feedback on AI responses."""
        if payload.user_id == self.user.id:
            return

        if str(payload.emoji) not in ("👍", "👎"):
            return

        # Check if the message was from the bot and had a trace ID
        channel = self.get_channel(payload.channel_id)
        if not channel:
            return
            
        try:
            message = await channel.fetch_message(payload.message_id)
            if message.author.id != self.user.id:
                return

            # Extract trace ID using regex
            match = re.search(r"Trace: `(trace-[a-z0-9\-]+)`", message.content)
            if not match:
                return

            trace_id = match.group(1)
            is_positive = str(payload.emoji) == "👍"

            # Call backend to record feedback
            url = f"{self._get_api_url()}/api/agent/feedback"
            async with httpx.AsyncClient(timeout=5.0) as client:
                headers = {}
                api_key = os.getenv("DACLE_API_KEY")
                if api_key:
                    headers["X-API-Key"] = api_key
                    
                await client.post(
                    url, 
                    json={"trace_id": trace_id, "is_positive": is_positive},
                    headers=headers
                )
                
            logger.info(f"Recorded Discord feedback for {trace_id}: {payload.emoji}")
            
        except Exception as e:
            logger.warning(f"Failed to process reaction feedback: {e}")


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
