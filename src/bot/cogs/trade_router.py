from src.utils.logger import get_logger
import re
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import os
from typing import Optional, Dict, Any

from src.utils.redis_lms import get_current_price

logger = get_logger(__name__)

# Patterns ported from deploy/openclaw/trade-router/index.js
SYMBOL_PATTERN = re.compile(r"\$([A-Z0-9]{2,10})|([A-Z0-9]{2,10})(?=\/(?:USDT|USD|USDC))", re.IGNORECASE)
DIRECTION_PATTERN = re.compile(r"\b(Short|Long|SHORT|LONG)\b", re.IGNORECASE)
ENTRY_PATTERN = re.compile(r"entry(?:\s*point)?\s*:\s*(?:entry\s*:)?\s*(?:limit\s*)?\$?([0-9]*\.?[0-9]+)", re.IGNORECASE | re.MULTILINE)
SL_PATTERN = re.compile(r"(?:stop\s*loss|sl)\s*:\s*\$?([0-9]*\.?[0-9]+)", re.IGNORECASE | re.MULTILINE)
TARGET_PATTERN = re.compile(r"(?:final\s*target|target|tp1|tp)\s*:\s*\$?([0-9]*\.?[0-9]+)", re.IGNORECASE | re.MULTILINE)

# Score card patterns
SCORE_CARD_HEADER_PATTERN = re.compile(r"^\s*\$?([A-Z0-9]{2,10})\s+(SHORT|LONG)\b", re.IGNORECASE | re.MULTILINE)
ENTRY_SCORE_PATTERN = re.compile(r"entry(?:\s*score)?\s*:?\s*([0-9]+(?:\.[0-9]+)?)\s*\/\s*10", re.IGNORECASE | re.MULTILINE)
RR_SCORE_PATTERN = re.compile(r"r:r\s*([0-9]+(?:\.[0-9]+)?)\s*:\s*1", re.IGNORECASE | re.MULTILINE)

# Default trades channel from Node.js code
TRADES_CHANNEL_ID = 1468948950412431598

