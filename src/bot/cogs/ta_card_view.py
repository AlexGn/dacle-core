"""TA Card Interactive View — Session 440.

Interactive buttons for the /ta slash command card.
Follows the TradeApprovalView pattern from analysis_views.py.
"""

import json
import os
from pathlib import Path
from typing import Any, Optional

import aiohttp
import discord

from src.utils.logger import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TOKENS_DIR = PROJECT_ROOT / "data" / "tokens"
TRADES_CHANNEL_ID = 1468948950412431598


def _get_api_base_url() -> str:
    """Resolve API base URL at call time."""
    return os.getenv("DACLE_API_URL", "http://localhost:8000")


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


class TACardView(discord.ui.View):
    """Interactive buttons for TA card embeds."""

    def __init__(
        self,
        symbol: str,
        direction: str,
        data: Optional[dict[str, Any]] = None,
    ):
        super().__init__(timeout=86400)  # 24h timeout
        self.symbol = symbol
        self.direction = direction
        self.data = data or {}

    @discord.ui.button(label="Approve Trade", style=discord.ButtonStyle.green)
    async def approve_trade(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Post trade setup to #trades channel."""
        exec_state = _load_execution_state(self.symbol, self.direction)
        if not exec_state:
            await interaction.response.send_message(
                f"No playbook found for **{self.symbol}** {self.direction}. "
                f"Run `/setup {self.symbol} {self.direction}` to post a setup to #trades.",
                ephemeral=False,
            )
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)
            return

        setup_msg = _format_setup_message(self.symbol, self.direction, exec_state)

        trades_channel = interaction.client.get_channel(TRADES_CHANNEL_ID)
        if not trades_channel:
            await interaction.response.send_message(
                f"Trade approved but could not find #trades channel. "
                f"Post manually:\n```\n{setup_msg}\n```",
                ephemeral=False,
            )
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)
            return

        try:
            await trades_channel.send(setup_msg)
            await interaction.response.send_message(
                f"Setup posted to #trades for **{self.symbol}** {self.direction}. "
                f"Trade router will run pre-trade-check automatically.",
                ephemeral=False,
            )
        except Exception as e:
            logger.error(f"Failed to post setup to #trades: {e}")
            await interaction.response.send_message(
                f"Trade approved but failed to post to #trades: {e}\n"
                f"Post manually:\n```\n{setup_msg}\n```",
                ephemeral=False,
            )

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.grey)
    async def refresh(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Refresh the TA card with fresh data."""
        await interaction.response.defer()

        api_base = _get_api_base_url()
        params = {"direction": self.direction}

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{api_base}/api/ta/card/{self.symbol}"
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status != 200:
                        error_data = await resp.json()
                        error_msg = error_data.get("detail", f"API error {resp.status}")
                        await interaction.followup.send(
                            f"Refresh failed for **{self.symbol}**: {error_msg}",
                            ephemeral=True,
                        )
                        return
                    new_data = await resp.json()
        except Exception as e:
            logger.error(f"TA card refresh failed: {e}")
            await interaction.followup.send(
                f"Refresh failed: {e}", ephemeral=True
            )
            return

        self.data = new_data
        self.direction = new_data.get("direction", self.direction)

        from src.bot.formatters.ta_card import build_ta_card_embed

        embed_data = build_ta_card_embed(new_data)
        embed = discord.Embed(
            title=embed_data["title"],
            description=embed_data.get("description", ""),
            color=embed_data.get("color", 0x9B9B9B),
        )
        for field in embed_data.get("fields", []):
            embed.add_field(
                name=field["name"],
                value=field["value"],
                inline=field.get("inline", False),
            )
        footer = embed_data.get("footer", {})
        if footer:
            embed.set_footer(text=footer.get("text", ""))

        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="Flip Direction", style=discord.ButtonStyle.blurple)
    async def flip_direction(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Flip SHORT<->LONG and refresh the card."""
        await interaction.response.defer()

        new_direction = "LONG" if self.direction == "SHORT" else "SHORT"
        api_base = _get_api_base_url()
        params = {"direction": new_direction}

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{api_base}/api/ta/card/{self.symbol}"
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status != 200:
                        error_data = await resp.json()
                        error_msg = error_data.get("detail", f"API error {resp.status}")
                        await interaction.followup.send(
                            f"Flip failed for **{self.symbol}** {new_direction}: {error_msg}",
                            ephemeral=True,
                        )
                        return
                    new_data = await resp.json()
        except Exception as e:
            logger.error(f"TA card flip failed: {e}")
            await interaction.followup.send(
                f"Flip failed: {e}", ephemeral=True
            )
            return

        self.data = new_data
        self.direction = new_data.get("direction", new_direction)
        self.symbol = new_data.get("symbol", self.symbol)

        from src.bot.formatters.ta_card import build_ta_card_embed

        embed_data = build_ta_card_embed(new_data)
        embed = discord.Embed(
            title=embed_data["title"],
            description=embed_data.get("description", ""),
            color=embed_data.get("color", 0x9B9B9B),
        )
        for field in embed_data.get("fields", []):
            embed.add_field(
                name=field["name"],
                value=field["value"],
                inline=field.get("inline", False),
            )
        footer = embed_data.get("footer", {})
        if footer:
            embed.set_footer(text=footer.get("text", ""))

        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="Details", style=discord.ButtonStyle.grey)
    async def show_details(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Show full reasoning and indicator details."""
        reasoning = self.data.get("reasoning") or []
        if not reasoning:
            await interaction.response.send_message(
                "No detailed reasoning available.", ephemeral=True
            )
            return

        lines = [f"**{self.symbol} {self.direction} — Full Reasoning:**\n"]
        for i, r in enumerate(reasoning, 1):
            lines.append(f"{i}. {r}")

        # OI analysis
        oi = self.data.get("oi_analysis")
        if oi and oi.get("quadrant") != "NEUTRAL":
            lines.append(f"\n**OI Analysis:** {oi.get('quadrant')} "
                         f"(4h: {oi.get('oi_change_4h_pct', 0):.1f}%, "
                         f"24h: {oi.get('oi_change_24h_pct', 0):.1f}%)")

        # Unlock risk
        unlock = self.data.get("unlock_risk")
        if unlock and unlock.get("risk_level") not in ("NO_DATA", "NONE", None):
            lines.append(f"**Unlock Risk:** {unlock.get('label', 'N/A')}")

        text = "\n".join(lines)
        # Discord message limit is 2000 chars
        if len(text) > 2000:
            text = text[:1997] + "..."

        await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(label="Full Analysis", style=discord.ButtonStyle.grey)
    async def full_analysis(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Suggest running full analysis."""
        await interaction.response.send_message(
            f"For full analysis with playbook generation, run:\n"
            f"`/analyze {self.symbol}`",
            ephemeral=True,
        )
