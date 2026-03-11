"""
Brief Action View — Interactive buttons for daily trading brief.

Provides per-token [Analyze] and [Setup] buttons for READY_TO_TRADE tokens.
Max 5 tokens (Discord 25-component / 5-row limit: 2 buttons per row = 10 max).
"""

import discord

from src.utils.logger import get_logger
from src.bot.runtime_routing import get_channel_id

logger = get_logger(__name__)

TRADES_CHANNEL_ID = get_channel_id("trades")
MAX_TOKENS = 5


class BriefActionView(discord.ui.View):
    """Interactive buttons for actionable tokens in the daily brief."""

    def __init__(self, tokens: list):
        super().__init__(timeout=86400)  # 24h timeout
        self._tokens = tokens[:MAX_TOKENS]
        self._build_buttons()

    def _build_buttons(self):
        """Dynamically add Analyze + Setup buttons per token."""
        for token in self._tokens:
            symbol = token["symbol"]
            direction = token["direction"]
            score = token.get("score", 0)

            analyze_btn = AnalyzeButton(symbol=symbol, score=score)
            setup_btn = SetupButton(symbol=symbol, direction=direction, score=score)

            self.add_item(analyze_btn)
            self.add_item(setup_btn)


class AnalyzeButton(discord.ui.Button):
    """Triggers /analyze logic for a token."""

    def __init__(self, symbol: str, score: float):
        super().__init__(
            label=f"Analyze {symbol}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"brief_analyze_{symbol}",
        )
        self._symbol = symbol
        self._score = score

    async def callback(self, interaction: discord.Interaction):
        try:
            cog = interaction.client.get_cog("AnalysisCommands")
            if cog:
                analysis_channel = cog._resolve_analysis_channel()
                if analysis_channel:
                    status_msg = await analysis_channel.send(
                        f"🔍 Analyzing **{self._symbol}**... (from daily brief)"
                    )
                    from src.bot.utils.safe_task import safe_create_task
                    safe_create_task(
                        cog._run_analysis_task(
                            interaction.user, status_msg, self._symbol, analysis_channel,
                        ),
                        logger=logger,
                        name=f"brief-analyze-{self._symbol}",
                    )
                    await interaction.response.send_message(
                        f"🔍 Analysis started for **{self._symbol}** in #analysis-updates.",
                        ephemeral=True,
                    )
                    return
            await interaction.response.send_message(
                f"Use `/analyze {self._symbol}` to start analysis.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(f"Brief analyze button error for {self._symbol}: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        f"Failed to analyze {self._symbol}: {e}", ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        f"Failed to analyze {self._symbol}: {e}", ephemeral=True,
                    )
            except Exception:
                pass


class SetupButton(discord.ui.Button):
    """Posts setup to #trades channel for a token."""

    def __init__(self, symbol: str, direction: str, score: float):
        super().__init__(
            label=f"Setup {symbol} {direction}",
            style=discord.ButtonStyle.green,
            custom_id=f"brief_setup_{symbol}_{direction}",
        )
        self._symbol = symbol
        self._direction = direction
        self._score = score

    async def callback(self, interaction: discord.Interaction):
        try:
            from src.bot.cogs.analysis_views import _load_execution_state, _format_setup_message
            exec_state = _load_execution_state(self._symbol, self._direction)
            if not exec_state:
                await interaction.response.send_message(
                    f"No playbook found for **{self._symbol}** {self._direction}. "
                    f"Run `/analyze {self._symbol}` first.",
                    ephemeral=True,
                )
                return

            setup_msg = _format_setup_message(self._symbol, self._direction, exec_state)
            trades_channel = interaction.client.get_channel(TRADES_CHANNEL_ID)
            if trades_channel:
                await trades_channel.send(setup_msg)
                await interaction.response.send_message(
                    f"✅ Setup posted to #trades for **{self._symbol}** {self._direction}.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"Could not find #trades. Manual setup:\n```\n{setup_msg}\n```",
                    ephemeral=True,
                )
        except Exception as e:
            logger.error(
                f"Brief setup button error for {self._symbol} {self._direction}: {e}"
            )
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        f"Failed to post setup for {self._symbol}: {e}", ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        f"Failed to post setup for {self._symbol}: {e}", ephemeral=True,
                    )
            except Exception:
                pass