class TradeRouter(commands.Cog):
    """
    Deterministic Trade Router (Ported from Node.js)
    Listens for trade setups in #trades and runs pre-trade diagnostics.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_url = os.getenv("DACLE_API_URL", "http://localhost:8000")
        logger.info("TradeRouter cog initialized")

    def parse_setup(self, content: str) -> Optional[Dict[str, Any]]:
        symbol_match = SYMBOL_PATTERN.search(content)
        dir_match = DIRECTION_PATTERN.search(content)
        entry_match = ENTRY_PATTERN.search(content)
        sl_match = SL_PATTERN.search(content)
        target_match = TARGET_PATTERN.search(content)
        
        if not all([symbol_match, dir_match, entry_match, sl_match]):
            return None
            
        symbol = (symbol_match.group(1) or symbol_match.group(2)).upper()
        direction = dir_match.group(1).upper()
        
        try:
            entry = float(entry_match.group(1))
            sl = float(sl_match.group(1))
            target = float(target_match.group(1)) if target_match else None
            
            return {
                "token": symbol,
                "direction": direction,
                "entry": entry,
                "sl": sl,
                "target": target
            }
        except ValueError:
            return None

    def parse_score_card(self, content: str) -> Optional[Dict[str, Any]]:
        header_match = SCORE_CARD_HEADER_PATTERN.search(content)
        score_match = ENTRY_SCORE_PATTERN.search(content)
        
        if not header_match or not score_match:
            return None
            
        symbol = header_match.group(1).upper()
        direction = header_match.group(2).upper()
        
        try:
            score = float(score_match.group(1))
            rr_match = RR_SCORE_PATTERN.search(content)
            rr = float(rr_match.group(1)) if rr_match else None
            
            return {
                "token": symbol,
                "direction": direction,
                "entry_score": score,
                "rr_ratio": rr
            }
        except ValueError:
            return None

    async def call_pre_trade_check(self, setup: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self.api_url}/api/execution/pre-trade-check"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=setup) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"API error: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Failed to call API: {e}")
            return None

    def format_score_decision(self, card: Dict[str, Any]) -> str:
        threshold = 8.0 if card["direction"] == "SHORT" else 8.5
        approved = card["entry_score"] >= threshold
        status_emoji = "✅" if approved else "❌"
        status_word = "EXECUTE" if approved else "SKIP"
        
        rr_line = f"• R:R: {card['rr_ratio']:.1f}:1" if card["rr_ratio"] else "• R:R: N/A"
        if card["rr_ratio"]:
             rr_line += " ✅" if card["rr_ratio"] >= 2.0 else " ⚠️ (below 2.0)"

        return (
            f"{status_emoji} {card['token']} {card['direction']} — {status_word}\n"
            f"**David should trade now:** {'YES' if approved else 'NO'}\n\n"
            f"📊 **Score-Only Evaluation:**\n"
            f"• Entry Score: {card['entry_score']:.1f}/10\n"
            f"{rr_line}\n"
            f"• Threshold ({card['direction']}): {threshold:.1f}/10\n\n"
            f"**Decision:** {status_emoji} {status_word}\n"
            f"{'Meets score threshold. Post full Entry/SL/Target to run full risk diagnostics.' if approved else 'Below threshold. Post full Entry/SL/Target only if you want full risk diagnostics.'}"
        )

    @app_commands.command(name="rerun", description="Re-run trade analysis with current market data")
    async def rerun(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.followup.send(
                "❌ `/rerun` must be used inside a trade thread.", ephemeral=True
            )
            return
        if channel.parent_id != TRADES_CHANNEL_ID:
            await interaction.followup.send(
                "❌ `/rerun` only works in `#trades` threads.", ephemeral=True
            )
            return

        setup = await self._extract_setup_from_thread(channel)
        if not setup:
            await interaction.followup.send(
                "❌ Could not find a trade setup in this thread.\n"
                "Expected format: `Entry: X.XX`, `SL: X.XX`, etc."
            )
            return

        proximity_note = await self._check_price_proximity(setup)

        try:
            api_res = await self.call_pre_trade_check(setup)
        except Exception as e:
            logger.error(f"Rerun API call failed: {e}")
            api_res = None

        if api_res and api_res.get("data", {}).get("formatted_response"):
            header = f"🔄 **RERUN** — {setup['token']} {setup['direction']}\n"
            if proximity_note:
                header += f"{proximity_note}\n"
            header += f"_Original setup: Entry ${setup['entry']}, SL ${setup['sl']}"
            if setup.get("target"):
                header += f", Target ${setup['target']}"
            header += "_\n\n"
            response_text = header + api_res["data"]["formatted_response"]
        else:
            response_text = "❌ Trade rerun failed — DACLE API unavailable."

        await interaction.followup.send(response_text)

    async def _extract_setup_from_thread(self, thread: discord.Thread) -> Optional[Dict[str, Any]]:
        """Scan thread messages for the original trade setup."""
        try:
            starter = await thread.fetch_message(thread.id)
            setup = self.parse_setup(starter.content)
            if setup:
                return setup
        except Exception:
            pass

        async for msg in thread.history(limit=50, oldest_first=True):
            if msg.author.bot:
                continue
            setup = self.parse_setup(msg.content)
            if setup:
                return setup

        return None

    async def _check_price_proximity(self, setup: Dict[str, Any]) -> Optional[str]:
        """Check if current price is within 10% of original entry. Returns warning or None."""
        try:
            current = get_current_price(setup["token"])
            if current is None:
                return "⚠️ Could not fetch current price — proximity check skipped."
            pct_diff = abs(current - setup["entry"]) / setup["entry"] * 100
            if pct_diff > 10:
                return f"⚠️ Current price ${current:.4g} is {pct_diff:.1f}% from entry ${setup['entry']} — setup may be stale."
            return None
        except Exception:
            return None

    # NOTE: on_message listener removed — trade setup detection is handled by
    # Node.js trade-router (deploy/openclaw/trade-router/index.js) per Session 408.
    # This cog only provides the /rerun slash command.

async def setup(bot: commands.Bot):
    await bot.add_cog(TradeRouter(bot))
    logger.info("TradeRouter cog loaded")
