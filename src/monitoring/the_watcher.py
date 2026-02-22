import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

import httpx
from src.utils.logger import get_logger
from src.utils.config import get_discord_config

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
API_BASE_URL = os.getenv("DACLE_API_URL", "http://localhost:8000")
API_KEY = os.getenv("DACLE_API_KEY", "").strip()
API_HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}
TRADES_CHANNEL_ID = 1468948950412431598 # #trades

class TheWatcher:
    """
    The Watcher (Tier 7.1) — Active Capital Protection.
    Persistent trade sentry that monitors macro divergences for open positions.
    """

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.client = httpx.AsyncClient(timeout=30.0, headers=API_HEADERS)
        self.state_file = PROJECT_ROOT / "data" / "state" / "watcher_state.json"
        self._load_state()

    def _load_state(self):
        if self.state_file.exists():
            try:
                self.state = json.loads(self.state_file.read_text())
            except: self.state = {"active_alerts": {}}
        else:
            self.state = {"active_alerts": {}}

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2))

    async def watch_cycle(self):
        """Perform one monitoring cycle for all open positions."""
        logger.info("WATCHER_CYCLE: Starting capital protection sweep...")
        
        # 1. Fetch Positions
        positions = await self._fetch_all_positions()
        if not positions:
            logger.info("WATCHER_CYCLE: No active positions found.")
            return

        # 2. Fetch Live Macro Context (The Sentinel Bridge)
        macro = await self._fetch_macro_context()
        
        # 3. Analyze Each Position
        for pos in positions:
            await self._analyze_position(pos, macro)

        self._save_state()

    async def _fetch_all_positions(self) -> List[Dict]:
        """Consolidate positions from Blofin and MEXC."""
        all_pos = []
        try:
            # Blofin
            r = await self.client.get(f"{API_BASE_URL}/api/blofin/positions")
            if r.status_code == 200:
                all_pos.extend(r.json().get("positions", []))
        except Exception as e:
            logger.error(f"WATCHER_FETCH: Failed to get Blofin positions: {e}")
            
        return all_pos

    async def _fetch_macro_context(self) -> Dict:
        """Get live DXY, NDX, and Market Direction."""
        context = {}
        try:
            r = await self.client.get(f"{API_BASE_URL}/api/macro/market-direction")
            if r.status_code == 200:
                context['market'] = r.json()
                
            r_trend = await self.client.get(f"{API_BASE_URL}/api/macro/market-direction/trend")
            if r_trend.status_code == 200:
                context['trend'] = r_trend.json()
        except Exception as e:
            logger.error(f"WATCHER_FETCH: Macro context failed: {e}")
        return context

    async def _analyze_position(self, pos: Dict, macro: Dict):
        """Identify invalidating macro conditions for a specific trade."""
        symbol = pos.get("token") or pos.get("symbol")
        side = pos.get("side", "UNKNOWN").upper()
        pnl_pct = float(pos.get("unrealized_pnl_pct", 0))
        
        # Specialist Rules (Hardcoded for timing precision)
        
        # Rule 1: DXY Divergence (The Trap)
        # If LONG but DXY is pumping, alert.
        market = macro.get("market", {})
        signals = market.get("signals", [])
        ext_macro = next((s for s in signals if s.get("name") == "External Macro"), {})
        
        is_dxy_uptrend = "DXY UPTREND" in ext_macro.get("label", "")
        
        alert_msg = None
        severity = "INFO"
        
        if side == "LONG" and is_dxy_uptrend:
            alert_msg = f"🛡️ **WATCHER ALERT**: ${symbol} LONG is threatened by **DXY UPTREND**. This is a Macro Trap."
            severity = "WARNING"
        
        # Rule 2: Regime Misalignment
        bias = market.get("bias", "UNKNOWN")
        if (side == "LONG" and bias == "BEARISH") or (side == "SHORT" and bias == "BULLISH"):
            alert_msg = f"🛡️ **WATCHER ALERT**: ${symbol} {side} is now **MISALIGNED** with confirmed {bias} regime."
            severity = "CRITICAL"

        # 3. Suppress duplicate alerts
        if alert_msg and self._should_alert(symbol, alert_msg):
            await self._post_alert(alert_msg, severity)

    def _should_alert(self, symbol: str, msg: str) -> bool:
        """Cooldown: only alert once every 4 hours per symbol/message."""
        now = datetime.now(timezone.utc).timestamp()
        key = f"{symbol}:{hash(msg)}"
        last_alert = self.state["active_alerts"].get(key, 0)
        
        if now - last_alert > (4 * 3600):
            self.state["active_alerts"][key] = now
            return True
        return False

    async def _post_alert(self, msg: str, severity: str):
        """Send the alert to Discord #trades."""
        if self.dry_run:
            logger.info(f"WATCHER_POST [DRY]: {msg}")
            return

        token = os.environ.get("DISCORD_BOT_TOKEN")
        if not token: return

        url = f"https://discord.com/api/v10/channels/{TRADES_CHANNEL_ID}/messages"
        headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
        payload = {"content": msg}
        
        try:
            await self.client.post(url, headers=headers, json=payload)
            logger.info(f"WATCHER_POST: Alert delivered to #trades")
        except Exception as e:
            logger.error(f"WATCHER_POST: Failed: {e}")

if __name__ == "__main__":
    # Manual test
    watcher = TheWatcher(dry_run=False)
    asyncio.run(watcher.watch_cycle())
