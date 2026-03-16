"""
Heartbeat Action Views — Discord interactive buttons for heartbeat alerts

Sprint 1b: Views for discovery (Approve/Dismiss) and position (Close/Reduce/Hold) alerts.
Posted by HeartbeatActionCog when pending action cards are detected.
"""

import discord
from datetime import datetime, timezone
from typing import Optional

from src.utils.logger import get_logger
from src.bot.runtime_routing import get_channel_id
from src.bot.cogs.analysis_views import (
    _load_execution_state,
    _format_setup_message,
)

logger = get_logger(__name__)

FOCUS_CHANNEL_ID = 1470789144736174326


def _get_trades_channel_id() -> int:
    """Resolve the canonical trades channel ID at call time."""
    return get_channel_id("trades")


def render_action_card_embed(card: dict) -> discord.Embed:
    """Convert an ActionCard dict to a Discord Embed.

    Args:
        card: Dict with keys: title, description, color, fields, buttons.
              Optional: urgency.

    Returns:
        discord.Embed ready to post.
    """
    embed = discord.Embed(
        title=card.get("title", "Heartbeat Alert"),
        description=card.get("description", ""),
        color=discord.Color(card.get("color", 0x3498DB)),
        timestamp=datetime.now(timezone.utc),
    )

    for field in card.get("fields", []):
        embed.add_field(
            name=field["name"],
            value=field["value"],
            inline=field.get("inline", False),
        )

    urgency = card.get("urgency")
    if urgency:
        embed.set_footer(text=f"Urgency: {urgency}")

    return embed


def select_view_for_card(card: dict) -> Optional[discord.ui.View]:
    """Select the correct View class based on button patterns in the card.

    Args:
        card: Dict with 'buttons' list and 'meta' dict.

    Returns:
        HeartbeatDiscoveryView, HeartbeatPositionView, or None.
    """
    buttons = card.get("buttons", [])
    if not buttons:
        return None

    custom_ids = [b.get("custom_id", "") for b in buttons]

    # Check for discovery pattern
    if any("discovery_approve" in cid for cid in custom_ids):
        meta = card.get("meta", {})
        token = meta.get("token", "???")
        score = meta.get("score", 0.0)
        # Infer direction from meta or default SHORT
        direction = meta.get("direction", "SHORT")
        return HeartbeatDiscoveryView(symbol=token, direction=direction, score=score)

    # Check for position pattern
    if any("position_close" in cid for cid in custom_ids):
        meta = card.get("meta", {})
        token = meta.get("token", "???")
        pnl_pct = meta.get("pnl_pct", 0.0)
        return HeartbeatPositionView(symbol=token, pnl_pct=pnl_pct)

    return None


class HeartbeatDiscoveryView(discord.ui.View):
    """Interactive buttons for high-conviction discovery alerts.

    Approve: loads playbook, formats setup, posts to #trades.
    Dismiss: logs dismissal, disables buttons.
    """

    def __init__(self, symbol: str, direction: str, score: float):
        super().__init__(timeout=86400)  # 24h
        self.symbol = symbol
        self.direction = direction
        self.score = score

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, emoji="\u2705")
    async def approve_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            exec_state = _load_execution_state(self.symbol, self.direction)
            if not exec_state:
                await interaction.response.send_message(
                    f"\u2705 **Approved**: No playbook found for **{self.symbol}** {self.direction}. "
                    f"Run `/setup {self.symbol} {self.direction}` to post a setup to #trades.",
                    ephemeral=False,
                )
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(view=self)
                return

            setup_msg = _format_setup_message(self.symbol, self.direction, exec_state)

            trades_channel = interaction.client.get_channel(_get_trades_channel_id())
            if not trades_channel:
                await interaction.response.send_message(
                    f"\u2705 **Approved** but could not find #trades channel. "
                    f"Post manually:\n```\n{setup_msg}\n```",
                    ephemeral=False,
                )
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(view=self)
                return

            await trades_channel.send(setup_msg)
            await interaction.response.send_message(
                f"\u2705 **Setup posted to #trades** for **{self.symbol}** {self.direction} "
                f"(score: {self.score}). Trade router will run pre-trade-check automatically.",
                ephemeral=False,
            )
            logger.info(f"Heartbeat discovery approved: {self.symbol} {self.direction} score={self.score}")

        except Exception as e:
            logger.error(f"Heartbeat approve failed for {self.symbol}: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        f"\u274c **Error approving {self.symbol}**: {e}", ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        f"\u274c **Error approving {self.symbol}**: {e}", ephemeral=True
                    )
            except Exception:
                pass

        # Always try to disable buttons
        try:
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.grey, emoji="\u274e")
    async def dismiss_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_message(
                f"\u274e **Dismissed**: {self.symbol} ({self.score}) — skipping this setup.",
                ephemeral=False,
            )
            logger.info(f"Heartbeat discovery dismissed: {self.symbol} score={self.score}")
        except Exception as e:
            logger.error(f"Heartbeat dismiss failed: {e}")

        try:
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(view=self)
        except Exception:
            pass

    async def on_timeout(self):
        """Disable buttons when view expires."""
        for child in self.children:
            child.disabled = True


