"""
Trade Approval View
Discord UI components (buttons) for approving or vetoing trade candidates.
"""

import json
import os
from pathlib import Path
from typing import Any, Optional

import discord

from src.utils.logger import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TOKENS_DIR = PROJECT_ROOT / "data" / "tokens"
TRADES_CHANNEL_ID = 1468948950412431598


def _load_execution_state(symbol: str, direction: str) -> Optional[dict]:
    """Load playbook execution state for a token."""
    token_dir = TOKENS_DIR / symbol.upper()
    playbooks_dir = token_dir / "playbooks"
    if not playbooks_dir.exists():
        return None

    candidates = [
        playbooks_dir / f"{symbol.upper()}_{direction.lower()}_execution_state.json",
        playbooks_dir / f"{symbol.upper()}_execution_state.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                continue
    return None


def _format_setup_message(symbol: str, direction: str, exec_state: dict) -> str:
    """Format a canonical setup message from execution state."""
    levels = exec_state.get("execution_levels", {})
    entry_low = levels.get("entry_low")
    entry_high = levels.get("entry_high")
    stop_loss = levels.get("stop_loss")
    target = levels.get("target_1")

    entry_str = ""
    if entry_low and entry_high:
        entry_str = f"Entry: {entry_low} - {entry_high}"
    elif entry_low:
        entry_str = f"Entry: {entry_low}"

    parts = [f"TAKE {direction.upper()} ${symbol.upper()}"]
    if entry_str:
        parts.append(entry_str)
    if stop_loss:
        parts.append(f"SL: {stop_loss}")
    if target:
        parts.append(f"Target: {target}")

    return "\n".join(parts)


class TradeApprovalView(discord.ui.View):
    """
    Interactive buttons for #analysis-updates candidates.
    """
    def __init__(self, symbol: str, conviction: float, direction: Optional[str] = None):
        super().__init__(timeout=86400)  # 24h timeout
        self.symbol = symbol
        self.conviction = conviction
        self.direction = direction

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        direction = self.direction or "SHORT"

        # Try to load playbook execution state
        exec_state = _load_execution_state(self.symbol, direction)
        if not exec_state:
            await interaction.response.send_message(
                f"✅ **Trade Approved**: No playbook found for **{self.symbol}** {direction}. "
                f"Run `/setup {self.symbol} {direction}` to post a setup to #trades.",
                ephemeral=False,
            )
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(view=self)
            return

        setup_msg = _format_setup_message(self.symbol, direction, exec_state)

        # Post to #trades channel
        trades_channel = interaction.client.get_channel(TRADES_CHANNEL_ID)
        if not trades_channel:
            await interaction.response.send_message(
                f"✅ **Trade Approved** but could not find #trades channel. "
                f"Post manually:\n```\n{setup_msg}\n```",
                ephemeral=False,
            )
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(view=self)
            return

        try:
            await trades_channel.send(setup_msg)
            await interaction.response.send_message(
                f"✅ **Setup posted to #trades** for **{self.symbol}** {direction}. "
                f"Trade router will run pre-trade-check automatically.",
                ephemeral=False,
            )
        except Exception as e:
            logger.error(f"Failed to post setup to #trades: {e}")
            await interaction.response.send_message(
                f"✅ **Trade Approved** but failed to post to #trades: {e}\n"
                f"Post manually:\n```\n{setup_msg}\n```",
                ephemeral=False,
            )

        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.grey, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"🔄 **Refreshing Analysis** for **{self.symbol}**...",
            ephemeral=True
        )

    @discord.ui.button(label="Veto", style=discord.ButtonStyle.red, emoji="❌")
    async def veto(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"❌ **Trade Vetoed**: {self.symbol} moved to archive.",
            ephemeral=False
        )
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)
