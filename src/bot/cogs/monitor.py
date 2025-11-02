"""
Message Monitor Cog
Monitors Discord messages for crypto project mentions and stores them in Supabase
"""

import re
from datetime import datetime
from typing import Dict, List, Optional

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

    def __init__(self, bot: commands.Bot):
        """Initialize the monitor cog"""
        self.bot = bot
        self.together = get_together_client()
        self.kb = get_knowledge_base()
        logger.info("MessageMonitor cog initialized")

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

        # Detect researcher
        researcher_name = self._detect_researcher(
            message.author.name, message.content
        )

        # Only process if from known researcher
        if not researcher_name:
            return

        logger.info(
            f"📨 Message from {researcher_name} in #{message.channel.name}: {message.content[:100]}"
        )

        # Extract projects using AI
        projects = await self._extract_projects_with_ai(message.content)

        if not projects:
            logger.debug(f"No projects found in message from {researcher_name}")
            return

        # Store each mention
        for project_info in projects:
            project_name = project_info.get("name")
            symbol = project_info.get("symbol")

            if project_name:
                await self._store_mention(
                    project_name=project_name,
                    researcher_name=researcher_name,
                    content=message.content,
                    symbol=symbol,
                )


async def setup(bot: commands.Bot):
    """Setup function called by bot when loading this cog"""
    await bot.add_cog(MessageMonitor(bot))
    logger.info("MessageMonitor cog loaded")
