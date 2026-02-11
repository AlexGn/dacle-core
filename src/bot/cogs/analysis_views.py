"""
Trade Approval View
Discord UI components (buttons) for approving or vetoing trade candidates.
"""

import discord
from typing import Any

class TradeApprovalView(discord.ui.View):
    """
    Interactive buttons for #analysis-updates candidates.
    """
    def __init__(self, symbol: str, conviction: float):
        super().__init__(timeout=86400)  # 24h timeout
        self.symbol = symbol
        self.conviction = conviction

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # In a real implementation, this would trigger /trade-entry
        await interaction.response.send_message(
            f"✅ **Trade Approved**: Logging entry for **{self.symbol}**...",
            ephemeral=False
        )
        # Disable buttons after action
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
