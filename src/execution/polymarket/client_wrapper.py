"""
DACLE Polymarket Execution Wrapper
Standardized execution client for Polymarket CLOB.
Handles: tick-size normalization, idempotency, GTD orders, and fail-fast safety.
"""

import logging
import os
import time
import asyncio
from typing import Any, Dict, Optional, List
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, ApiCreds, OrderType

logger = logging.getLogger(__name__)

# Polymarket Order Types
# GTC: Good Till Cancelled
# GTD: Good Till Date (Requires expiration)
# FOK: Fill or Kill
# FAK: Fill and Kill (Partial fill allowed, remainder cancelled)
ORDER_TYPE_MAP = {
    "GTC": OrderType.GTC,
    "GTD": OrderType.GTD,
    "FOK": OrderType.FOK,
    "FAK": OrderType.FAK
}
VALID_ORDER_TYPES = list(ORDER_TYPE_MAP.keys())

class PolymarketClientWrapper:
    def __init__(self, config: dict, client: ClobClient):
        self.config = config
        self.client = client
        self.mode = config.get("mode", "SHADOW").upper()

        # Env var override takes priority over config
        env_mode = os.getenv("POLY_MODE", "").upper()
        if env_mode in ("SHADOW", "PAPER", "LIVE"):
            self.mode = env_mode

        # LIVE mode requires both POLY_LIVE_ENABLED=true AND mode=LIVE
        live_enabled = os.getenv("POLY_LIVE_ENABLED", "false").lower() == "true"
        if self.mode == "LIVE" and not live_enabled:
            logger.warning("POLY_MODE=LIVE but POLY_LIVE_ENABLED is not true. Falling back to SHADOW.")
            self.mode = "SHADOW"

        # Execution settings
        exec_cfg = config.get("execution", {})
        self.default_expiration_sec = exec_cfg.get("order_timeout_sec", 60)
        self.max_retries = exec_cfg.get("max_retries", 3)

        # Internal caches with TTL
        self._market_metadata: Dict[str, Dict[str, Any]] = {}
        self._metadata_lock = asyncio.Lock()
        self._metadata_ttl_sec: float = 3600.0

        # Latency monitoring (rolling last 10)
        self._order_latencies: List[float] = []
        self._latency_lock = asyncio.Lock()

    def _is_shadow_mode(self) -> bool:
        return self.mode == "SHADOW"

    def get_p95_latency_ms(self) -> float:
        if not self._order_latencies:
            return 0.0
        sorted_lat = sorted(self._order_latencies)
        idx = max(0, int(len(sorted_lat) * 0.95) - 1)
        return sorted_lat[idx]

    async def get_market_metadata(self, token_id: str) -> Dict[str, Any]:
        """Fetch and cache tickSize and negRisk for a specific token."""
        if token_id in self._market_metadata:
            cached = self._market_metadata[token_id]
            if time.time() - cached.get("_cached_at", 0) <= self._metadata_ttl_sec:
                return cached

        async with self._metadata_lock:
            if token_id in self._market_metadata:
                cached = self._market_metadata[token_id]
                if time.time() - cached.get("_cached_at", 0) <= self._metadata_ttl_sec:
                    return cached

            try:
                # Use endpoints that work with token_id directly
                tick_size = await asyncio.to_thread(self.client.get_tick_size, token_id)
                neg_risk = await asyncio.to_thread(self.client.get_neg_risk, token_id)

                meta = {
                    "tick_size": float(tick_size) if tick_size else 0.01,
                    "min_order_size": 5.0, # Updated based on test results (Min 5 shares)
                    "neg_risk": bool(neg_risk),
                    "_cached_at": time.time()
                }
                self._market_metadata[token_id] = meta
                return meta
            except Exception as e:
                logger.warning(f"Failed to fetch market metadata for {token_id}: {e}")

            return {"tick_size": 0.01, "min_order_size": 5.0, "neg_risk": False, "_cached_at": 0}

    def normalize_price(self, price: float, tick_size: float, side: str = "BUY") -> float:
        """Round price to the nearest tick_size and enforce hard caps."""
        if tick_size <= 0:
            tick_size = 0.01

        # Round to nearest tick
        norm = round(round(price / tick_size) * tick_size, 4)

        # Hard Caps to avoid 'invalid amount' or 'crosses the book' errors
        if side.upper() == "BUY":
            # Buy price must be < 1.0
            if norm >= 0.9999: norm = 0.99
        else:
            # Sell price must be > 0.0
            if norm <= 0.0001: norm = 0.01

        return norm

    async def create_order(
        self,
        token_id: str,
        price: float,
        qty: float,
        side: str,
        order_type: str = "GTD",
        expiration_sec: Optional[int] = None,
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create and post an order to Polymarket.
        """
        side_val = side.upper()
        if side_val not in ("BUY", "SELL"):
            return {"status": "error", "error": f"Invalid side: {side}"}

        order_type = order_type.upper()
        if order_type not in VALID_ORDER_TYPES:
            return {"status": "error", "error": f"Invalid order_type: {order_type}"}

        # 1. Resolve Metadata
        meta = await self.get_market_metadata(token_id)
        norm_price = self.normalize_price(price, meta["tick_size"], side_val)

        # 2. Enforce Integer Quantities (Fix for 'max of 4 decimals' errors)
        # Polymarket shares are essentially ERC1155 tokens, integer shares are safer.
        qty_int = int(qty)
        if qty_int < meta["min_order_size"]:
            return {
                "status": "error",
                "error": f"Quantity {qty_int} below minimum {meta['min_order_size']}",
                "error_code": "MIN_SIZE_REJECT"
            }

        # 3. Idempotency Key (Client Order ID)
        if not client_order_id:
            client_order_id = f"dacle_{int(time.time() * 1000)}"

        # 4. Shadow Mode Bypass
        if self._is_shadow_mode():
            logger.info(f"[SHADOW] Post Order: {side_val} {qty_int} @ {norm_price} (token={token_id}, type={order_type})")
            return {
                "status": "success",
                "shadow": True,
                "order_id": f"shadow_{client_order_id}",
                "client_order_id": client_order_id,
                "filled_qty": 0.0,
                "filled_price": 0.0
            }

        # 4b. Paper Mode: place real order at $0.01 POST_ONLY+GTD, then cancel immediately
        if self.mode == "PAPER":
            paper_price = 0.01
            norm_paper = self.normalize_price(paper_price, meta["tick_size"], side_val)
            logger.info(f"[PAPER] POST_ONLY order: {side_val} {qty_int} @ {norm_paper} (token={token_id})")
            exp = int(time.time() + 30)  # 30s GTD
            paper_args = OrderArgs(price=norm_paper, size=qty_int, side=side_val, token_id=token_id, expiration=exp)
            try:
                signed = await asyncio.to_thread(self.client.create_order, paper_args)
                resp = await asyncio.to_thread(self.client.post_order, signed, orderType=OrderType.GTD)
                if resp and resp.get("success"):
                    paper_order_id = resp.get("orderID")
                    logger.info(f"[PAPER] Order placed: {paper_order_id}. Cancelling immediately...")
                    await asyncio.sleep(0.5)
                    await self.cancel_order(paper_order_id)
                    return {"status": "success", "paper": True, "order_id": paper_order_id, "client_order_id": client_order_id}
                else:
                    return {"status": "error", "error": resp.get("errorMsg", "Paper order failed"), "error_code": "PAPER_REJECT"}
            except Exception as e:
                return {"status": "error", "error": str(e), "error_code": "PAPER_EXCEPTION"}

        # 5. Expiration
        if order_type == "GTD":
            exp = int(time.time() + (expiration_sec or self.default_expiration_sec))
        else:
            # GTC, FOK, FAK must have expiration 0
            exp = 0

        # 6. Build Order Arguments
        order_args = OrderArgs(
            price=norm_price,
            size=qty_int,
            side=side_val,
            token_id=token_id,
            expiration=exp
        )

        # 7. Execute with latency tracking
        try:
            # Note: For explicit FOK/FAK we must use create_order + post_order
            # as create_and_post_order does not expose the order_type parameter.
            target_type = ORDER_TYPE_MAP.get(order_type, OrderType.GTD)

            t_start = time.time()
            # Step A: Sign
            signed = await asyncio.to_thread(self.client.create_order, order_args)
            # Step B: Post
            resp = await asyncio.to_thread(self.client.post_order, signed, orderType=target_type)
            latency_ms = (time.time() - t_start) * 1000

            # Track latency
            async with self._latency_lock:
                self._order_latencies.append(latency_ms)
                if len(self._order_latencies) > 10:
                    self._order_latencies.pop(0)
                p95_latency = self.get_p95_latency_ms()
                if p95_latency > 500:
                    penalty_bps = (p95_latency - 200) / 100 * 10
                    logger.warning(f"High relayer latency P95={p95_latency:.0f}ms, penalty={penalty_bps:.0f}bps")

            if resp and resp.get("success"):
                return {
                    "status": "success",
                    "order_id": resp.get("orderID"),
                    "client_order_id": client_order_id,
                    "raw": resp
                }
            else:
                error_msg = resp.get("errorMsg", "Unknown execution error")
                return {
                    "status": "error",
                    "error": error_msg,
                    "error_code": "API_REJECT"
                }
        except Exception as e:
            logger.error(f"Polymarket Execution Exception: {e}")
            return {"status": "error", "error": str(e), "error_code": "EXCEPTION"}

    async def cancel_order(self, order_id: str) -> bool:
        if self._is_shadow_mode(): return True
        try:
            resp = await asyncio.to_thread(self.client.cancel, order_id)
            return bool(resp and resp.get("success"))
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def _build_l2_headers(self, method: str, request_path: str) -> dict:
        """
        Generate Polymarket Level 2 auth headers for a REST call.

        Bypasses py_clob_client header generation to avoid a known bug where
        AssetType.__str__() returns 'AssetType.COLLATERAL' (with class prefix)
        instead of plain 'COLLATERAL' in Python 3.11+, causing the backend to
        return '400 assetAddress invalid hex address'.

        Signature: base64url( HMAC-SHA256( base64url_decode(secret), ts+method+path ) )
        """
        import hmac as _hmac
        import hashlib
        import base64

        key = os.getenv("POLY_API_KEY", "")
        secret = os.getenv("POLY_API_SECRET", "")
        passphrase = os.getenv("POLY_API_PASSPHRASE", "")
        address = os.getenv("POLY_ADDRESS", "")

        ts = str(int(time.time()))
        msg = ts + method.upper() + request_path
        secret_padded = secret + "=" * ((4 - len(secret) % 4) % 4)
        secret_bytes = base64.urlsafe_b64decode(secret_padded)
        sig = base64.urlsafe_b64encode(
            _hmac.new(secret_bytes, msg.encode(), hashlib.sha256).digest()
        ).decode()

        return {
            "POLY_ADDRESS": address,
            "POLY_API_KEY": key,
            "POLY_SIGNATURE": sig,
            "POLY_TIMESTAMP": ts,
            "POLY_PASSPHRASE": passphrase,
        }

    async def get_balance(self, token_id: Optional[str] = None) -> float:
        """
        Fetch balance for USDC.e (if token_id is None) or shares (if token_id is provided).

        Uses a direct httpx call instead of py_clob_client.get_balance_allowance()
        to avoid a Python 3.11+ enum.__str__ regression that causes the backend to
        receive '?asset_type=AssetType.COLLATERAL' and reject with 400.
        """
        import httpx

        request_path = "/balance-allowance"
        sig_type = os.getenv("POLY_SIGNATURE_TYPE", "2")
        if token_id:
            url = f"https://clob.polymarket.com{request_path}?asset_type=CONDITIONAL&token_id={token_id}&signature_type={sig_type}"
        else:
            url = f"https://clob.polymarket.com{request_path}?asset_type=COLLATERAL&signature_type={sig_type}"

        try:
            headers = self._build_l2_headers("GET", request_path)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    raw_bal = float(data.get("balance", "0"))
                    return raw_bal / 1_000_000  # USDC.e has 6 decimals
                logger.error(f"balance-allowance HTTP {resp.status_code}: {resp.text[:200]}")
                return 0.0
        except Exception as e:
            logger.error(f"Direct balance check failed for {token_id or 'USDC.e'}: {e}")
            return 0.0

    # CTF Exchange address on Polygon — the contract that needs USDC.e allowance
    CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

    async def get_usdc_balance_and_allowance(self) -> dict:
        """
        Returns both USDC.e balance and CTF Exchange allowance in a single call.
        Useful for pre-trade checks: need balance > order_cost AND allowance > order_cost.

        The CLOB API returns:
          {"balance": "12345678", "allowances": {"0x4bFb...": "99999999", ...}}
        Note: "allowances" is a dict keyed by contract address, NOT a single "allowance" field.

        Returns: {"balance_usdc": float, "allowance_usdc": float, "ok": bool}
        """
        import httpx

        sig_type = os.getenv("POLY_SIGNATURE_TYPE", "2")
        request_path = "/balance-allowance"
        url = f"https://clob.polymarket.com{request_path}?asset_type=COLLATERAL&signature_type={sig_type}"
        try:
            headers = self._build_l2_headers("GET", request_path)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    balance = float(data.get("balance", "0")) / 1_000_000
                    # allowances is a dict of {contract_address: amount_str}
                    allowances = data.get("allowances", {})
                    ctf_raw = allowances.get(self.CTF_EXCHANGE, "0")
                    allowance = float(ctf_raw) / 1_000_000
                    return {
                        "balance_usdc": balance,
                        "allowance_usdc": allowance,
                        "allowances_raw": allowances,
                        "ok": True,
                    }
                logger.error(f"get_usdc_balance_and_allowance HTTP {resp.status_code}: {resp.text[:200]}")
                return {"balance_usdc": 0.0, "allowance_usdc": 0.0, "ok": False, "error": resp.text[:200]}
        except Exception as e:
            logger.error(f"get_usdc_balance_and_allowance failed: {e}")
            return {"balance_usdc": 0.0, "allowance_usdc": 0.0, "ok": False, "error": str(e)}

    async def wait_for_balance(self, token_id: str, target_qty: float, timeout_sec: int = 60) -> bool:
        """
        Poll the balance until target_qty is reached or timeout.
        Ensures shares are settled on-chain before attempting a sell.
        """
        start_time = time.time()
        while time.time() - start_time < timeout_sec:
            bal = await self.get_balance(token_id)
            if bal >= target_qty:
                return True
            await asyncio.sleep(5)
        return False
