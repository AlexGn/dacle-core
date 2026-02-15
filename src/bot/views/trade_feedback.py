"""
Trade Close Feedback View — Interactive buttons for post-trade feedback capture.

When a position is closed (detected by blofin_trade_sync), this view is posted
to Discord allowing David to quickly rate:
  - Entry quality: Good / Late / Early
  - Stop-loss quality: Good / Tight / Wide

Feedback is appended to data/ml/feedback_patterns.json under the
"trade_close_feedback" key for downstream learning loop consumption.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import discord

from src.utils.logger import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FEEDBACK_PATTERNS_PATH = PROJECT_ROOT / "data" / "ml" / "feedback_patterns.json"

ENTRY_QUALITIES = ["GOOD", "LATE", "EARLY"]
SL_QUALITIES = ["GOOD", "TIGHT", "WIDE"]

ENTRY_BUTTON_STYLES = {
    "Good Entry": discord.ButtonStyle.green,
    "Late Entry": discord.ButtonStyle.secondary,
    "Early Entry": discord.ButtonStyle.secondary,
}

SL_BUTTON_STYLES = {
    "Good SL": discord.ButtonStyle.green,
    "Tight SL": discord.ButtonStyle.secondary,
    "Wide SL": discord.ButtonStyle.secondary,
}


def build_feedback_record(
    trade: Dict[str, Any],
    entry_quality: str,
    sl_quality: str,
) -> Dict[str, Any]:
    """Build a structured feedback record from trade info and user selections."""
    return {
        "token": trade.get("token"),
        "trade_id": trade.get("trade_id"),
        "result": trade.get("result"),
        "pnl_percent": trade.get("pnl_percent"),
        "entry_quality": entry_quality,
        "sl_quality": sl_quality,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def append_feedback_to_patterns(
    record: Dict[str, Any],
    patterns_path: Optional[Path] = None,
) -> None:
    """Append a feedback record to feedback_patterns.json."""
    path = patterns_path or FEEDBACK_PATTERNS_PATH
    try:
        if path.exists():
            data = json.loads(path.read_text())
        else:
            data = {}

        if "trade_close_feedback" not in data:
            data["trade_close_feedback"] = []

        data["trade_close_feedback"].append(record)
        data["last_updated"] = datetime.now(timezone.utc).isoformat()

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        tmp_path.replace(path)

    except Exception:
        logger.error("Failed to append feedback to patterns", exc_info=True)


def format_close_notification(trade: Dict[str, Any]) -> str:
    """Format a human-readable position close notification."""
    token = trade.get("token", "???")
    trade_type = trade.get("trade_type", "???")
    result = trade.get("result", "???")
    pnl_pct = trade.get("pnl_percent", 0)
    pnl_usd = trade.get("pnl_usd", 0)

    sign = "+" if pnl_pct >= 0 else ""
    return (
        f"Position Closed: {token} {trade_type}\n"
        f"Result: {result} ({sign}{pnl_pct:.1f}% / ${pnl_usd:+.2f})\n"
        f"Rate this trade below:"
    )


class TradeCloseFeedbackView(discord.ui.View):
    """Interactive buttons for rating a closed trade's entry and SL quality."""

    def __init__(self, trade: Dict[str, Any]):
        super().__init__(timeout=86400)  # 24h timeout
        self.trade_info = trade
        self._selected_entry: Optional[str] = None
        self._selected_sl: Optional[str] = None
        self._build_buttons()

    def _build_buttons(self):
        """Add entry quality, SL quality, and skip buttons."""
        for label, style in ENTRY_BUTTON_STYLES.items():
            btn = _EntryButton(label=label, style=style, view_ref=self)
            self.add_item(btn)

        for label, style in SL_BUTTON_STYLES.items():
            btn = _SLButton(label=label, style=style, view_ref=self)
            self.add_item(btn)

        skip = discord.ui.Button(label="Skip", style=discord.ButtonStyle.red, row=2)
        skip.callback = self._skip_callback
        self.add_item(skip)

    async def _skip_callback(self, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"Feedback skipped for **{self.trade_info.get('token', '?')}**.",
            view=self,
        )
        self.stop()

    async def _try_submit(self, interaction: discord.Interaction):
        """Submit feedback once both entry and SL are selected."""
        if not self._selected_entry or not self._selected_sl:
            return  # Wait for both selections

        record = build_feedback_record(
            self.trade_info,
            entry_quality=self._selected_entry,
            sl_quality=self._selected_sl,
        )
        append_feedback_to_patterns(record)

        for child in self.children:
            child.disabled = True

        token = self.trade_info.get("token", "?")
        await interaction.response.edit_message(
            content=(
                f"Feedback recorded for **{token}**: "
                f"Entry={self._selected_entry}, SL={self._selected_sl}"
            ),
            view=self,
        )
        self.stop()


class _EntryButton(discord.ui.Button):
    """Button for entry quality selection."""

    def __init__(self, label: str, style: discord.ButtonStyle, view_ref: TradeCloseFeedbackView):
        super().__init__(label=label, style=style, row=0)
        self._view_ref = view_ref

    async def callback(self, interaction: discord.Interaction):
        quality = self.label.replace(" Entry", "").upper()
        self._view_ref._selected_entry = quality
        await self._view_ref._try_submit(interaction)
        if not interaction.response.is_done():
            await interaction.response.defer()


class _SLButton(discord.ui.Button):
    """Button for stop-loss quality selection."""

    def __init__(self, label: str, style: discord.ButtonStyle, view_ref: TradeCloseFeedbackView):
        super().__init__(label=label, style=style, row=1)
        self._view_ref = view_ref

    async def callback(self, interaction: discord.Interaction):
        quality = self.label.replace(" SL", "").upper()
        self._view_ref._selected_sl = quality
        await self._view_ref._try_submit(interaction)
        if not interaction.response.is_done():
            await interaction.response.defer()
