"""
Message Monitor Cog
Monitors Discord messages for crypto project mentions and stores them in Supabase
"""

import re
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands

from ai.together_client import get_together_client
from knowledge.supabase_client import get_knowledge_base
from utils.logger import get_logger

logger = get_logger(__name__)


class MessageMonitor(commands.Cog):
    """
    Cog for monitoring Discord messages and extracting project mentions
    """

    # Known researchers (from database seed data)
    RESEARCHERS = {
        "austin": {"name": "Austin", "tier": 1},
        "phobia": {"name": "Phobia", "tier": 1},
        "sebastien": {"name": "Sebastien", "tier": 1},
        "seb": {"name": "Sebastien", "tier": 1},  # Alias
    }

    # Message context settings
    CONTEXT_WINDOW_SECONDS = 30  # Look back 30 seconds for related messages
    MAX_CACHED_MESSAGES_PER_USER = 5  # Keep last 5 messages per user

    def __init__(self, bot: commands.Bot):
        """Initialize the monitor cog"""
        self.bot = bot
        self.together = get_together_client()
        self.kb = get_knowledge_base()

        # Message cache: {user_id: deque([(timestamp, content), ...])}
        self.message_cache: Dict[int, deque] = {}

        logger.info("MessageMonitor cog initialized")

    def _cache_message(self, user_id: int, content: str):
        """
        Cache a message for context aggregation

        Args:
            user_id: Discord user ID
            content: Message content
        """
        if user_id not in self.message_cache:
            self.message_cache[user_id] = deque(maxlen=self.MAX_CACHED_MESSAGES_PER_USER)

        # Add message with timestamp
        self.message_cache[user_id].append((datetime.now(), content))

    def _get_recent_context(
        self, user_id: int, current_message: str, time_window_seconds: int = None
    ) -> str:
        """
        Get aggregated context from recent messages by the same user

        Args:
            user_id: Discord user ID
            current_message: Current message content
            time_window_seconds: Time window to look back (default: CONTEXT_WINDOW_SECONDS)

        Returns:
            Aggregated message content from recent context
        """
        if time_window_seconds is None:
            time_window_seconds = self.CONTEXT_WINDOW_SECONDS

        # Start with current message
        aggregated_content = current_message

        # Check if user has cached messages
        if user_id not in self.message_cache:
            return aggregated_content

        # Get cutoff time
        cutoff_time = datetime.now() - timedelta(seconds=time_window_seconds)

        # Collect recent messages within time window (excluding current one)
        recent_messages = []
        for timestamp, content in self.message_cache[user_id]:
            if timestamp >= cutoff_time and content != current_message:
                recent_messages.append(content)

        # If we have recent messages (other than current), prepend them
        if recent_messages:
            logger.info(
                f"Found {len(recent_messages)} recent message(s) from user {user_id} "
                f"within {time_window_seconds}s window"
            )
            # Combine: older messages first, then current message
            aggregated_content = "\n".join(recent_messages + [current_message])

        return aggregated_content

    def _cleanup_old_messages(self):
        """
        Clean up messages older than 5 minutes from cache to prevent memory bloat
        """
        cutoff_time = datetime.now() - timedelta(minutes=5)
        cleaned_users = []

        for user_id, messages in self.message_cache.items():
            # Filter out old messages
            messages_to_keep = deque(
                [(ts, content) for ts, content in messages if ts >= cutoff_time],
                maxlen=self.MAX_CACHED_MESSAGES_PER_USER,
            )

            if len(messages_to_keep) < len(messages):
                self.message_cache[user_id] = messages_to_keep
                cleaned_users.append(user_id)

        if cleaned_users:
            logger.debug(f"Cleaned old messages from {len(cleaned_users)} user(s)")

    def _detect_researcher(self, author_name: str, content: str) -> Optional[str]:
        """
        Detect which researcher sent the message

        Args:
            author_name: Discord username
            content: Message content

        Returns:
            Researcher name if detected, None otherwise
        """
        # First check for hashtag in message (e.g., #austin, #phobia)
        content_lower = content.lower()
        for key, info in self.RESEARCHERS.items():
            if f"#{key}" in content_lower:
                logger.info(f"Detected researcher via hashtag: #{key} -> {info['name']}")
                return info["name"]

        # Also check for @mention (e.g., @austin, @phobia)
        # Note: @ alone is enough, as Discord converts @austin to <@USER_ID> but user might type it
        for key, info in self.RESEARCHERS.items():
            if f"@{key}" in content_lower:
                logger.info(f"Detected researcher via @mention: @{key} -> {info['name']}")
                return info["name"]

        # Then check username
        author_lower = author_name.lower()
        for key, info in self.RESEARCHERS.items():
            if key in author_lower:
                logger.info(f"Detected researcher via username: {author_name} -> {info['name']}")
                return info["name"]

        return None

    def _extract_crypto_symbol(self, text: str) -> Optional[str]:
        """
        Extract crypto symbol from text (e.g., $BTC, $ETH)

        Args:
            text: Message text

        Returns:
            Symbol without $, or None
        """
        # Match $SYMBOL pattern
        match = re.search(r"\$([A-Z]{2,10})", text)
        if match:
            return match.group(1)
        return None

    async def _extract_projects_with_ai(self, content: str) -> List[Dict[str, str]]:
        """
        Use Together.ai LLM to extract project mentions from message

        Args:
            content: Message content

        Returns:
            List of dicts with 'name', 'symbol', 'sentiment'
        """
        try:
            prompt = f"""Extract cryptocurrency project mentions from this message.
Return ONLY valid JSON array with this exact format:
[{{"name": "Project Name", "symbol": "SYMBOL", "sentiment": "positive/neutral/negative"}}]

Message: {content}

If no crypto projects mentioned, return: []
"""

            messages = [
                {
                    "role": "system",
                    "content": "You are a crypto project extractor. Return only valid JSON arrays.",
                },
                {"role": "user", "content": prompt},
            ]

            response = self.together.chat_completion(
                messages, temperature=0.3, max_tokens=300
            )

            # Parse JSON response
            import json

            projects = json.loads(response.strip())

            # Validate it's a list
            if isinstance(projects, list):
                return projects
            else:
                logger.warning(f"AI returned non-list: {response}")
                return []

        except Exception as e:
            logger.error(f"Error extracting projects with AI: {e}")
            return []

    async def _store_mention(
        self,
        project_name: str,
        researcher_name: str,
        content: str,
        symbol: Optional[str] = None,
    ):
        """
        Store project mention in Supabase

        Args:
            project_name: Name of the project
            researcher_name: Who mentioned it
            content: What they said
            symbol: Project symbol (optional)
        """
        try:
            # Get or create project
            project = self.kb.get_project_by_name(project_name)
            if not project:
                logger.info(f"Creating new project: {project_name}")
                project = self.kb.create_project(
                    name=project_name,
                    symbol=symbol,
                    data={"source": "discord"},
                    first_mentioned_at=datetime.now(),
                )

            # Get researcher
            researcher = self.kb.get_researcher_by_name(researcher_name)
            if not researcher:
                logger.warning(f"Researcher not found: {researcher_name}")
                return

            # Create mention record
            mention = self.kb.create_project_mention(
                project_id=project["id"],
                researcher_id=researcher["id"],
                mentioned_at=datetime.now(),
                context=content[:500],  # Limit context length
                source="discord",
                data={},
            )

            # Generate and store embedding for semantic search
            embedding = self.together.generate_embedding(
                f"{project_name}: {content[:300]}"
            )

            # Update project with embedding
            self.kb.update_project(project["id"], {"embedding": embedding})

            logger.info(
                f"✅ Stored mention: {project_name} by {researcher_name} (mention ID: {mention['id']})"
            )

        except Exception as e:
            logger.error(
                f"Error storing mention for {project_name} by {researcher_name}: {e}"
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Listen to all messages and extract project mentions

        Args:
            message: Discord message object
        """
        # Ignore bot messages
        if message.author.bot:
            return

        # Only process messages from configured private server
        if not message.guild or message.guild.id != self.bot.private_server_id:
            return

        # Extract actual message content (handle forwarded messages)
        message_content = message.content

        # Debug: Log ALL message properties when content is empty (to diagnose forwards)
        if not message_content.strip():
            logger.info("=" * 60)
            logger.info("🔍 EMPTY MESSAGE - DEBUGGING ALL PROPERTIES")
            logger.info(f"  content: '{message.content}'")
            logger.info(f"  system_content: '{message.system_content}'")
            logger.info(f"  clean_content: '{message.clean_content}'")
            logger.info(f"  reference: {message.reference}")
            logger.info(f"  embeds: {len(message.embeds)} embed(s)")
            logger.info(f"  attachments: {len(message.attachments)} attachment(s)")
            logger.info(f"  type: {message.type}")
            logger.info(f"  flags: {message.flags}")

            if message.embeds:
                for i, embed in enumerate(message.embeds):
                    logger.info(f"  --- Embed {i} ---")
                    logger.info(f"    type: {embed.type}")
                    logger.info(f"    title: {embed.title}")
                    logger.info(f"    description: {embed.description}")
                    logger.info(f"    url: {embed.url}")
                    logger.info(f"    author: {embed.author}")
                    logger.info(f"    fields: {len(embed.fields or [])} field(s)")
            logger.info("=" * 60)

        # Debug: Check if message has reference (forwarded/replied) or embeds
        if message.reference:
            logger.info(
                f"🔗 Message has reference: resolved={'Yes' if message.reference.resolved else 'No'}, "
                f"content_empty={not message_content.strip()}"
            )

        # If message is forwarded (has reference) and content is empty, get referenced content
        if message.reference and not message_content.strip():
            try:
                # Try to fetch the referenced message if not auto-resolved
                if not message.reference.resolved:
                    logger.info("📥 Fetching referenced message...")
                    referenced_msg = await message.channel.fetch_message(message.reference.message_id)
                else:
                    referenced_msg = message.reference.resolved

                if referenced_msg and referenced_msg.content:
                    message_content = referenced_msg.content
                    logger.info(
                        f"📨 Forwarded message detected, extracted content: {message_content[:100]}"
                    )
            except Exception as e:
                logger.warning(f"⚠️  Failed to extract forwarded message content: {e}")

        # If still no content, check embeds (forwarded messages might use embeds)
        if not message_content.strip() and message.embeds:
            for embed in message.embeds:
                if embed.description:
                    message_content = embed.description
                    logger.info(f"📎 Extracted content from embed: {message_content[:100]}")
                    break

        # Cache this message for context aggregation (use extracted content)
        self._cache_message(message.author.id, message_content)

        # Periodically clean up old messages (every ~20th message)
        import random
        if random.randint(1, 20) == 1:
            self._cleanup_old_messages()

        # Detect researcher (check both username and content - use extracted content)
        researcher_name = self._detect_researcher(
            message.author.name, message_content
        )

        # Only process if from known researcher
        if not researcher_name:
            return

        logger.info(
            f"📨 Message from {researcher_name} in #{message.channel.name}: {message_content[:100]}"
        )

        # Get aggregated context (current message + recent messages from same user)
        aggregated_content = self._get_recent_context(
            message.author.id, message_content
        )

        # Log if we're using context from multiple messages
        if aggregated_content != message_content:
            logger.info(
                f"🔗 Using aggregated context from multiple messages "
                f"(length: {len(aggregated_content)} chars)"
            )

        # Extract projects using AI (from aggregated content)
        projects = await self._extract_projects_with_ai(aggregated_content)

        if not projects:
            logger.debug(f"No projects found in message(s) from {researcher_name}")
            return

        # Store each mention
        for project_info in projects:
            project_name = project_info.get("name")
            symbol = project_info.get("symbol")

            if project_name:
                await self._store_mention(
                    project_name=project_name,
                    researcher_name=researcher_name,
                    content=aggregated_content,  # Store full context
                    symbol=symbol,
                )


async def setup(bot: commands.Bot):
    """Setup function called by bot when loading this cog"""
    await bot.add_cog(MessageMonitor(bot))
    logger.info("MessageMonitor cog loaded")
