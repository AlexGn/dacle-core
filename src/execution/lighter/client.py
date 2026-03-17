"""
DACLE Lighter Real Client
Production-grade client for Lighter.xyz.
Handles authenticated orders, signing, and REST API interactions.
"""

import asyncio
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from src.execution.lighter.signer import LighterSigner
from src.utils.network import get_standard_headers

logger = logging.getLogger(__name__)

# Errors that warrant failover to a secondary API URL.
_FAILOVER_ERRORS = (asyncio.TimeoutError, aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError)
_FAILOVER_HTTP_STATUS_CODES = {502, 503, 504, 520, 522, 524}
_QUOTE_BALANCE_ASSET = "USDC"

# Valid order types and their Lighter API timeInForce mapping.
_ORDER_TYPE_TO_TIF = {
    "IOC": "IOC",
    "LIMIT": "GTC",
    "POST_ONLY": "POST_ONLY",
}


class MarketMetadata:
    def __init__(
        self,
        price_decimals: int = 1,
        size_decimals: int = 5,
        min_base_amount: float = 0.0,
        min_quote_amount: float = 0.0,
    ):
        self.price_decimals = price_decimals
        self.size_decimals = size_decimals
        self.min_base_amount = max(0.0, float(min_base_amount))
        self.min_quote_amount = max(0.0, float(min_quote_amount))