class HeartbeatPositionView(discord.ui.View):
    """Interactive buttons for critical position health alerts.

    Close: posts close advisory to #focus.
    Reduce: posts reduce advisory to #focus.
    Hold: logs hold decision, disables buttons.
    """

    def __init__(self, symbol: str, pnl_pct: float):
        super().__init__(timeout=86400)  # 24h
        self.symbol = symbol
        self.pnl_pct = pnl_pct

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, emoji="\U0001f6d1")
    async def close_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            focus_channel = interaction.client.get_channel(FOCUS_CHANNEL_ID)
            if focus_channel:
                await focus_channel.send(
                    f"\U0001f6d1 **Close recommended**: {self.symbol} (PnL: {self.pnl_pct:.1f}%) "
                    f"— Decision: CLOSE"
                )

            await interaction.response.send_message(
                f"\U0001f6d1 **Close decision logged** for {self.symbol} ({self.pnl_pct:.1f}%)",
                ephemeral=False,
            )
            logger.info(f"Heartbeat position CLOSE: {self.symbol} pnl={self.pnl_pct:.1f}%")
        except Exception as e:
            logger.error(f"Heartbeat close failed: {e}")
            try:
                await interaction.response.send_message(
                    f"\u274c **Error**: {e}", ephemeral=True
                )
            except Exception:
                pass

        try:
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Reduce", style=discord.ButtonStyle.grey, emoji="\u2935\ufe0f")
    async def reduce_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            focus_channel = interaction.client.get_channel(FOCUS_CHANNEL_ID)
            if focus_channel:
                await focus_channel.send(
                    f"\u2935\ufe0f **Reduce recommended**: {self.symbol} (PnL: {self.pnl_pct:.1f}%) "
                    f"— Decision: REDUCE"
                )

            await interaction.response.send_message(
                f"\u2935\ufe0f **Reduce decision logged** for {self.symbol} ({self.pnl_pct:.1f}%)",
                ephemeral=False,
            )
            logger.info(f"Heartbeat position REDUCE: {self.symbol} pnl={self.pnl_pct:.1f}%")
        except Exception as e:
            logger.error(f"Heartbeat reduce failed: {e}")
            try:
                await interaction.response.send_message(
                    f"\u274c **Error**: {e}", ephemeral=True
                )
            except Exception:
                pass

        try:
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Hold", style=discord.ButtonStyle.blurple, emoji="\u270b")
    async def hold_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_message(
                f"\u270b **Hold decision logged** for {self.symbol} ({self.pnl_pct:.1f}%) "
                f"— Keeping position open.",
                ephemeral=False,
            )
            logger.info(f"Heartbeat position HOLD: {self.symbol} pnl={self.pnl_pct:.1f}%")
        except Exception as e:
            logger.error(f"Heartbeat hold failed: {e}")
            try:
                await interaction.response.send_message(
                    f"\u274c **Error**: {e}", ephemeral=True
                )
            except Exception:
                pass

        try:
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(view=self)
        except Exception:
            pass

    async def on_timeout(self):
        """Disable buttons when view expires."""
        for child in self.children:
            child.disabled = True
