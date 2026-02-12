import logging
import re
import aiohttp
import discord
from discord.ext import commands
import os
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Check channel
        if message.channel.id != TRADES_CHANNEL_ID:
            return

        # Ignore if mentions anyone (ClawdBot handles mentions)
        if message.mentions:
            return

        # Try full setup
        setup = self.parse_setup(message.content)
        if setup:
            logger.info(f"Trade Router detected setup for {setup['token']}")
            api_res = await self.call_pre_trade_check(setup)
            
            if api_res and api_res.get("data", {}).get("formatted_response"):
                response_text = api_res["data"]["formatted_response"]
            else:
                response_text = "❌ Trade check failed - DACLE API unavailable."

            await self.send_in_thread(message, setup, response_text)
            return

        # Try score card
        card = self.parse_score_card(message.content)
        if card:
            logger.info(f"Trade Router detected score card for {card['token']}")
            response_text = self.format_score_decision(card)
            await self.send_in_thread(message, card, response_text)

    async def send_in_thread(self, message: discord.Message, setup: Dict[str, Any], content: str):
        try:
            # Create thread if not in one
            if not isinstance(message.channel, discord.Thread):
                thread_name = f"{setup['token']}-{setup['direction']}"
                thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)
                await thread.send(content)
                await message.reply(f"🧵 Analysis posted in thread: {thread.mention}", mention_author=False)
            else:
                await message.channel.send(content)
        except Exception as e:
            logger.error(f"Failed to send in thread: {e}")
            await message.reply(content, mention_author=False)

async def setup(bot: commands.Bot):
    await bot.add_cog(TradeRouter(bot))
    logger.info("TradeRouter cog loaded")
