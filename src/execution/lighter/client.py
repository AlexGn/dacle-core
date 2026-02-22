"""
DACLE Lighter Real Client
Production-grade client for Lighter.xyz.
Handles authenticated orders, signing, and REST API interactions.
"""

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from src.execution.lighter.signer import LighterSigner

logger = logging.getLogger(__name__)

# Errors that warrant failover to a secondary API URL.
_FAILOVER_ERRORS = (asyncio.TimeoutError, aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError)

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

        # Auth expiry detection (Day 3)
        self._auth_expired_at: float = 0.0
        self._auth_expired_callback = None

        # 5.8: Execution timeouts — strict 500ms default on ALL REST calls.
        timeout_sec = float(config.get("timeout_sec", 0.5))
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec)

        # 5.10: Order deadline/expiration — optional, off by default.
        self.enable_order_deadline = bool(config.get("enable_order_deadline", False))
        self.order_deadline_sec = int(config.get("order_deadline_sec", 5))

        # 5.11: API failover — primary + optional secondary URLs.
        self.api_urls: List[str] = config.get("api_urls") or [self.api_url]
        self.ws_urls: List[str] = config.get("ws_urls") or []

    def set_auth_expired_callback(self, callback):
        """Register async callback for auth expiry detection."""
        self._auth_expired_callback = callback

    def is_auth_expired(self) -> bool:
        return self._auth_expired_at > 0.0

    def clear_auth_expired(self):
        self._auth_expired_at = 0.0

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
            "filled_qty": str(qty),
            "filled_price": str(price),
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

        # 5.10: Optional deadline in signed order data.
        if self.enable_order_deadline:
            order_data["deadline"] = int(time.time()) + self.order_deadline_sec

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

        # 5.10: Include deadline in REST payload when enabled.
        if self.enable_order_deadline:
            payload["deadline"] = order_data["deadline"]

        # 5.11: Try each API URL in order; failover on transient errors.
        last_error: Optional[Exception] = None
        for api_url in self.api_urls:
            url = f"{api_url}/sendTx"
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.post(url, json=payload) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            result = {"status": "success", "order_id": data.get("orderId"), "raw": data}
                            result["filled_qty"] = data.get("filled_qty") or data.get("filledQty")
                            result["filled_price"] = data.get("filled_price") or data.get("filledPrice")
                            return result
                        err_text = await resp.text()
                        return {"status": "error", "error": f"HTTP {resp.status}: {err_text}"}
            except _FAILOVER_ERRORS as e:
                logger.warning(f"Failover: {api_url}/sendTx failed ({type(e).__name__}: {e}), trying next URL")
                last_error = e
                continue
            except Exception as e:
                logger.error(f"Execution Error: {e}")
                return {"status": "error", "error": str(e)}

        # All URLs exhausted.
        error_msg = str(last_error) if last_error else "All API URLs exhausted"
        logger.error(f"Execution Error (all URLs failed): {error_msg}")
        return {"status": "error", "error": error_msg}

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

        Supports API failover (5.11): retries with secondary URL on transient errors.
        """
        if not self.signer:
            return {"status": "error", "error": "No signer available for fills endpoint."}

        original_api_url = self.api_url
        last_error: Optional[Exception] = None

        for api_url in self.api_urls:
            self.api_url = api_url
            # Reset resolved account index so it re-resolves against the new URL.
            saved_account_index = self._resolved_account_index
            if len(self.api_urls) > 1:
                self._resolved_account_index = self._to_int(self.account_index)
            try:
                result = await self._fetch_fills_inner(
                    since_ts=since_ts, cursor=cursor, limit=limit,
                )
                self.api_url = original_api_url
                return result
            except _FAILOVER_ERRORS as e:
                logger.warning(f"Failover: fetch_fills on {api_url} failed ({type(e).__name__}: {e}), trying next URL")
                last_error = e
                self._resolved_account_index = saved_account_index
                continue
            except Exception as e:
                self.api_url = original_api_url
                self._resolved_account_index = saved_account_index
                logger.error(f"Fills Fetch Error: {e}")
                return {"status": "error", "error": str(e)}

        self.api_url = original_api_url
        error_msg = str(last_error) if last_error else "All API URLs exhausted"
        logger.error(f"Fills Fetch Error (all URLs failed): {error_msg}")
        return {"status": "error", "error": error_msg}

    async def _fetch_fills_inner(
        self,
        since_ts: Optional[int] = None,
        cursor: Optional[str] = None,
        limit: int = 100,
    ) -> dict:
        """Inner fetch_fills logic; may raise _FAILOVER_ERRORS for URL failover."""
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
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

            # Auth expiry detection (Day 3)
            if status in (401, 403):
                self._auth_expired_at = time.monotonic()
                logger.critical("Auth token expired: HTTP %d from %s", status, url)
                if self._auth_expired_callback:
                    try:
                        result = self._auth_expired_callback()
                        if asyncio.iscoroutine(result):
                            asyncio.get_running_loop().create_task(result)
                    except Exception:
                        pass  # Best-effort; callback handles its own scheduling

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

    def _to_bool(self, value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return None

    async def get_balance(self) -> dict:
        """Fetch account balance via REST.

        Supports API failover (5.11): tries each URL in api_urls on transient errors.
        """
        if not self.signer:
            return {}

        last_error: Optional[Exception] = None
        for api_url in self.api_urls:
            url = f"{api_url}/account/{self.signer.address}/balances"
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            return await resp.json()
            except _FAILOVER_ERRORS as e:
                logger.warning(f"Failover: {url} failed ({type(e).__name__}: {e}), trying next URL")
                last_error = e
                continue
            except Exception:
                pass
                return {}

        if last_error:
            logger.error(f"get_balance: all API URLs failed: {last_error}")
        return {}

    def _build_balance_url(self, api_url: str) -> str:
        """Build balance endpoint URL."""
        return f"{api_url}/account/{self.signer.address}/balances"

    def _normalize_balance_payload(self, raw: Any) -> dict:
        """Normalize balance response into flat {asset: float} map.
        Handles nested structures, sums available + locked if present."""
        if not isinstance(raw, dict):
            return {}
        result = {}
        for key, value in raw.items():
            if isinstance(value, dict):
                # Sum available + locked to get true position size
                available = self._safe_float(value.get("available", 0))
                locked = self._safe_float(value.get("locked", 0))
                # If neither key exists, try 'balance' or 'total'
                if "available" not in value and "locked" not in value:
                    available = self._safe_float(value.get("balance", value.get("total", 0)))
                result[key] = available + locked
            else:
                result[key] = self._safe_float(value)
        return result

    def _safe_float(self, value: Any) -> float:
        """Convert to float safely, return 0.0 on failure."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    async def get_balance_checked(self, timeout_sec: Optional[float] = None) -> tuple:
        """Tri-state balance fetch with its own error handling.

        Returns (success, balances):
          - (True, {BTC: X, USDT: Y}) — API responded, balances are authoritative
          - (False, {}) — API failed/timeout, balances are UNKNOWN

        SHADOW mode: returns (True, {}) — always succeeds, no balance data.
        """
        if self._is_shadow_mode():
            return (True, {})

        if not self.signer:
            return (False, {})

        effective_timeout = aiohttp.ClientTimeout(total=timeout_sec) if timeout_sec else self.timeout

        for api_url in self.api_urls:
            url = self._build_balance_url(api_url)
            try:
                async with aiohttp.ClientSession(timeout=effective_timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            raw = await resp.json(content_type=None)
                            normalized = self._normalize_balance_payload(raw)
                            return (True, normalized)
                        else:
                            logger.warning(f"get_balance_checked HTTP {resp.status} from {url}")
            except _FAILOVER_ERRORS as e:
                logger.warning(f"get_balance_checked failover: {url} ({type(e).__name__}: {e})")
                continue
            except Exception as e:
                logger.error(f"get_balance_checked error: {e}")
                return (False, {})

        return (False, {})

    async def fetch_nonce(self) -> dict:
        """Fetch the current nonce for this account from the Lighter API.

        In SHADOW mode, returns nonce=0 without any network I/O.
        In LIVE mode, queries the account nonce REST endpoint.

        Returns:
            {"status": "success", "nonce": N} or {"status": "error", "error": "..."}
        """
        if self._is_shadow_mode():
            return {"status": "success", "nonce": 0}

        if not self.signer:
            return {"status": "error", "error": "No signer available for nonce lookup."}

        account_index = self._resolved_account_index
        if account_index is None:
            # Attempt to resolve account index first
            try:
                async with aiohttp.ClientSession() as session:
                    account_index, idx_err = await self._resolve_account_index(session)
                    if idx_err:
                        return {"status": "error", "error": f"Account resolution failed: {idx_err}"}
            except Exception as e:
                return {"status": "error", "error": f"Account resolution error: {e}"}

        url = f"{self.api_url}/account/{account_index}/nonce"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        nonce_val = data if isinstance(data, int) else data.get("nonce", 0) if isinstance(data, dict) else 0
                        return {"status": "success", "nonce": int(nonce_val)}
                    err_text = await resp.text()
                    return {"status": "error", "error": f"HTTP {resp.status}: {err_text}"}
        except Exception as e:
            logger.error(f"Nonce fetch error: {e}")
            return {"status": "error", "error": str(e)}

    async def preflight_live_readiness(self) -> Dict[str, Any]:
        """Validate auth token and infer whether account permissions allow trading."""
        if self._is_shadow_mode():
            return {"status": "success", "auth_ok": True, "can_trade": True, "detail": "shadow_mode"}
        if not self.signer:
            return {"status": "error", "auth_ok": False, "can_trade": False, "detail": "missing signer"}
        if not str(self.auth_token or "").strip():
            return {"status": "error", "auth_ok": False, "can_trade": False, "detail": "missing auth token"}

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                account_index, idx_err = await self._resolve_account_index(session)
                if idx_err:
                    return {"status": "error", "auth_ok": False, "can_trade": False, "detail": idx_err}

                trades = await self._fetch_account_trades(
                    session=session,
                    account_index=account_index,
                    cursor=None,
                    limit=1,
                )
                if trades.get("status") != "success":
                    return {
                        "status": "error",
                        "auth_ok": False,
                        "can_trade": False,
                        "detail": trades.get("error", "trades auth check failed"),
                    }

                permission = await self._infer_account_trade_permission(
                    session=session,
                    account_index=int(account_index),
                )
                can_trade = bool(permission.get("can_trade", True))
                detail = str(permission.get("detail", "ok"))
                return {
                    "status": "success" if can_trade else "error",
                    "auth_ok": True,
                    "can_trade": can_trade,
                    "detail": detail,
                    "account_index": int(account_index),
                }
        except Exception as e:
            return {"status": "error", "auth_ok": False, "can_trade": False, "detail": str(e)}

    async def _infer_account_trade_permission(
        self,
        session: aiohttp.ClientSession,
        account_index: int,
    ) -> Dict[str, Any]:
        if not self.signer or not self.signer.address:
            return {"can_trade": False, "detail": "missing signer address"}

        url = f"{self.api_url}/accountsByL1Address"
        status, payload, err_text = await self._get_json(
            session,
            url,
            params={"l1_address": self.signer.address},
        )
        if status != 200 or not isinstance(payload, dict):
            detail = self._extract_error_message(payload) or err_text or "account permission lookup failed"
            return {"can_trade": False, "detail": detail}

        sub_accounts = payload.get("sub_accounts")
        if not isinstance(sub_accounts, list):
            return {"can_trade": False, "detail": "account permission payload missing sub_accounts"}

        chosen = None
        for account in sub_accounts:
            if not isinstance(account, dict):
                continue
            if self._to_int(account.get("index")) == int(account_index):
                chosen = account
                break

        if not isinstance(chosen, dict):
            return {"can_trade": False, "detail": "resolved account index missing in account lookup"}

        # Fail closed if explicit read-only flags are present.
        for key in ("read_only", "is_read_only", "readonly", "trade_disabled", "is_trade_disabled"):
            if key in chosen and self._to_bool(chosen.get(key)) is True:
                return {"can_trade": False, "detail": f"{key}=true"}

        for key in ("can_trade", "trade_enabled", "is_trade_enabled", "write_enabled", "can_write"):
            value = self._to_bool(chosen.get(key))
            if value is False:
                return {"can_trade": False, "detail": f"{key}=false"}

        permissions = chosen.get("permissions")
        if isinstance(permissions, str):
            normalized = permissions.strip().lower()
            if "read_only" in normalized or normalized == "read":
                return {"can_trade": False, "detail": f"permissions={permissions}"}
        if isinstance(permissions, list):
            normalized_items = {str(item).strip().lower() for item in permissions}
            if "read_only" in normalized_items:
                return {"can_trade": False, "detail": "permissions include read_only"}
            if "trade" not in normalized_items and "write" not in normalized_items and "read" in normalized_items:
                return {"can_trade": False, "detail": "permissions missing trade/write"}
        if isinstance(permissions, dict):
            for key in ("can_trade", "trade_enabled", "write_enabled"):
                value = self._to_bool(permissions.get(key))
                if value is False:
                    return {"can_trade": False, "detail": f"permissions.{key}=false"}

        return {"can_trade": True, "detail": "ok"}

    async def cancel_order(self, order_id: Any, nonce: int) -> dict:
        """
        Cancel a single order by ID.

        In SHADOW mode returns a success stub without network I/O.
        In LIVE mode creates its own aiohttp session and delegates to _cancel_order.
        """
        if self._is_shadow_mode():
            return {"status": "success", "shadow": True, "order_id": str(order_id)}

        if not self.signer:
            return {"status": "error", "error": "No signer available for cancel_order."}

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                return await self._cancel_order(session, order_id, nonce)
        except Exception as e:
            logger.error(f"cancel_order error: {e}")
            return {"status": "error", "error": str(e)}

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> dict:
        """
        Best-effort cancel-all used by poison-pill shutdown.

        5.5: In LIVE mode, fetches open orders via GET /orders, then cancels each
        via POST /sendTx with a cancel payload. In SHADOW mode returns a success stub.
        """
        if self._is_shadow_mode():
            return {"status": "success", "shadow": True, "cancelled": 0}

        if not self.signer:
            return {"status": "error", "error": "No signer available for cancel_all_orders."}

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                orders = await self._fetch_open_orders(session)
                if orders is None:
                    return {"status": "error", "error": "Failed to fetch open orders."}

                # Filter by symbol if requested.
                if symbol:
                    orders = [
                        o for o in orders
                        if o.get("symbol") == symbol or o.get("market_id") == self.market_id
                    ]

                if not orders:
                    return {"status": "success", "cancelled": 0}

                cancelled = 0
                errors: List[str] = []
                for order in orders:
                    order_id = order.get("order_id") or order.get("orderId") or order.get("id")
                    nonce = order.get("nonce", 0)
                    result = await self._cancel_order(session, order_id, nonce)
                    if result.get("status") == "success":
                        cancelled += 1
                    else:
                        errors.append(f"order {order_id}: {result.get('error', 'unknown')}")

                response: Dict[str, Any] = {"status": "success", "cancelled": cancelled}
                if errors:
                    response["errors"] = errors
                return response
        except Exception as e:
            logger.error(f"cancel_all_orders error: {e}")
            return {"status": "error", "error": str(e)}

    async def fetch_open_orders(self, timeout_sec: Optional[float] = None) -> Optional[list]:
        """Public wrapper for open orders fetch.

        SHADOW returns [].
        LIVE creates session + calls _fetch_open_orders.
        Returns None on failure (consumers MUST treat None as 'unknown, skip').
        """
        if self._is_shadow_mode():
            return []

        if not self.signer:
            return None

        effective_timeout = aiohttp.ClientTimeout(total=timeout_sec) if timeout_sec else self.timeout

        try:
            async with aiohttp.ClientSession(timeout=effective_timeout) as session:
                return await self._fetch_open_orders(session)
        except Exception as e:
            logger.error(f"fetch_open_orders error: {e}")
            return None

    async def _fetch_open_orders(self, session: aiohttp.ClientSession) -> Optional[List[Dict[str, Any]]]:
        """Fetch open orders via GET /orders."""
        url = f"{self.api_url}/orders"
        params: Dict[str, Any] = {"market_id": self.market_id}

        account_idx = self._resolved_account_index
        if account_idx is not None:
            params["account_index"] = account_idx
        if self.auth_token:
            params["auth"] = self.auth_token

        status, payload, err_text = await self._get_json(session, url, params=params)
        if status != 200:
            logger.error(f"_fetch_open_orders HTTP {status}: {err_text}")
            return None

        if isinstance(payload, dict):
            orders = payload.get("orders")
            if isinstance(orders, list):
                return orders
            # Some API shapes return data directly.
            data = payload.get("data")
            if isinstance(data, list):
                return data
        if isinstance(payload, list):
            return payload
        return []

    async def _cancel_order(self, session: aiohttp.ClientSession, order_id: Any, nonce: int) -> dict:
        """Cancel a single order via POST /sendTx."""
        url = f"{self.api_url}/sendTx"
        payload = {
            "type": "cancel_order",
            "orderId": str(order_id),
            "nonce": nonce,
        }

        if self.signer:
            cancel_data = {"orderId": str(order_id), "nonce": nonce}
            try:
                payload["signature"] = self.signer.sign_order({
                    "marketId": self.market_id,
                    "side": 0,
                    "price": 0,
                    "size": 0,
                    "nonce": nonce,
                })
            except Exception as e:
                logger.warning(f"Failed to sign cancel for order {order_id}: {e}")

        try:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return {"status": "success"}
                err_text = await resp.text()
                return {"status": "error", "error": f"HTTP {resp.status}: {err_text}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
