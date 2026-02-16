"""
Heartbeat Action Cog — Polls pending ActionCards and posts to Discord

Sprint 1b: Bridge between cron heartbeat (writes cards) and bot (reads + posts).
Pattern: file-based IPC via atomic_update (same as atomic_state.py usage).

Polling loop:
    1. Read data/state/pending_action_cards.json (atomic)
    2. Clear the file (atomic)
    3. Post embeds + views to Discord channels
    4. Skip expired cards (>2h old)
"""

from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks

from src.utils.logger import get_logger
from src.utils.atomic_state import atomic_update
from src.bot.views.heartbeat_actions import render_action_card_embed, select_view_for_card

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class HeartbeatActionCog(commands.Cog):
    """Polls pending heartbeat action cards and posts interactive embeds to Discord."""

    PENDING_CARDS_PATH = PROJECT_ROOT / "data" / "state" / "pending_action_cards.json"
    CARD_TTL_SECONDS = 7200  # 2h expiry for stale cards

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        """Start polling loop when cog loads."""
        self._poll_cards.start()
        logger.info("HeartbeatActionCog started — polling every 30s for action cards")

    async def cog_unload(self):
        """Stop polling loop when cog unloads."""
        self._poll_cards.cancel()
        logger.info("HeartbeatActionCog stopped")

    @tasks.loop(seconds=30)
    async def _poll_cards(self):
        """Read pending action cards and post to Discord."""
        try:
            cards = self._read_and_clear_pending()
            if not cards:
                return

            posted = 0
            expired = 0
            for card in cards:
                if self._is_expired(card):
                    expired += 1
                    continue

                channel_id = card.get("channel_id")
                if not channel_id:
                    logger.warning("Action card missing channel_id, skipping")
                    continue

                channel = self.bot.get_channel(channel_id)
                if not channel:
                    logger.warning(f"Channel {channel_id} not found for action card: {card.get('title')}")
                    continue

                try:
                    embed = render_action_card_embed(card)
                    view = select_view_for_card(card)

                    if view:
                        await channel.send(embed=embed, view=view)
                    else:
                        await channel.send(embed=embed)

                    posted += 1
                except Exception as e:
                    logger.error(f"Failed to post action card '{card.get('title')}': {e}")

            if posted > 0 or expired > 0:
                logger.info(
                    f"HeartbeatActionCog: posted={posted}, expired={expired}, "
                    f"total={len(cards)}"
                )

        except Exception as e:
            logger.error(f"HeartbeatActionCog poll error: {e}")

    @_poll_cards.before_loop
    async def _wait_ready(self):
        """Wait for bot to be ready before polling."""
        await self.bot.wait_until_ready()

    def _read_and_clear_pending(self) -> list:
        """Atomically read pending cards and clear the file.

        Returns:
            List of card dicts that were pending.
        """
        consumed = []

        def extract_and_clear(state):
            nonlocal consumed
            consumed = state.get("cards", [])
            state["cards"] = []
            state["_consumed"] = consumed
            return state

        try:
            atomic_update(
                self.PENDING_CARDS_PATH,
                extract_and_clear,
                default={"cards": []},
            )
        except Exception as e:
            logger.error(f"Failed to read pending action cards: {e}")
            return []

        return consumed

    def _is_expired(self, card: dict) -> bool:
        """Check if a card has exceeded the TTL.

        Args:
            card: Card dict with optional 'created_utc' ISO timestamp.

        Returns:
            True if card is older than CARD_TTL_SECONDS, False otherwise.
            Missing timestamp defaults to not expired (process anyway).
        """
        created_str = card.get("created_utc")
        if not created_str:
            return False

        try:
            created = datetime.fromisoformat(created_str)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            return age > self.CARD_TTL_SECONDS
        except (ValueError, TypeError):
            return False


async def setup(bot: commands.Bot):
    """Setup function called by Discord.py when loading cog."""
    await bot.add_cog(HeartbeatActionCog(bot))
    logger.info("HeartbeatActionCog loaded successfully")