class LighterRealClient:
    def __init__(self, config: dict, signer: Optional[LighterSigner] = None, risk_ledger=None, redis_client=None):
        self.api_url = config.get("api_url", "https://mainnet.zklighter.elliot.ai/api/v1")
        self.signer = signer
        self.risk_ledger = risk_ledger
        self.market_id = config.get("market_id", 1)
        configured_account_tier = config.get("account_tier", config.get("account_type", "STANDARD"))
        self.account_tier = self._normalize_account_tier(configured_account_tier)
        # Backward-compatible alias for older callers that still read account_type.
        self.account_type = self.account_tier
        self.mode = str(config.get("mode", "SHADOW")).upper()
        use_signer_sendtx = self._to_bool(config.get("use_signer_client_sendtx"))
        self.use_signer_client_sendtx = bool(use_signer_sendtx) if use_signer_sendtx is not None else False
        configured_account_index = config.get("account_index")
        if configured_account_index is None and self.mode == "LIVE":
            env_account_index = str(os.getenv("SCALPER_ACCOUNT_INDEX") or "").strip()
            if env_account_index:
                configured_account_index = env_account_index
        self.account_index = configured_account_index
        self.auth_refresh_interval_sec = int(config.get("auth_refresh_interval_sec", 1200))
        self.auth_token = (config.get("auth_token") or os.getenv("SCALPER_AUTH_TOKEN") or "").strip()
        self._resolved_account_index: Optional[int] = self._to_int(self.account_index)
        self._resolved_account_type: Optional[int] = None
        self._account_tier_mismatch: bool = False
        explicit_account_required = self._to_bool(config.get("require_explicit_account_index"))
        self.require_explicit_account_index = (
            bool(explicit_account_required)
            if explicit_account_required is not None
            else self.mode == "LIVE"
        )
        enforce_tier_match = self._to_bool(config.get("enforce_account_tier_match"))
        self.enforce_account_tier_match = (
            bool(enforce_tier_match)
            if enforce_tier_match is not None
            else self.mode == "LIVE"
        )
        self._shadow_order_counter = 0
        self.degraded_snapshot_spread_bps = float(config.get("degraded_snapshot_spread_bps", 2.0))
        
        # Phase 2: Account Metadata Cache
        self.redis = redis_client
        self.cache = None
        if self.redis:
            from src.execution.lighter.account_cache import LighterAccountCache
            self.cache = LighterAccountCache(self.redis)
        
        # Market Metadata Cache
        self._market_metadata: Dict[int, MarketMetadata] = {
            1: MarketMetadata(price_decimals=1, size_decimals=5) # BTC Default
        }
        self._metadata_lock = asyncio.Lock()

        # Auth expiry detection (Day 3)
        self._auth_expired_at: float = 0.0
        self._auth_expired_callback = None
        self._reactive_auth_lock = asyncio.Lock()
        self._last_preflight_readiness: Dict[str, Any] = {}

        # 5.8: Execution timeouts — strict 500ms default on ALL REST calls.
        timeout_sec = float(config.get("timeout_sec", 0.5))
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec)

        # 5.10: Order deadline/expiration — optional, off by default.
        self.enable_order_deadline = bool(config.get("enable_order_deadline", False))
        self.order_deadline_sec = int(config.get("order_deadline_sec", 5))

        # 5.11: API failover — primary + optional secondary URLs.
        self.api_urls: List[str] = config.get("api_urls") or [self.api_url]
        self.ws_urls: List[str] = config.get("ws_urls") or []

    async def _get_market_metadata(self, market_id: int) -> MarketMetadata:
        """Fetch and cache decimals for a specific market."""
        if market_id in self._market_metadata:
            return self._market_metadata[market_id]
            
        async with self._metadata_lock:
            # Re-check after acquiring lock
            if market_id in self._market_metadata:
                return self._market_metadata[market_id]
                
            url = f"{self.api_url}/orderBooks"
            try:
                async with aiohttp.ClientSession(timeout=self.timeout, headers=get_standard_headers()) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for ob in data.get("order_books", []):
                                mid = int(ob["market_id"])
                                self._market_metadata[mid] = MarketMetadata(
                                    price_decimals=int(ob["supported_price_decimals"]),
                                    size_decimals=int(ob["supported_size_decimals"]),
                                    min_base_amount=float(ob.get("min_base_amount") or 0.0),
                                    min_quote_amount=float(ob.get("min_quote_amount") or 0.0),
                                )
            except Exception as e:
                logger.warning(f"Failed to fetch market metadata: {e}")
                
            return self._market_metadata.get(market_id, MarketMetadata())

    def set_auth_expired_callback(self, callback):
        """Register async callback for auth expiry detection."""
        self._auth_expired_callback = callback

    def is_auth_expired(self) -> bool:
        return self._auth_expired_at > 0.0

    def clear_auth_expired(self):
        self._auth_expired_at = 0.0

    def _api_root_url(self, api_url: str) -> str:
        url = str(api_url or "").strip().rstrip("/")
        marker = "/api/"
        idx = url.find(marker)
        if idx > 0:
            return url[:idx]
        return url

    def _explorer_root_url(self) -> str:
        root = self._api_root_url(self.api_url)
        if "mainnet.zklighter.elliot.ai" in root:
            return "https://explorer.elliot.ai/api"
        return root.rstrip("/") + "/explorer/api"

    async def resolve_account_index(self, session: Optional[aiohttp.ClientSession] = None, api_url: Optional[str] = None) -> Tuple[Optional[int], Optional[str]]:
        """
        Deterministic resolver for account index.
        1. Check memory cache (self._resolved_account_index).
        2. Check configured property (self.account_index).
        3. Check environment variable (SCALPER_ACCOUNT_INDEX).
        4. Check persistent cache (Phase 2 AccountCache).
        5. Fetch from network (/accountsByL1Address).
        """
        # 1. Memory cache
        if self._resolved_account_index is not None:
            return self._resolved_account_index, None

        # 2. Configured property
        explicit = self._to_int(self.account_index)
        if explicit is not None:
            self._resolved_account_index = explicit
            return explicit, None

        # 3. Environment variable
        env_idx = self._to_int(os.getenv("SCALPER_ACCOUNT_INDEX"))
        if env_idx is not None:
            self._resolved_account_index = env_idx
            return env_idx, None

        if not self.signer or not self.signer.address:
            return None, "missing_signer_address"

        # 4. Persistent cache (Redis)
        if self.cache:
            cached = await self.cache.get_account_info(self.signer.address)
            if cached and "index" in cached:
                idx = int(cached["index"])
                self._resolved_account_index = idx
                self._set_account_tier_resolution(
                    account_index=idx,
                    account_type=self._to_int(cached.get("account_type")),
                )
                logger.debug(f"Resolved account index {idx} from cache.")
                return idx, None

        # 5. Network fetch
        try:
            if session:
                return await self._fetch_account_index_network(session, api_url=api_url)
            else:
                async with aiohttp.ClientSession(timeout=self.timeout, headers=get_standard_headers()) as new_session:
                    return await self._fetch_account_index_network(new_session, api_url=api_url)
        except Exception as exc:
            return None, str(exc)

    async def _fetch_account_index_network(self, session: aiohttp.ClientSession, api_url: Optional[str] = None) -> Tuple[Optional[int], Optional[str]]:
        """Internal network fetch for /accountsByL1Address."""
        base_url = str(api_url or self.api_url).rstrip("/")
        url = f"{base_url}/accountsByL1Address"
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
        
        sub_accounts = payload.get("sub_accounts")
        if not isinstance(sub_accounts, list) or not sub_accounts:
            return None, "No sub_accounts returned for signer address."

        chosen = self._choose_account_index(sub_accounts)
        if chosen is None:
            return None, "Unable to resolve account index from sub_accounts."

        self._resolved_account_index = chosen
        acc_type = self._lookup_account_type(sub_accounts, chosen)
        self._set_account_tier_resolution(account_index=chosen, account_type=acc_type)
        
        # Phase 2: Persist to cache
        if self.cache:
            cache_payload: Dict[str, Any] = {"index": chosen}
            if acc_type is not None:
                cache_payload["account_type"] = acc_type
            await self.cache.set_account_info(self.signer.address, cache_payload)
            
        return chosen, None

    async def _resolve_runtime_account_index(self) -> Tuple[Optional[int], Optional[str]]:
        """Deprecated: Use resolve_account_index instead."""
        return await self.resolve_account_index()

    async def refresh_auth_token_runtime(self, reason: str = "runtime_refresh") -> dict:
        """Regenerate auth token in-process without writing .env."""
        if self._is_shadow_mode():
            return {"status": "success", "source": "shadow", "token_changed": False}

        api_private_key = (os.getenv("LIGHTER_API_PRIVATE_KEY") or "").strip()
        if not api_private_key:
            return {"status": "error", "retryable": False, "error": "LIGHTER_API_PRIVATE_KEY missing"}

        account_index, idx_err = await self._resolve_runtime_account_index()
        if idx_err or account_index is None:
            return {"status": "error", "retryable": True, "error": f"account_index_unavailable: {idx_err}"}
        if self.enforce_account_tier_match and self._account_tier_mismatch:
            return self._account_tier_mismatch_error(context="auth_refresh")

        api_key_index = self._to_int(os.getenv("SCALPER_API_KEY_INDEX"), default=0)
        token_ttl_sec = self._to_int(os.getenv("SCALPER_AUTH_TOKEN_TTL_SEC"), default=None)
        if token_ttl_sec is None:
            token_ttl_sec = max(600, int(self.auth_refresh_interval_sec) + 120)
        else:
            token_ttl_sec = max(60, int(token_ttl_sec))

        root_url = self._api_root_url(self.api_url)
        if not root_url:
            return {"status": "error", "retryable": False, "error": "api_url_missing"}

        signer_client = None
        try:
            from lighter.signer_client import SignerClient

            signer_client = SignerClient(
                url=root_url,
                account_index=int(account_index),
                api_private_keys={int(api_key_index): api_private_key},
            )
            token, err = signer_client.create_auth_token_with_expiry(
                deadline=int(token_ttl_sec),
                api_key_index=int(api_key_index),
            )
            if err:
                return {"status": "error", "retryable": True, "error": str(err)}
            token = str(token or "").strip()
            if not token:
                return {"status": "error", "retryable": False, "error": "empty token generated"}

            previous = str(self.auth_token or "")
            self.auth_token = token
            self.clear_auth_expired()
            logger.info(
                "AUTH_RUNTIME_REFRESH_OK reason=%s account_index=%s api_key_index=%s ttl_sec=%s changed=%s",
                reason,
                account_index,
                api_key_index,
                token_ttl_sec,
                token != previous,
            )
            return {
                "status": "success",
                "source": "runtime",
                "token_changed": token != previous,
                "account_index": int(account_index),
                "api_key_index": int(api_key_index),
            }
        except Exception as exc:
            return {"status": "error", "retryable": True, "error": str(exc)}
        finally:
            if signer_client is not None:
                api_client = getattr(signer_client, "api_client", None)
                if api_client is not None:
                    try:
                        await api_client.close()
                    except Exception:
                        pass

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

    def _resolve_lighter_api_private_key(self) -> str:
        return str(
            os.getenv("LIGHTER_API_PRIVATE_KEY")
            or os.getenv("SCALPER_API_PRIVATE_KEY")
            or ""
        ).strip()

    async def _create_order_via_signer_client(
        self,
        *,
        side: str,
        price_int: int,
        size_int: int,
        nonce: int,
        order_type: str,
        is_reduce_only: bool,
    ) -> Tuple[bool, dict]:
        """
        Submit live orders through the official Lighter signer client/sendTx form contract.

        Returns:
            (attempted, result)
            - attempted=False when signer-client path is unavailable and caller should
              continue with legacy JSON path.
            - attempted=True with success/error result when signer-client path was used.
        """
        if not self.use_signer_client_sendtx:
            return False, {}
        api_private_key = self._resolve_lighter_api_private_key()
        if not api_private_key:
            return False, {}

        try:
            from lighter.signer_client import SignerClient
        except Exception:
            return False, {}

        account_index, idx_err = await self._resolve_runtime_account_index()
        if idx_err or account_index is None:
            return True, {
                "status": "error",
                "error": f"account_index_unavailable: {idx_err}",
                "error_code": "API_ERROR",
            }
        if self.enforce_account_tier_match and self._account_tier_mismatch:
            return True, self._account_tier_mismatch_error(context="create_order")

        api_key_index = int(self._to_int(os.getenv("SCALPER_API_KEY_INDEX"), default=0) or 0)
        is_ask = str(side).upper() != "BUY"
        order_type_u = str(order_type or "IOC").upper()

        # Mirror Lighter SDK semantics:
        # - IOC => market order + IOC tif + immediate expiry
        # - LIMIT => limit + GTT
        # - POST_ONLY => limit + post-only tif
        if order_type_u == "IOC":
            sdk_order_type = SignerClient.ORDER_TYPE_MARKET
            sdk_tif = SignerClient.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL
            sdk_expiry = SignerClient.DEFAULT_IOC_EXPIRY
        elif order_type_u == "LIMIT":
            sdk_order_type = SignerClient.ORDER_TYPE_LIMIT
            sdk_tif = SignerClient.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME
            sdk_expiry = SignerClient.DEFAULT_28_DAY_ORDER_EXPIRY
        else:  # POST_ONLY
            sdk_order_type = SignerClient.ORDER_TYPE_LIMIT
            sdk_tif = SignerClient.ORDER_TIME_IN_FORCE_POST_ONLY
            sdk_expiry = SignerClient.DEFAULT_28_DAY_ORDER_EXPIRY

        last_error = "unknown signer-client failure"
        for api_url in self.api_urls:
            root_url = self._api_root_url(api_url)
            if not root_url:
                continue
            signer_client = None
            try:
                signer_client = SignerClient(
                    url=root_url,
                    account_index=int(account_index),
                    api_private_keys={api_key_index: api_private_key},
                )
                submit_nonce = int(nonce)
                submit_api_key_index = int(api_key_index)
                submit_client_order_index = int(nonce)
                if submit_nonce < 0:
                    submit_nonce = int(getattr(SignerClient, "DEFAULT_NONCE", -1))
                    submit_api_key_index = int(getattr(SignerClient, "DEFAULT_API_KEY_INDEX", 255))
                    submit_client_order_index = max(1, int(time.time() * 1000) & 0x7FFFFFFF)
                _tx, resp, err = await signer_client.create_order(
                    market_index=int(self.market_id),
                    client_order_index=int(submit_client_order_index),
                    base_amount=int(size_int),
                    price=int(price_int),
                    is_ask=bool(is_ask),
                    order_type=int(sdk_order_type),
                    time_in_force=int(sdk_tif),
                    reduce_only=bool(is_reduce_only),
                    order_expiry=int(sdk_expiry),
                    nonce=int(submit_nonce),
                    api_key_index=int(submit_api_key_index),
                )
                if err:
                    last_error = str(err)
                    continue

                payload = resp.to_dict() if hasattr(resp, "to_dict") else dict(resp or {})
                code = int(payload.get("code") or 0)
                if code == 200:
                    tx_hash = str(payload.get("tx_hash") or "")
                    return True, {"status": "success", "order_id": tx_hash, "raw": payload}

                last_error = str(payload.get("message") or f"code={code}")
            except Exception as exc:
                last_error = str(exc)
                continue
            finally:
                if signer_client is not None:
                    api_client = getattr(signer_client, "api_client", None)
                    if api_client is not None:
                        try:
                            await api_client.close()
                        except Exception:
                            pass

        error_code = "NONCE_ERROR" if "nonce" in last_error.lower() else "API_ERROR"
        return True, {"status": "error", "error": last_error, "error_code": error_code}

    async def create_order(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        nonce: int,
        order_type: str = "IOC",
        is_reduce_only: bool = False,
        is_emergency_exit: bool = False,
    ) -> dict:
        """
        Creates an authenticated order on Lighter.xyz.

        Args:
            order_type: One of "IOC", "LIMIT", or "POST_ONLY".
                        IOC   -> timeInForce "IOC"   (immediate-or-cancel, taker)
                        LIMIT -> timeInForce "GTC"   (good-till-cancelled, maker/taker)
                        POST_ONLY -> timeInForce "POST_ONLY" (maker-only, better fees)
            is_reduce_only: When True, bypass notional-cap risk checks so exit orders
                        can always flatten exposure.
            is_emergency_exit: Enables reactive auth refresh + single retry on 401/403.

        In SHADOW mode this function fail-closes by returning a mock ack before any network I/O.
        """
        emergency_exit_mode = bool(is_emergency_exit or is_reduce_only)
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
        if self.enforce_account_tier_match and self._account_tier_mismatch:
            return self._account_tier_mismatch_error(context="create_order")

        # Resolve venue metadata and enforce minimum tradable size/notional before submit.
        meta = await self._get_market_metadata(self.market_id)
        price_f = float(price)
        qty_f = float(qty)
        min_qty = float(meta.min_base_amount or 0.0)
        if price_f > 0 and float(meta.min_quote_amount or 0.0) > 0.0:
            min_qty = max(min_qty, float(meta.min_quote_amount) / price_f)
        if min_qty > 0.0 and qty_f < min_qty:
            qty_f = min_qty
            logger.info(
                "ORDER_SIZE_FLOOR_APPLIED market_id=%s symbol=%s qty=%.8f min_qty=%.8f",
                self.market_id,
                symbol,
                float(qty),
                min_qty,
            )

        size_scale = 10 ** int(meta.size_decimals)
        size_int = max(1, int(math.ceil(qty_f * size_scale)))
        qty_f = size_int / size_scale
        price_scale = 10 ** int(meta.price_decimals)
        price_int = max(1, int(round(price_f * price_scale)))
        price_f = price_int / price_scale

        # Prefer official signer-client/sendTx flow in LIVE mode when API keys
        # are available. Legacy JSON path remains as compatibility fallback.
        attempted_signer_client, signer_client_result = await self._create_order_via_signer_client(
            side=side,
            price_int=price_int,
            size_int=size_int,
            nonce=int(nonce),
            order_type=order_type,
            is_reduce_only=bool(is_reduce_only),
        )
        if attempted_signer_client:
            return signer_client_result

        # 1. Pre-send check against real-time risk ledger
        if self.risk_ledger:
            intent_notional = price_f * qty_f
            is_allowed, reason = await self.risk_ledger.check_order_allowed(
                intent_notional,
                is_reduce_only=is_reduce_only,
            )
            if not is_allowed:
                logger.warning(f"Order blocked by Risk Ledger: {reason}")
                return {"status": "error", "error": f"BLOCKED_BY_RISK_LEDGER: {reason}", "error_code": "RISK_BLOCK"}
        
        # Resolve V2 fields for EIP-712 signing
        account_index = self._resolved_account_index
        if account_index is None:
            # Fallback to resolving it now if needed
            logger.info("Resolving account index for V2 signing...")
            try:
                # We use a short-lived session here if none provided
                async with aiohttp.ClientSession(timeout=self.timeout, headers=get_standard_headers()) as session:
                    account_index, _ = await self.resolve_account_index(session)
            except Exception as e:
                logger.warning(f"Failed to resolve account index for V2 signing: {e}")
                account_index = 0

        # Resolve SDK-specific order type values for signing
        # Matches logic in _create_order_via_signer_client
        sdk_order_type = 0 # LIMIT
        sdk_expiry = int(time.time()) + 3600 * 24 * 28 # 28 days
        if order_type == "IOC":
            sdk_order_type = 1 # MARKET/IOC
            sdk_expiry = int(time.time()) + 300 # 5 min
        elif order_type == "POST_ONLY":
            sdk_order_type = 0 # LIMIT
        
        order_data = {
            "subAccountIndex": int(account_index or 0),
            "marketId": self.market_id,
            "side": 0 if side == "BUY" else 1,
            "price": price_int,
            "size": size_int,
            "orderType": sdk_order_type,
            "nonce": nonce,
            "orderExpiry": sdk_expiry,
        }

        signature = self.signer.sign_order(order_data)

        payload = {
            "type": "create_order",
            "marketId": self.market_id,
            "side": side.lower(),
            "price": str(price_f),
            "size": str(qty_f),
            "nonce": nonce,
            "signature": signature,
            "timeInForce": tif,
            "subAccountIndex": int(account_index or 0),
            "orderType": sdk_order_type,
            "orderExpiry": sdk_expiry,
        }

        # 5.11: Try each API URL in order; failover on transient errors.
        last_error: Optional[Exception] = None
        for api_url in self.api_urls:
            url = f"{api_url}/sendTx"
            did_reactive_retry = False
            try:
                # 2. Mid-cycle kill-switch check right before transmit
                if self.risk_ledger:
                    intent_notional = price_f * qty_f
                    is_allowed, reason = await self.risk_ledger.check_order_allowed(
                        intent_notional,
                        is_reduce_only=is_reduce_only,
                    )
                    if not is_allowed:
                        logger.critical(f"Transmission blocked mid-cycle by Risk Ledger: {reason}")
                        return {"status": "error", "error": f"BLOCKED_MID_CYCLE: {reason}", "error_code": "RISK_BLOCK"}

                async with aiohttp.ClientSession(timeout=self.timeout, headers=get_standard_headers()) as session:
                    status, response_payload, err_text = await self._post_json(session, url, json_payload=payload)

                    # Exit safety: on auth expiry, trigger immediate refresh and retry once.
                    if status in (401, 403) and emergency_exit_mode and not did_reactive_retry:
                        refreshed = await self._reactive_auth_refresh_once("create_order_401_403")
                        if refreshed:
                            did_reactive_retry = True
                            status, response_payload, err_text = await self._post_json(session, url, json_payload=payload)

                    if status == 200:
                        data = response_payload
                        result = {"status": "success", "order_id": data.get("orderId"), "raw": data}
                        result["filled_qty"] = data.get("filled_qty") or data.get("filledQty")
                        result["filled_price"] = data.get("filled_price") or data.get("filledPrice")
                        return result

                    if self._is_retryable_failover_status(status):
                        msg = self._extract_error_message(response_payload) or err_text or f"HTTP {status}"
                        last_error = RuntimeError(f"HTTP {status}: {msg}")
                        logger.warning(
                            "Failover: %s returned retryable HTTP %s (%s), trying next URL",
                            url,
                            status,
                            msg,
                        )
                        continue
                    
                    # Structured error classification
                    error_code = "API_ERROR"
                    try:
                        err_code_field = str(response_payload.get("code", "") or response_payload.get("error_code", "")).lower() if response_payload else ""
                        if "nonce" in err_code_field or "sequence" in err_code_field:
                            error_code = "NONCE_ERROR"
                    except Exception:
                        # Fallback: substring match on raw text
                        lower_text = err_text.lower()
                        if "nonce" in lower_text or "sequence" in lower_text:
                            error_code = "NONCE_ERROR"
                    return {
                        "status": "error",
                        "error": f"HTTP {status}: {self._extract_error_message(response_payload) or err_text}",
                        "error_code": error_code,
                        "http_status": status,
                    }
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

        last_error: Optional[Exception] = None

        for api_url in self.api_urls:
            # Reset resolved account index so it re-resolves against the new URL.
            saved_account_index = self._resolved_account_index
            if len(self.api_urls) > 1:
                self._resolved_account_index = self._to_int(self.account_index)
            try:
                result = await self._fetch_fills_inner(
                    since_ts=since_ts,
                    cursor=cursor,
                    limit=limit,
                    api_url=api_url,
                )
                if result.get("status") == "error" and self._is_retryable_fill_result(result):
                    err = str(result.get("error") or "retryable fills error")
                    logger.warning(
                        "Failover: fetch_fills on %s returned retryable error (%s), trying next URL",
                        api_url,
                        err,
                    )
                    last_error = RuntimeError(err)
                    self._resolved_account_index = saved_account_index
                    continue
                return result
            except _FAILOVER_ERRORS as e:
                logger.warning(f"Failover: fetch_fills on {api_url} failed ({type(e).__name__}: {e}), trying next URL")
                last_error = e
                self._resolved_account_index = saved_account_index
                continue
            except Exception as e:
                self._resolved_account_index = saved_account_index
                logger.error(f"Fills Fetch Error: {e}")
                return {"status": "error", "error": str(e)}

        error_msg = str(last_error) if last_error else "All API URLs exhausted"
        logger.error(f"Fills Fetch Error (all URLs failed): {error_msg}")
        return {"status": "error", "error": error_msg}

    async def _fetch_fills_inner(
        self,
        since_ts: Optional[int] = None,
        cursor: Optional[str] = None,
        limit: int = 100,
        api_url: Optional[str] = None,
    ) -> dict:
        """Inner fetch_fills logic; may raise _FAILOVER_ERRORS for URL failover."""
        base_api_url = str(api_url or self.api_url).rstrip("/")
        async with aiohttp.ClientSession(timeout=self.timeout, headers=get_standard_headers()) as session:
            account_index, idx_err = await self.resolve_account_index(session, api_url=base_api_url)
            if idx_err:
                return {"status": "error", "error": idx_err}

            trades_result = await self._fetch_account_trades(
                session=session,
                account_index=account_index,
                cursor=cursor,
                limit=limit,
                api_url=base_api_url,
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

            trades_error = str(trades_result.get("error") or "")
            # Do not compound provider throttling by immediately hitting a second endpoint
            # when the primary account-trades call already returned 429.
            if "HTTP 429" in trades_error or "429" in trades_error:
                return {"status": "error", "error": trades_error}

            # Fallback path when auth token missing/invalid or endpoint shape drifts.
            fallback_result = await self._fetch_recent_trades(
                session=session,
                limit=limit,
                api_url=base_api_url,
            )
            if fallback_result["status"] != "success":
                return {
                    "status": "error",
                    "error": (
                        f"trades failed ({trades_error}); "
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


    def _choose_account_index(self, sub_accounts: List[Dict[str, Any]]) -> Optional[int]:
        if self.account_index is not None:
            configured = self._to_int(self.account_index)
            if configured is not None:
                return configured

        preferred_account_type = self._preferred_account_type_id()
        active_preferred: List[int] = []
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
            if is_active and account_type == preferred_account_type:
                active_preferred.append(idx)
            if is_active:
                active_any.append(idx)

        if self.require_explicit_account_index and self.mode == "LIVE":
            # Silo guard: never auto-pick from multiple candidates in LIVE mode.
            candidates = sorted(set(any_index))
            if len(candidates) == 1:
                return candidates[0]
            logger.error(
                "LIVE account selection ambiguous: explicit account_index is required "
                "(candidates=%s)",
                candidates,
            )
            return None

        if active_preferred:
            return sorted(active_preferred)[0]
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
        api_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        base_api_url = str(api_url or self.api_url).rstrip("/")
        url = f"{base_api_url}/trades"
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
            res = {
                "status": "error",
                "error": f"HTTP {status}: {msg}",
                "http_status": status,
                "retryable_failover": self._is_retryable_failover_status(status),
            }
            if status == 429 and isinstance(payload, dict) and "retry_after_sec" in payload:
                res["retry_after_sec"] = payload["retry_after_sec"]
            return res
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

    async def _fetch_recent_trades(
        self,
        session: aiohttp.ClientSession,
        limit: int,
        api_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        base_api_url = str(api_url or self.api_url).rstrip("/")
        url = f"{base_api_url}/recentTrades"
        params = {"market_id": self.market_id, "limit": max(1, min(int(limit), 100))}
        status, payload, err_text = await self._get_json(session, url, params=params)
        if status != 200:
            msg = self._extract_error_message(payload) or err_text
            res = {
                "status": "error",
                "error": f"HTTP {status}: {msg}",
                "http_status": status,
                "retryable_failover": self._is_retryable_failover_status(status),
            }
            if status == 429 and isinstance(payload, dict) and "retry_after_sec" in payload:
                res["retry_after_sec"] = payload["retry_after_sec"]
            return res
        if not isinstance(payload, dict):
            return {"status": "error", "error": "Invalid recentTrades payload.", "raw": payload}
        code = self._to_int(payload.get("code"))
        if code is not None and code != 200:
            msg = str(payload.get("message") or "recentTrades fetch failed")
            return {"status": "error", "error": f"recentTrades error {code}: {msg}", "raw": payload}
        return {"status": "success", "raw": payload}

    def _parse_retry_after(self, retry_after: Optional[str]) -> float:
        """
        Robustly parse Retry-After header.
        Supports: 
        - Numeric seconds (e.g. "30")
        - UTC Epoch (> 1,000,000,000)
        """
        if not retry_after:
            return 0.0
        try:
            val = float(str(retry_after).strip())
            if val > 1_000_000_000: # UTC Epoch
                return max(0.0, val - time.time())
            return max(0.0, val)
        except (ValueError, TypeError):
            return 0.0

    def _is_retryable_failover_status(self, status: Any) -> bool:
        parsed = self._to_int(status, default=0) or 0
        return parsed in _FAILOVER_HTTP_STATUS_CODES

    def _is_retryable_fill_result(self, result: Dict[str, Any]) -> bool:
        if not isinstance(result, dict):
            return False
        if bool(result.get("retryable_failover")):
            return True
        status = self._to_int(result.get("http_status"), default=0) or 0
        if self._is_retryable_failover_status(status):
            return True
        err = str(result.get("error") or "").upper()
        return any(f"HTTP {code}" in err for code in _FAILOVER_HTTP_STATUS_CODES)

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
                await self._handle_auth_failure(status, url)

            retry_after_raw = resp.headers.get("Retry-After")
            retry_after_sec = self._parse_retry_after(retry_after_raw)

            if payload is None:
                text = await resp.text()
                if status == 429 and retry_after_sec > 0:
                    text = f"{text} (Retry-After: {retry_after_sec:g}s)".strip()
                return status, None, text
            
            if status == 429:
                if isinstance(payload, dict):
                    payload = dict(payload)
                    message = self._extract_error_message(payload)
                    if retry_after_sec > 0:
                        payload["message"] = f"{message} (Retry-After: {retry_after_sec:g}s)".strip()
                    payload["retry_after_sec"] = retry_after_sec
            return status, payload, ""

    async def _post_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        json_payload: Dict[str, Any],
    ) -> Tuple[int, Any, str]:
        """POST wrapper with auth-expiry detection."""
        async with session.post(url, json=json_payload) as resp:
            status = resp.status
            try:
                payload = await resp.json(content_type=None)
            except Exception:
                payload = None

            if status in (401, 403):
                await self._handle_auth_failure(status, url)

            if payload is None:
                return status, None, await resp.text()
            return status, payload, ""

    async def _handle_auth_failure(self, status: int, url: str):
        """Standardized auth failure handling and callback trigger."""
        self._auth_expired_at = time.monotonic()
        logger.critical("Auth token expired: HTTP %d from %s", status, url)
        
        # Phase 2: Invalidate cache
        if self.cache and self.signer:
            await self.cache.invalidate(self.signer.address, reason=f"auth_failure_{status}")

        if self._auth_expired_callback:
            try:
                result = self._auth_expired_callback()
                if asyncio.iscoroutine(result):
                    asyncio.get_running_loop().create_task(result)
            except Exception as e:
                logger.error("Failed to trigger auth-expired callback: %s", e)

    async def _reactive_auth_refresh_once(self, reason: str, timeout_sec: float = 8.0) -> bool:
        """Single-flight reactive refresh for runtime 401/403 recovery."""
        if not self._auth_expired_callback:
            logger.error("Reactive auth refresh skipped (%s): callback unavailable", reason)
            return False

        async with self._reactive_auth_lock:
            if not self.is_auth_expired() and str(self.auth_token or "").strip():
                logger.info("Reactive auth refresh fast-path skip: %s", reason)
                return True
            before_token = str(self.auth_token or "")
            logger.warning("Reactive auth refresh triggered: %s", reason)
            try:
                result = self._auth_expired_callback()
                if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                    await result
            except Exception as e:
                logger.error("Reactive auth refresh callback failed (%s): %s", reason, e)
                return False

            # Callback may schedule work asynchronously; wait for token/state change.
            deadline = time.monotonic() + timeout_sec
            while time.monotonic() < deadline:
                token_now = str(self.auth_token or "")
                if token_now and token_now != before_token:
                    self.clear_auth_expired()
                    return True
                if not self.is_auth_expired():
                    return True
                await asyncio.sleep(0.2)

            return not self.is_auth_expired()

    def _normalize_book_levels(self, raw_levels: Any) -> List[Dict[str, str]]:
        levels: List[Dict[str, str]] = []
        if not isinstance(raw_levels, list):
            return levels
        for entry in raw_levels:
            price: Optional[float] = None
            size: Optional[float] = None
            if isinstance(entry, dict):
                price = self._safe_float(
                    entry.get("price")
                    or entry.get("px")
                    or entry.get("p")
                )
                size = self._safe_float(
                    entry.get("size")
                    or entry.get("qty")
                    or entry.get("amount")
                    or entry.get("q")
                )
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                price = self._safe_float(entry[0])
                size = self._safe_float(entry[1])
            if price is None or size is None or price <= 0 or size <= 0:
                continue
            levels.append({"price": str(price), "size": str(size)})
        return levels

    def _extract_snapshot_from_orderbooks_payload(
        self,
        payload: Any,
        market_id: int,
    ) -> Optional[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        if isinstance(payload, dict):
            if isinstance(payload.get("order_books"), list):
                records = [x for x in payload.get("order_books", []) if isinstance(x, dict)]
            elif any(k in payload for k in ("bids", "asks")):
                records = [payload]
        elif isinstance(payload, list):
            records = [x for x in payload if isinstance(x, dict)]

        target_record: Optional[Dict[str, Any]] = None
        for rec in records:
            mid = self._to_int(rec.get("market_id") or rec.get("marketId"), default=market_id)
            if mid == market_id:
                target_record = rec
                break

        if target_record is None:
            return None

        bids = self._normalize_book_levels(
            target_record.get("bids")
            or target_record.get("bid_levels")
            or target_record.get("bidLevels")
        )
        asks = self._normalize_book_levels(
            target_record.get("asks")
            or target_record.get("ask_levels")
            or target_record.get("askLevels")
        )
        if not bids or not asks:
            return None

        nonce = self._to_int(
            target_record.get("nonce")
            or target_record.get("sequence")
            or target_record.get("seq"),
            default=0,
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return {
            "timestamp": now_ms,
            "order_book": {
                "begin_nonce": 0,
                "nonce": int(nonce or 0),
                "bids": bids,
                "asks": asks,
            },
        }

    async def get_order_book_snapshot_checked(
        self,
        market_id: Optional[int] = None,
        timeout_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Fetch a normalized order-book snapshot from REST with status metadata."""
        target_market = int(market_id or self.market_id)
        effective_timeout = aiohttp.ClientTimeout(total=timeout_sec) if timeout_sec else self.timeout
        last_error: Optional[str] = None
        last_http_status: Optional[int] = None

        for api_url in self.api_urls:
            try:
                async with aiohttp.ClientSession(timeout=effective_timeout, headers=get_standard_headers()) as session:
                    # Primary REST depth endpoint (known to be provider-gated in some environments).
                    url = f"{api_url}/orderbook"
                    async with session.get(url, params={"market_id": target_market}) as resp:
                        last_http_status = int(resp.status)
                        try:
                            payload = await resp.json(content_type=None)
                        except Exception:
                            payload = None

                        if resp.status == 200:
                            snapshot = self._extract_snapshot_from_orderbooks_payload(payload, target_market)
                            if snapshot:
                                return {
                                    "status": "success",
                                    "snapshot": snapshot,
                                    "http_status": resp.status,
                                    "source": "orderbook",
                                }
                            last_error = "orderbook payload missing bids/asks"
                        else:
                            retry_after_raw = resp.headers.get("Retry-After")
                            retry_after_sec = self._parse_retry_after(retry_after_raw)
                            last_error = self._extract_error_message(payload) or await resp.text()
                            if resp.status == 429:
                                return {
                                    "status": "error",
                                    "snapshot": None,
                                    "http_status": resp.status,
                                    "error": last_error,
                                    "retry_after_sec": retry_after_sec,
                                }

                    # Compatibility fallback: /orderBooks (some deployments expose only metadata here).
                    books_url = f"{api_url}/orderBooks"
                    books_status, books_payload, books_err = await self._get_json(
                        session,
                        books_url,
                        params={"market_id": target_market},
                    )
                    last_http_status = int(books_status)
                    if books_status == 200:
                        snapshot = self._extract_snapshot_from_orderbooks_payload(books_payload, target_market)
                        if snapshot:
                            return {
                                "status": "success",
                                "snapshot": snapshot,
                                "http_status": books_status,
                                "source": "orderBooks",
                            }
                        last_error = "orderBooks payload missing bids/asks"
                    elif books_status == 429:
                        return {
                            "status": "error",
                            "snapshot": None,
                            "http_status": books_status,
                            "error": self._extract_error_message(books_payload) or books_err,
                            "retry_after_sec": float(books_payload.get("retry_after_sec", 0.0))
                            if isinstance(books_payload, dict)
                            else 0.0,
                        }
                    else:
                        last_error = self._extract_error_message(books_payload) or books_err

                    # Final degraded fallback: synthesize a one-level snapshot from recent trades.
                    recent_url = f"{api_url}/recentTrades"
                    recent_status, recent_payload, recent_err = await self._get_json(
                        session,
                        recent_url,
                        params={"market_id": target_market, "limit": 1},
                    )
                    last_http_status = int(recent_status)
                    if recent_status == 200:
                        synthetic = self._snapshot_from_recent_trades_payload(recent_payload, target_market)
                        if synthetic:
                            return {
                                "status": "success",
                                "snapshot": synthetic,
                                "http_status": recent_status,
                                "source": "recentTrades_synthetic",
                            }
                        last_error = "recentTrades payload missing price/size"
                    elif recent_status == 429:
                        return {
                            "status": "error",
                            "snapshot": None,
                            "http_status": recent_status,
                            "error": self._extract_error_message(recent_payload) or recent_err,
                            "retry_after_sec": float(recent_payload.get("retry_after_sec", 0.0))
                            if isinstance(recent_payload, dict)
                            else 0.0,
                        }
                    else:
                        last_error = self._extract_error_message(recent_payload) or recent_err
            except _FAILOVER_ERRORS as exc:
                last_error = str(exc)
                continue
            except Exception as exc:
                last_error = str(exc)
                break

        return {
            "status": "error",
            "snapshot": None,
            "http_status": last_http_status,
            "error": last_error or "orderbook snapshot unavailable",
        }

    async def get_order_book_snapshot(
        self,
        market_id: Optional[int] = None,
        timeout_sec: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Backward-compatible snapshot fetcher used by daemon sequence-gap recovery."""
        result = await self.get_order_book_snapshot_checked(market_id=market_id, timeout_sec=timeout_sec)
        return result.get("snapshot")

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

        fee_value = trade.get(fee_key) if trade.get(fee_key) is not None else trade.get("fee")
        return {
            "timestamp": trade.get("timestamp") or trade.get("transaction_time"),
            "symbol": symbol,
            "side": side,
            "size": trade.get("size") or trade.get("qty") or trade.get("base_amount"),
            "qty": trade.get("size") or trade.get("qty") or trade.get("base_amount"),
            "price": trade.get("price") or trade.get("execution_price"),
            "fee_usd": self._normalize_trade_fee_usd(trade, fee_value),
            "tx_hash": str(tx_hash) if tx_hash is not None else "",
            "trade_id": trade.get("trade_id"),
            "role": role,
            "market_id": trade.get("market_id", self.market_id),
        }

    def _normalize_trade_fee_usd(self, trade: Dict[str, Any], fee_value: Any) -> float:
        fee = self._to_float(fee_value, default=0.0) or 0.0
        if fee <= 0.0:
            return 0.0

        # Live Lighter trades expose maker/taker fees as integer ppm fee rates
        # (for example taker_fee=280 means 280 / 1_000_000 of usd_amount).
        if isinstance(fee_value, int):
            usd_amount = self._to_float(trade.get("usd_amount"), default=0.0) or 0.0
            if usd_amount > 0.0:
                return usd_amount * fee / 1_000_000.0
        return fee

    def _extract_error_message(self, payload: Any) -> str:
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("error")
            if message:
                return str(message)
        if isinstance(payload, str):
            return payload
        return ""

    def _to_float(self, value: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

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

    def _normalize_account_tier(self, value: Any) -> str:
        normalized = str(value or "STANDARD").strip().upper()
        if normalized in {"0", "STANDARD"}:
            return "STANDARD"
        if normalized in {"1", "PREMIUM"}:
            return "PREMIUM"
        logger.warning("Unknown account_tier=%r, defaulting to STANDARD", value)
        return "STANDARD"

    def _preferred_account_type_id(self) -> int:
        # Lighter account_type mapping: 0=STANDARD, 1=PREMIUM.
        return 1 if self.account_tier == "PREMIUM" else 0

    def _lookup_account_type(self, sub_accounts: List[Dict[str, Any]], account_index: int) -> Optional[int]:
        for account in sub_accounts:
            if not isinstance(account, dict):
                continue
            if self._to_int(account.get("index")) == int(account_index):
                return self._to_int(account.get("account_type"))
        return None

    def _set_account_tier_resolution(self, account_index: int, account_type: Optional[int]) -> None:
        self._resolved_account_type = self._to_int(account_type)
        if self._resolved_account_type is None:
            self._account_tier_mismatch = False
            return
        preferred = self._preferred_account_type_id()
        self._account_tier_mismatch = self._resolved_account_type != preferred
        if self._account_tier_mismatch:
            logger.warning(
                "ACCOUNT_TIER_MISMATCH configured=%s resolved_type=%s account_index=%s",
                self.account_tier,
                self._resolved_account_type,
                account_index,
            )

    def _account_tier_mismatch_error(self, context: str) -> Dict[str, Any]:
        return {
            "status": "error",
            "error_code": "ACCOUNT_TIER_MISMATCH",
            "error": (
                f"account_tier_mismatch: configured={self.account_tier} "
                f"resolved_type={self._resolved_account_type} context={context}"
            ),
        }

    async def get_balance(self) -> dict:
        """Fetch account balance via REST.

        Supports API failover (5.11): tries each URL in api_urls on transient errors.
        """
        if not self.signer:
            return {}

        last_error: Optional[Exception] = None
        for api_url in self.api_urls:
            balance_urls = await self._build_balance_urls(api_url)
            for url in balance_urls:
                try:
                    async with aiohttp.ClientSession(timeout=self.timeout, headers=get_standard_headers()) as session:
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

    async def _build_balance_urls(self, api_url: str) -> List[str]:
        """Build candidate balance URLs (account-index first, address fallback)."""
        base = str(api_url).rstrip("/")
        urls: List[str] = []

        account_index = self._to_int(self._resolved_account_index)
        if account_index is None:
            account_index, _ = await self._resolve_runtime_account_index()

        if account_index is not None:
            urls.append(f"{base}/account/{int(account_index)}/balances")

        if self.signer and getattr(self.signer, "address", None):
            urls.append(f"{base}/account/{self.signer.address}/balances")

        # Preserve order and remove duplicates.
        seen = set()
        deduped: List[str] = []
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
        return deduped

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

    def _extract_account_metadata_entry(
        self,
        payload: Any,
        account_index: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        sub_accounts = payload.get("sub_accounts")
        if not isinstance(sub_accounts, list):
            return None

        target_index = self._to_int(account_index, default=self._to_int(self._resolved_account_index))
        if target_index is not None:
            for account in sub_accounts:
                if not isinstance(account, dict):
                    continue
                if self._to_int(account.get("index")) == int(target_index):
                    return account

        for account in sub_accounts:
            if isinstance(account, dict):
                return account
        return None

    def _balance_fallback_from_account_metadata(
        self,
        payload: Any,
        account_index: Optional[int],
    ) -> dict:
        account = self._extract_account_metadata_entry(payload, account_index)
        if not isinstance(account, dict):
            return {}

        available = self._safe_float(account.get("available_balance"))
        collateral = self._safe_float(account.get("collateral"))
        quote_balance = available if available > 0.0 else collateral
        if quote_balance <= 0.0:
            return {}
        return {_QUOTE_BALANCE_ASSET: quote_balance}

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

        # Phase 2: Check cache first (with 10m sentinel window)
        if self.cache:
            cached = await self.cache.get_account_info(self.signer.address, sentinel_window_sec=600)
            if cached and "balances" in cached:
                logger.debug("Returning authoritative balances from cache.")
                return (True, cached["balances"])

        effective_timeout = aiohttp.ClientTimeout(total=timeout_sec) if timeout_sec else self.timeout

        for api_url in self.api_urls:
            balance_urls = await self._build_balance_urls(api_url)
            if not balance_urls:
                continue

            params = {}
            if self.auth_token:
                params["auth"] = self.auth_token

            for url in balance_urls:
                try:
                    async with aiohttp.ClientSession(timeout=effective_timeout, headers=get_standard_headers()) as session:
                        async with session.get(url, params=params) as resp:
                            if resp.status == 200:
                                raw = await resp.json(content_type=None)
                                normalized = self._normalize_balance_payload(raw)

                                # Phase 2: Persist to cache
                                if self.cache:
                                    # We merge with existing index if available
                                    existing = await self.cache.get_account_info(self.signer.address) or {}
                                    existing["balances"] = normalized
                                    await self.cache.set_account_info(self.signer.address, existing)

                                return (True, normalized)
                            else:
                                # Phase 2: Invalidate on 429
                                if resp.status == 429 and self.cache:
                                    await self.cache.handle_transport_error(self.signer.address, resp.status)

                                # Auth expiry detection (Day 3)
                                if resp.status in (401, 403):
                                    await self._handle_auth_failure(resp.status, url)
                                logger.warning(f"get_balance_checked HTTP {resp.status} from {url}")
                except _FAILOVER_ERRORS as e:
                    logger.warning(f"get_balance_checked failover: {url} ({type(e).__name__}: {e})")
                    continue
                except Exception as e:
                    logger.error(f"get_balance_checked error: {e}")
                    return (False, {})

            # Fallback: some upstream environments now block /balances but still expose
            # collateral/account metadata via accountsByL1Address.
            try:
                async with aiohttp.ClientSession(timeout=effective_timeout, headers=get_standard_headers()) as session:
                    account_index, idx_err = await self.resolve_account_index(session, api_url=api_url)
                    if idx_err or account_index is None or not self.signer:
                        continue
                    status, payload, _ = await self._get_json(
                        session,
                        f"{str(api_url).rstrip('/')}/accountsByL1Address",
                        params={"l1_address": self.signer.address},
                    )
                    if status != 200:
                        continue
                    fallback_balances = self._balance_fallback_from_account_metadata(payload, account_index)
                    if fallback_balances:
                        if self.cache:
                            existing = await self.cache.get_account_info(self.signer.address) or {}
                            existing["balances"] = fallback_balances
                            await self.cache.set_account_info(self.signer.address, existing)
                        logger.info(
                            "get_balance_checked fallback: using accountsByL1Address collateral metadata for %s",
                            account_index,
                        )
                        return (True, fallback_balances)
            except Exception as e:
                logger.warning("get_balance_checked metadata fallback failed: %s", e)

        return (False, {})

    async def fetch_account_positions(self, timeout_sec: Optional[float] = None) -> dict:
        """Fetch current account positions from the public explorer API."""
        if self._is_shadow_mode():
            return {"status": "success", "positions": {}, "source": "shadow"}

        account_index, idx_err = await self._resolve_runtime_account_index()
        if idx_err or account_index is None:
            return {"status": "error", "error": f"account_index_unavailable: {idx_err}"}

        effective_timeout = aiohttp.ClientTimeout(total=timeout_sec) if timeout_sec else self.timeout
        url = f"{self._explorer_root_url().rstrip('/')}/accounts/{int(account_index)}/positions"
        try:
            async with aiohttp.ClientSession(timeout=effective_timeout, headers=get_standard_headers()) as session:
                status, payload, err_text = await self._get_json(session, url)
        except Exception as e:
            return {"status": "error", "error": str(e)}

        if status != 200 or not isinstance(payload, dict):
            return {"status": "error", "error": err_text or f"HTTP {status}"}

        positions = payload.get("positions")
        if not isinstance(positions, dict):
            return {"status": "error", "error": "positions payload missing"}

        return {
            "status": "success",
            "positions": positions,
            "source": "explorer_positions",
            "account_index": int(account_index),
        }

    async def fetch_order_history(
        self,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
        timeout_sec: Optional[float] = None,
    ) -> dict:
        """Fetch inactive order history for this account."""
        if self._is_shadow_mode():
            return {"status": "success", "orders": [], "source": "shadow"}
        if not self.signer:
            return {"status": "error", "error": "No signer available for order history."}

        effective_timeout = aiohttp.ClientTimeout(total=timeout_sec) if timeout_sec else self.timeout
        for api_url in self.api_urls:
            try:
                async with aiohttp.ClientSession(timeout=effective_timeout, headers=get_standard_headers()) as session:
                    account_index, idx_err = await self.resolve_account_index(session, api_url=api_url)
                    if idx_err or account_index is None:
                        return {"status": "error", "error": idx_err or "account_index_unavailable"}
                    params: Dict[str, Any] = {"account_index": int(account_index), "limit": int(limit)}
                    if cursor:
                        params["cursor"] = cursor
                    token = str(self.auth_token or "").strip()
                    if token:
                        params["auth"] = token
                    status, payload, err_text = await self._get_json(
                        session,
                        f"{str(api_url).rstrip('/')}/accountInactiveOrders",
                        params=params,
                    )
                    if status == 200 and isinstance(payload, dict):
                        orders = payload.get("orders")
                        if isinstance(orders, list):
                            return {
                                "status": "success",
                                "orders": orders,
                                "next_cursor": payload.get("next_cursor"),
                                "source": "accountInactiveOrders",
                                "account_index": int(account_index),
                            }
                    if status in (401, 403):
                        await self._handle_auth_failure(status, f"{str(api_url).rstrip('/')}/accountInactiveOrders")
            except _FAILOVER_ERRORS:
                continue
            except Exception as e:
                return {"status": "error", "error": str(e)}
        return {"status": "error", "error": "order_history_unavailable"}

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
                    account_index, idx_err = await self.resolve_account_index(session)
                    if idx_err:
                        return {"status": "error", "error": f"Account resolution failed: {idx_err}"}
            except Exception as e:
                return {"status": "error", "error": f"Account resolution error: {e}"}

        params = {}
        if self.auth_token:
            params["auth"] = self.auth_token

        candidate_urls: List[str] = []
        for base_url in self.api_urls or [self.api_url]:
            clean = str(base_url or "").strip().rstrip("/")
            if clean and clean not in candidate_urls:
                candidate_urls.append(clean)
        if not candidate_urls:
            candidate_urls.append(str(self.api_url or "").strip().rstrip("/"))

        best_result: Optional[dict] = None
        last_error: Optional[str] = None
        try:
            async with aiohttp.ClientSession(headers=get_standard_headers()) as session:
                for base_url in candidate_urls:
                    url = f"{base_url}/account/{account_index}/nonce"
                    try:
                        async with session.get(url, params=params) as resp:
                            if resp.status == 200:
                                data = await resp.json(content_type=None)
                                nonce_val = data if isinstance(data, int) else data.get("nonce", 0) if isinstance(data, dict) else 0
                                nonce_result = {
                                    "status": "success",
                                    "nonce": int(nonce_val),
                                    "source": "nonce_endpoint",
                                    "url": base_url,
                                }
                                if best_result is None or int(nonce_result["nonce"]) > int(best_result["nonce"]):
                                    best_result = nonce_result
                                continue

                            if resp.status in (401, 403):
                                await self._handle_auth_failure(resp.status, url)
                            if resp.status == 403:
                                fallback_nonce = await self._fallback_nonce_from_account_metadata(
                                    session=session,
                                    account_index=int(account_index),
                                )
                                if fallback_nonce is not None:
                                    logger.warning(
                                        "Nonce endpoint forbidden (403); using accountsByL1Address fallback nonce=%s",
                                        fallback_nonce,
                                    )
                                    nonce_result = {
                                        "status": "success",
                                        "nonce": int(fallback_nonce),
                                        "source": "accountsByL1Address_fallback",
                                        "url": base_url,
                                    }
                                    if best_result is None or int(nonce_result["nonce"]) > int(best_result["nonce"]):
                                        best_result = nonce_result
                                    continue

                            err_text = await resp.text()
                            last_error = f"HTTP {resp.status}: {err_text}"
                    except Exception as exc:
                        last_error = str(exc)
                        logger.warning("Nonce fetch error via %s: %s", base_url, exc)

                if best_result is not None:
                    return best_result
                return {"status": "error", "error": last_error or "nonce fetch failed"}
        except Exception as e:
            logger.error(f"Nonce fetch error: {e}")
            return {"status": "error", "error": str(e)}

    async def _fallback_nonce_from_account_metadata(
        self,
        session: aiohttp.ClientSession,
        account_index: int,
    ) -> Optional[int]:
        """Best-effort nonce fallback from account metadata when nonce endpoint is blocked.

        Some upstream environments return 403 on /account/{index}/nonce while
        still allowing accountsByL1Address and authenticated trading routes.
        """
        if not self.signer or not getattr(self.signer, "address", None):
            return None

        url = f"{self.api_url}/accountsByL1Address"
        status, payload, _ = await self._get_json(
            session,
            url,
            params={"l1_address": self.signer.address},
        )
        if status != 200 or not isinstance(payload, dict):
            return None

        sub_accounts = payload.get("sub_accounts")
        if not isinstance(sub_accounts, list):
            return None

        chosen = None
        for account in sub_accounts:
            if not isinstance(account, dict):
                continue
            if self._to_int(account.get("index")) == int(account_index):
                chosen = account
                break
        if not isinstance(chosen, dict):
            return None

        for key in ("nonce", "next_nonce", "order_nonce", "total_order_count"):
            value = self._to_int(chosen.get(key))
            if value is not None and value >= 0:
                return int(value)
        return None

    async def preflight_live_readiness(self) -> Dict[str, Any]:
        """Validate auth token and infer whether account permissions allow trading."""
        def _finalize(payload: Dict[str, Any]) -> Dict[str, Any]:
            self._last_preflight_readiness = dict(payload)
            return payload

        if self._is_shadow_mode():
            return _finalize({"status": "success", "auth_ok": True, "can_trade": True, "detail": "shadow_mode"})
        if not self.signer:
            return _finalize({"status": "error", "auth_ok": False, "can_trade": False, "detail": "missing signer"})
        if not str(self.auth_token or "").strip():
            return _finalize({"status": "error", "auth_ok": False, "can_trade": False, "detail": "missing auth token"})

        try:
            async with aiohttp.ClientSession(timeout=self.timeout, headers=get_standard_headers()) as session:
                account_index, idx_err = await self.resolve_account_index(session)
                if idx_err:
                    return _finalize({"status": "error", "auth_ok": False, "can_trade": False, "detail": idx_err})

                if self.enforce_account_tier_match and self._account_tier_mismatch:
                    detail = (
                        "ACCOUNT_TIER_MISMATCH "
                        f"configured={self.account_tier} "
                        f"resolved_type={self._resolved_account_type} "
                        f"account_index={int(account_index)}"
                    )
                    logger.error("LIVE preflight tier mismatch: %s", detail)
                    return _finalize(
                        {
                            "status": "error",
                            "auth_ok": True,
                            "can_trade": False,
                            "detail": detail,
                            "error_code": "ACCOUNT_TIER_MISMATCH",
                            "account_index": int(account_index),
                            "configured_account_tier": self.account_tier,
                            "resolved_account_type": self._resolved_account_type,
                        }
                    )

                trades = await self._fetch_account_trades(
                    session=session,
                    account_index=account_index,
                    cursor=None,
                    limit=1,
                )
                if trades.get("status") != "success":
                    return _finalize(
                        {
                            "status": "error",
                            "auth_ok": False,
                            "can_trade": False,
                            "detail": trades.get("error", "trades auth check failed"),
                        }
                    )

                permission = await self._infer_account_trade_permission(
                    session=session,
                    account_index=int(account_index),
                )
                can_trade = bool(permission.get("can_trade", True))
                detail = str(permission.get("detail", "ok"))
                return _finalize(
                    {
                        "status": "success" if can_trade else "error",
                        "auth_ok": True,
                        "can_trade": can_trade,
                        "detail": detail,
                        "account_index": int(account_index),
                    }
                )
        except Exception as e:
            detail = str(e).strip() or f"{type(e).__name__}"
            return _finalize({"status": "error", "auth_ok": False, "can_trade": False, "detail": detail})

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
            async with aiohttp.ClientSession(timeout=self.timeout, headers=get_standard_headers()) as session:
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
            async with aiohttp.ClientSession(timeout=self.timeout, headers=get_standard_headers()) as session:
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
        account_idx = self._resolved_account_index

        def _build_params() -> Dict[str, Any]:
            params: Dict[str, Any] = {"market_id": self.market_id}
            if account_idx is not None:
                params["account_index"] = account_idx
            token = str(self.auth_token or "").strip()
            if token:
                params["auth"] = token
            return params

        status, payload, err_text = await self._get_json(session, url, params=_build_params())
        if status in (401, 403):
            refreshed = await self._reactive_auth_refresh_once("fetch_open_orders_401_403")
            if refreshed:
                status, payload, err_text = await self._get_json(session, url, params=_build_params())
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

    def _snapshot_from_recent_trades_payload(
        self,
        payload: Any,
        market_id: int,
    ) -> Optional[Dict[str, Any]]:
        trades = self._extract_trades(payload)
        if not trades:
            return None

        trade = trades[0]
        price = self._safe_float(trade.get("price") or trade.get("execution_price"))
        if price <= 0:
            return None

        size = self._safe_float(trade.get("size") or trade.get("qty") or trade.get("base_amount"))
        if size <= 0:
            size = 0.0001

        spread_bps = max(float(self.degraded_snapshot_spread_bps), 0.1)
        half_spread = max(price * (spread_bps / 10000.0) * 0.5, 0.0001)
        bid = max(price - half_spread, 0.0001)
        ask = max(price + half_spread, bid + 0.0001)
        nonce = self._to_int(trade.get("trade_id") or trade.get("id"), default=0) or 0
        ts_default = int(datetime.now(timezone.utc).timestamp() * 1000)
        ts_ms = self._to_int(trade.get("timestamp") or trade.get("transaction_time"), default=ts_default) or ts_default

        return {
            "timestamp": int(ts_ms),
            "order_book": {
                "begin_nonce": 0,
                "nonce": int(nonce),
                "bids": [{"price": str(bid), "size": str(size)}],
                "asks": [{"price": str(ask), "size": str(size)}],
            },
            "synthetic": True,
            "source": "recentTrades",
            "market_id": int(market_id),
        }

    async def _cancel_order(self, session: aiohttp.ClientSession, order_id: Any, nonce: int) -> dict:
        """Cancel a single order via POST /sendTx."""
        url = f"{self.api_url}/sendTx"
        payload = {
            "type": "cancel_order",
            "orderId": str(order_id),
            "nonce": nonce,
        }

        if self.signer:
            try:
                # V2: Even cancellations currently reuse the Order struct with dummy values
                # or require specific CancelOrder struct. Given the existing code reuses
                # sign_order, we provide the V2 fields to avoid 400 Bad Request.
                payload["signature"] = self.signer.sign_order({
                    "subAccountIndex": int(self._resolved_account_index or 0),
                    "marketId": self.market_id,
                    "side": 0,
                    "price": 0,
                    "size": 0,
                    "orderType": 0,
                    "nonce": nonce,
                    "orderExpiry": 0,
                })
            except Exception as e:
                logger.warning(f"Failed to sign cancel for order {order_id}: {e}")

        try:
            status, payload, err_text = await self._post_json(session, url, json_payload=payload)
            if status == 200:
                return {"status": "success"}
            return {"status": "error", "error": f"HTTP {status}: {err_text}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
