"""
DACLE Lighter Real Client
Production-grade client for Lighter.xyz.
Handles authenticated orders, signing, and REST API interactions.
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from src.execution.lighter.signer import LighterSigner

logger = logging.getLogger(__name__)

# Valid order types and their Lighter API timeInForce mapping.
_ORDER_TYPE_TO_TIF = {
    "IOC": "IOC",
    "LIMIT": "GTC",
    "POST_ONLY": "POST_ONLY",
}


class LighterRealClient:
    def __init__(self, config: dict, signer: Optional[LighterSigner] = None):
        self.api_url = config.get("api_url", "https://mainnet.zklighter.elliot.ai/api/v1")
        self.signer = signer
        self.market_id = config.get("market_id", 1)
        self.account_type = config.get("account_type", "STANDARD")
        self.mode = str(config.get("mode", "SHADOW")).upper()
        self.account_index = config.get("account_index")
        self.auth_token = (config.get("auth_token") or os.getenv("SCALPER_AUTH_TOKEN") or "").strip()
        self._resolved_account_index: Optional[int] = self._to_int(self.account_index)
        self._shadow_order_counter = 0

    def _is_shadow_mode(self) -> bool:
        return self.mode == "SHADOW"

    def _build_shadow_ack(self, symbol: str, side: str, price: float, qty: float, nonce: int, order_type: str = "IOC") -> dict:
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
            "order_type": order_type,
        }

    async def create_order(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        nonce: int,
        order_type: str = "IOC",
    ) -> dict:
        """
        Creates an authenticated order on Lighter.xyz.

        Args:
            order_type: One of "IOC", "LIMIT", or "POST_ONLY".
                        IOC   -> timeInForce "IOC"   (immediate-or-cancel, taker)
                        LIMIT -> timeInForce "GTC"   (good-till-cancelled, maker/taker)
                        POST_ONLY -> timeInForce "POST_ONLY" (maker-only, better fees)

        In SHADOW mode this function fail-closes by returning a mock ack before any network I/O.
        """
        order_type = order_type.upper() if order_type else "IOC"
        tif = _ORDER_TYPE_TO_TIF.get(order_type)
        if tif is None:
            return {
                "status": "error",
                "error": f"Invalid order_type '{order_type}'. Must be one of: {', '.join(_ORDER_TYPE_TO_TIF)}",
            }

        if self._is_shadow_mode():
            return self._build_shadow_ack(symbol, side, price, qty, nonce, order_type=order_type)

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
            "timeInForce": tif,
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
        """Fetch recent fills for GhostSweeper reconciliation.

        Official flow:
        1) /accountsByL1Address -> resolve account_index
        2) /trades (auth token required)

        Fallback:
        - /recentTrades with account-index filtering and parser fallback.
        """
        if not self.signer:
            return {"status": "error", "error": "No signer available for fills endpoint."}

        try:
            async with aiohttp.ClientSession() as session:
                account_index, idx_err = await self._resolve_account_index(session)
                if idx_err:
                    return {"status": "error", "error": idx_err}

                trades_result = await self._fetch_account_trades(
                    session=session,
                    account_index=account_index,
                    cursor=cursor,
                    limit=limit,
                )

                if trades_result["status"] == "success":
                    trades = self._extract_trades(trades_result["raw"])
                    fills = self._trades_to_fills(trades, account_index=account_index)
                    if since_ts is not None:
                        fills = [fill for fill in fills if self._to_int(fill.get("timestamp"), default=0) >= since_ts]
                    return {
                        "status": "success",
                        "fills": fills,
                        "next_cursor": trades_result.get("next_cursor"),
                        "raw": trades_result["raw"],
                        "source": "trades",
                        "account_index": account_index,
                    }

                # Fallback path when auth token missing/invalid or endpoint shape drifts.
                fallback_result = await self._fetch_recent_trades(session=session, limit=limit)
                if fallback_result["status"] != "success":
                    return {
                        "status": "error",
                        "error": (
                            f"trades failed ({trades_result.get('error')}); "
                            f"fallback failed ({fallback_result.get('error')})"
                        ),
                    }

                recent = self._extract_trades(fallback_result["raw"])
                fills = self._trades_to_fills(recent, account_index=account_index)
                if since_ts is not None:
                    fills = [fill for fill in fills if self._to_int(fill.get("timestamp"), default=0) >= since_ts]
                return {
                    "status": "success",
                    "fills": fills,
                    "next_cursor": None,
                    "raw": fallback_result["raw"],
                    "source": "recentTrades_fallback",
                    "account_index": account_index,
                    "warning": trades_result.get("error"),
                }
        except Exception as e:
            logger.error(f"Fills Fetch Error: {e}")
            return {"status": "error", "error": str(e)}

    async def _resolve_account_index(self, session: aiohttp.ClientSession) -> Tuple[Optional[int], Optional[str]]:
        if self._resolved_account_index is not None:
            return self._resolved_account_index, None
        if not self.signer or not self.signer.address:
            return None, "No signer address available for account lookup."

        url = f"{self.api_url}/accountsByL1Address"
        status, payload, err_text = await self._get_json(
            session,
            url,
            params={"l1_address": self.signer.address},
        )
        if status != 200:
            msg = self._extract_error_message(payload) or err_text
            return None, f"HTTP {status} account lookup failed: {msg}"

        if not isinstance(payload, dict):
            return None, "Invalid account lookup payload."
        code = self._to_int(payload.get("code"))
        if code is not None and code != 200:
            msg = str(payload.get("message") or "account lookup failed")
            return None, f"Account lookup error {code}: {msg}"

        sub_accounts = payload.get("sub_accounts")
        if not isinstance(sub_accounts, list) or not sub_accounts:
            return None, "No sub_accounts returned for signer address."

        chosen = self._choose_account_index(sub_accounts)
        if chosen is None:
            return None, "Unable to resolve account index from sub_accounts."

        self._resolved_account_index = chosen
        return chosen, None

    def _choose_account_index(self, sub_accounts: List[Dict[str, Any]]) -> Optional[int]:
        if self.account_index is not None:
            configured = self._to_int(self.account_index)
            if configured is not None:
                return configured

        # Prefer active STANDARD accounts when available.
        active_standard: List[int] = []
        active_any: List[int] = []
        any_index: List[int] = []
        for account in sub_accounts:
            if not isinstance(account, dict):
                continue
            idx = self._to_int(account.get("index"))
            if idx is None:
                continue
            any_index.append(idx)
            is_active = self._to_int(account.get("status"), default=0) == 1
            account_type = self._to_int(account.get("account_type"), default=-1)
            if is_active and account_type == 0:
                active_standard.append(idx)
            if is_active:
                active_any.append(idx)

        if active_standard:
            return sorted(active_standard)[0]
        if active_any:
            return sorted(active_any)[0]
        if any_index:
            # Some newly created accounts may be temporarily reported inactive.
            # Prefer deterministic index selection over hard-failing lookup.
            return sorted(any_index)[0]
        return None

    async def _fetch_account_trades(
        self,
        session: aiohttp.ClientSession,
        account_index: int,
        cursor: Optional[str],
        limit: int,
    ) -> Dict[str, Any]:
        url = f"{self.api_url}/trades"
        params: Dict[str, Any] = {
            "sort_by": "timestamp",
            "sort_dir": "desc",
            "limit": max(1, min(int(limit), 100)),
            "market_id": self.market_id,
            "account_index": account_index,
        }
        if cursor:
            params["cursor"] = cursor
        if self.auth_token:
            params["auth"] = self.auth_token

        status, payload, err_text = await self._get_json(session, url, params=params)
        if status != 200:
            msg = self._extract_error_message(payload) or err_text
            return {"status": "error", "error": f"HTTP {status}: {msg}"}
        if not isinstance(payload, dict):
            return {"status": "error", "error": "Invalid trades payload.", "raw": payload}
        code = self._to_int(payload.get("code"))
        if code is not None and code != 200:
            msg = str(payload.get("message") or "trades fetch failed")
            return {"status": "error", "error": f"Trades error {code}: {msg}", "raw": payload}
        return {
            "status": "success",
            "raw": payload,
            "next_cursor": payload.get("next_cursor") or payload.get("nextCursor") or payload.get("cursor"),
        }

    async def _fetch_recent_trades(self, session: aiohttp.ClientSession, limit: int) -> Dict[str, Any]:
        url = f"{self.api_url}/recentTrades"
        params = {"market_id": self.market_id, "limit": max(1, min(int(limit), 100))}
        status, payload, err_text = await self._get_json(session, url, params=params)
        if status != 200:
            msg = self._extract_error_message(payload) or err_text
            return {"status": "error", "error": f"HTTP {status}: {msg}"}
        if not isinstance(payload, dict):
            return {"status": "error", "error": "Invalid recentTrades payload.", "raw": payload}
        code = self._to_int(payload.get("code"))
        if code is not None and code != 200:
            msg = str(payload.get("message") or "recentTrades fetch failed")
            return {"status": "error", "error": f"recentTrades error {code}: {msg}", "raw": payload}
        return {"status": "success", "raw": payload}

    async def _get_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Any, str]:
        async with session.get(url, params=params) as resp:
            status = resp.status
            try:
                payload = await resp.json(content_type=None)
            except Exception:
                payload = None
            if payload is None:
                return status, None, await resp.text()
            return status, payload, ""

    def _extract_trades(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("trades", "fills", "data", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
        return []

    def _trades_to_fills(self, trades: List[Dict[str, Any]], account_index: int) -> List[Dict[str, Any]]:
        fills: List[Dict[str, Any]] = []
        for trade in trades:
            fill = self._trade_to_fill(trade, account_index=account_index)
            if fill:
                fills.append(fill)
        return fills

    def _trade_to_fill(self, trade: Dict[str, Any], account_index: int) -> Optional[Dict[str, Any]]:
        ask_account = self._to_int(trade.get("ask_account_id"))
        bid_account = self._to_int(trade.get("bid_account_id"))
        is_our_ask = ask_account == account_index
        is_our_bid = bid_account == account_index
        if not (is_our_ask or is_our_bid):
            return None

        side = "SELL" if is_our_ask else "BUY"
        is_maker_ask = bool(trade.get("is_maker_ask"))
        is_maker = is_maker_ask if is_our_ask else not is_maker_ask
        role = "maker" if is_maker else "taker"
        fee_key = "maker_fee" if is_maker else "taker_fee"

        tx_hash = (
            trade.get("tx_hash")
            or trade.get("txHash")
            or trade.get("trade_id")
            or trade.get("id")
            or trade.get("order_id")
        )
        symbol = trade.get("symbol") or trade.get("pair")
        if not symbol:
            symbol = "BTC-USDC" if self.market_id == 1 else f"MARKET-{self.market_id}"

        return {
            "timestamp": trade.get("timestamp") or trade.get("transaction_time"),
            "symbol": symbol,
            "side": side,
            "size": trade.get("size") or trade.get("qty") or trade.get("base_amount"),
            "qty": trade.get("size") or trade.get("qty") or trade.get("base_amount"),
            "price": trade.get("price") or trade.get("execution_price"),
            "fee_usd": trade.get(fee_key) if trade.get(fee_key) is not None else trade.get("fee"),
            "tx_hash": str(tx_hash) if tx_hash is not None else "",
            "trade_id": trade.get("trade_id"),
            "role": role,
            "market_id": trade.get("market_id", self.market_id),
        }

    def _extract_error_message(self, payload: Any) -> str:
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("error")
            if message:
                return str(message)
        if isinstance(payload, str):
            return payload
        return ""

    def _to_int(self, value: Any, default: Optional[int] = None) -> Optional[int]:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

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
