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

from src.ai.together_client import get_together_client
from src.knowledge.knowledge_base import KnowledgeBase
from src.knowledge.supabase_client import get_knowledge_base
from src.scoring.mention_conviction_scorer import MentionConvictionScorer
from src.utils.logger import get_logger

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

        # Initialize conviction scorer with knowledge base
        knowledge_base = KnowledgeBase(self.together, self.kb)
        self.conviction_scorer = MentionConvictionScorer(
            together_client=self.together,
            knowledge_base=knowledge_base,
            researcher_tiers={"Austin": 1, "Phobia": 1, "Sebastien": 1},
        )

        # Message cache: {user_id: deque([(timestamp, content), ...])}
        self.message_cache: Dict[int, deque] = {}

        logger.info("MessageMonitor cog initialized with conviction scoring")

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

            response = self.together.chat_completion(messages, temperature=0.3, max_tokens=300)

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
        Store project mention in Supabase with conviction scoring

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

            # Calculate conviction score using AI + knowledge base
            logger.info(f"🤖 Calculating conviction score for {project_name}...")
            conviction_score = self.conviction_scorer.score_mention(
                message_text=content,
                researcher_name=researcher_name,
                project_name=project_name,
                project_symbol=symbol,
            )

            logger.info(
                f"📊 Conviction: {conviction_score.final_score}/10 "
                f"(confidence: {conviction_score.confidence}, "
                f"position: {conviction_score.position_size}%)"
            )

            # Prepare conviction data for database
            conviction_data = {
                "conviction_score": float(conviction_score.final_score),
                "conviction_confidence": float(conviction_score.confidence),
                "position_size": float(conviction_score.position_size),
                "conviction_signals": conviction_score.conviction_signals,
                "red_flags": conviction_score.red_flags,
                "green_flags": conviction_score.green_flags,
                "knowledge_citations": conviction_score.knowledge_citations,
                "scoring_reasoning": conviction_score.reasoning,
            }

            # Create mention record with conviction scoring
            mention = self.kb.create_project_mention(
                project_id=project["id"],
                researcher_id=researcher["id"],
                mentioned_at=datetime.now(),
                context=content[:500],  # Limit context length
                source="discord",
                data=conviction_data,
            )

            # Generate and store embedding for semantic search
            embedding = self.together.generate_embedding(f"{project_name}: {content[:300]}")

            # Update project with embedding
            self.kb.update_project(project["id"], {"embedding": embedding})

            logger.info(
                f"✅ Stored mention: {project_name} by {researcher_name} "
                f"(mention ID: {mention['id']}, score: {conviction_score.final_score}/10)"
            )

            # Log conviction details for visibility
            if conviction_score.conviction_signals:
                logger.info(f"  Signals: {', '.join(conviction_score.conviction_signals[:2])}")
            if conviction_score.green_flags:
                logger.info(f"  ✅ Green: {', '.join(conviction_score.green_flags[:2])}")
            if conviction_score.red_flags:
                logger.warning(f"  🚩 Red: {', '.join(conviction_score.red_flags[:2])}")

        except Exception as e:
            logger.error(f"Error storing mention for {project_name} by {researcher_name}: {e}")
            logger.exception("Full traceback:")

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

        # Log ALL messages for debugging
        logger.info(
            f"📨 Message received from {message.author.name} in "
            f"{'#' + message.channel.name if message.guild else 'DM'}: {message.content[:100]}"
        )

        # Only process messages from configured private server
        if not message.guild or message.guild.id != self.bot.private_server_id:
            logger.warning(
                f"⚠️  Message NOT from private server (guild_id: {message.guild.id if message.guild else 'None'})"
            )
            # Still process commands even if not from private server
            await self.bot.process_commands(message)
            return

        logger.info(f"✅ Message is from private server, processing for mentions")

        # Extract actual message content (handle forwarded messages)
        message_content = message.content

        # Skip forwarded messages - researchers will use copy-paste workflow
        if not message_content.strip() and message.flags.value & 16384:
            logger.debug(
                f"Ignoring forwarded message from {message.author.name} (no content accessible)"
            )
            await self.bot.process_commands(message)
            return

        # Try to get referenced message content (for replies, not forwards)
        # Note: Forwards from other servers will fail with 404 - this is expected
        if message.reference and not message_content.strip() and not (message.flags.value & 16384):
            try:
                # Only try for non-forwarded messages (replies)
                if message.reference.resolved:
                    referenced_msg = message.reference.resolved
                    if referenced_msg and referenced_msg.content:
                        message_content = referenced_msg.content
                        logger.info(
                            f"📨 Extracted content from reply reference: {message_content[:100]}"
                        )
            except Exception as e:
                logger.debug(f"Could not extract referenced message content: {e}")

        # If still no content, check embeds (forwarded messages might use embeds)
        if not message_content.strip() and message.embeds:
            for embed in message.embeds:
                if embed.description:
                    message_content = embed.description
                    logger.info(f"📎 Extracted content from embed: {message_content[:100]}")
                    break

        # Note: Forwarded messages (flag 16384) are ignored - researchers use copy-paste workflow

        # Cache this message for context aggregation (use extracted content)
        self._cache_message(message.author.id, message_content)

        # Periodically clean up old messages (every ~20th message)
        import random

        if random.randint(1, 20) == 1:
            self._cleanup_old_messages()

        # Detect researcher (check both username and content - use extracted content)
        researcher_name = self._detect_researcher(message.author.name, message_content)

        # Only process if from known researcher
        if not researcher_name:
            await self.bot.process_commands(message)
            return

        logger.info(
            f"📨 Message from {researcher_name} in #{message.channel.name}: {message_content[:100]}"
        )

        # Get aggregated context (current message + recent messages from same user)
        aggregated_content = self._get_recent_context(message.author.id, message_content)

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
            await self.bot.process_commands(message)
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

        # Process commands after handling message
        await self.bot.process_commands(message)


async def setup(bot: commands.Bot):
    """Setup function called by bot when loading this cog"""
    await bot.add_cog(MessageMonitor(bot))
    logger.info("MessageMonitor cog loaded")
