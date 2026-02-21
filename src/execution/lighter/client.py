"""
DACLE Lighter Real Client
Production-grade client for Lighter.xyz.
Handles authenticated orders, signing, and REST API interactions.
"""

import logging
import time
from typing import Any, Dict, Optional

import aiohttp

from src.execution.lighter.signer import LighterSigner

logger = logging.getLogger(__name__)


class LighterRealClient:
    def __init__(self, config: dict, signer: Optional[LighterSigner] = None):
        self.api_url = config.get("api_url", "https://mainnet.zklighter.elliot.ai/api/v1")
        self.signer = signer
        self.market_id = config.get("market_id", 1)
        self.account_type = config.get("account_type", "STANDARD")
        self.mode = str(config.get("mode", "SHADOW")).upper()
        self._shadow_order_counter = 0

    def _is_shadow_mode(self) -> bool:
        return self.mode == "SHADOW"

    def _build_shadow_ack(self, symbol: str, side: str, price: float, qty: float, nonce: int) -> dict:
        self._shadow_order_counter += 1
        now_ms = int(time.time() * 1000)
        return {
            "status": "success",
            "shadow": True,
            "order_id": f"shadow_{now_ms}_{self._shadow_order_counter}",
            "client_order_id": f"shadow_cli_{nonce}_{self._shadow_order_counter}",
            "symbol": symbol,
            "side": side.lower(),
            "price": str(price),
            "size": str(qty),
            "nonce": nonce,
            "timestamp": now_ms,
        }

    async def create_order(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        nonce: int,
    ) -> dict:
        """
        Creates an authenticated IOC order on Lighter.xyz.
        In SHADOW mode this function fail-closes by returning a mock ack before any network I/O.
        """
        if self._is_shadow_mode():
            return self._build_shadow_ack(symbol, side, price, qty, nonce)

        if not self.signer:
            return {"status": "error", "error": "No signer provided for LIVE mode."}

        order_data = {
            "marketId": self.market_id,
            "side": 0 if side == "BUY" else 1,
            "price": int(price * 10),  # Assuming price_decimals=1 for BTC perp
            "size": int(qty * 100000),  # Assuming size_decimals=5
            "nonce": nonce,
        }
        signature = self.signer.sign_order(order_data)

        payload = {
            "type": "create_order",
            "marketId": self.market_id,
            "side": side.lower(),
            "price": str(price),
            "size": str(qty),
            "nonce": nonce,
            "signature": signature,
            "timeInForce": "IOC",
        }

        url = f"{self.api_url}/sendTx"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"status": "success", "order_id": data.get("orderId"), "raw": data}
                    err_text = await resp.text()
                    return {"status": "error", "error": f"HTTP {resp.status}: {err_text}"}
        except Exception as e:
            logger.error(f"Execution Error: {e}")
            return {"status": "error", "error": str(e)}

    async def fetch_fills(
        self,
        since_ts: Optional[int] = None,
        cursor: Optional[str] = None,
        limit: int = 100,
    ) -> dict:
        """Fetch recent fills for GhostSweeper reconciliation."""
        if not self.signer:
            return {"status": "error", "error": "No signer available for fills endpoint."}

        url = f"{self.api_url}/account/{self.signer.address}/fills"
        params: Dict[str, Any] = {"marketId": self.market_id, "limit": limit}
        if since_ts is not None:
            params["sinceTs"] = since_ts
        if cursor:
            params["cursor"] = cursor

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        return {"status": "error", "error": f"HTTP {resp.status}: {err_text}"}
                    data = await resp.json()
        except Exception as e:
            logger.error(f"Fills Fetch Error: {e}")
            return {"status": "error", "error": str(e)}

        fills = []
        if isinstance(data, list):
            fills = data
        elif isinstance(data, dict):
            if isinstance(data.get("fills"), list):
                fills = data["fills"]
            elif isinstance(data.get("data"), list):
                fills = data["data"]
            elif isinstance(data.get("results"), list):
                fills = data["results"]

        next_cursor = None
        if isinstance(data, dict):
            next_cursor = data.get("next_cursor") or data.get("nextCursor") or data.get("cursor")

        return {"status": "success", "fills": fills, "next_cursor": next_cursor, "raw": data}

    async def get_balance(self) -> dict:
        """Fetch account balance via REST."""
        if not self.signer:
            return {}
        url = f"{self.api_url}/account/{self.signer.address}/balances"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            pass
        return {}

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> dict:
        """
        Best-effort cancel-all used by poison-pill shutdown.
        """
        if self._is_shadow_mode():
            return {"status": "success", "shadow": True, "cancelled": 0}
        return {"status": "error", "error": "cancel_all_orders endpoint not implemented"}
