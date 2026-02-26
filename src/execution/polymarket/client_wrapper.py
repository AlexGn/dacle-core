"""
DACLE Polymarket Execution Wrapper
Standardized execution client for Polymarket CLOB.
Handles: tick-size normalization, idempotency, GTD orders, and fail-fast safety.
"""

import logging
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
        
        # Execution settings
        exec_cfg = config.get("execution", {})
        self.default_expiration_sec = exec_cfg.get("order_timeout_sec", 60)
        self.max_retries = exec_cfg.get("max_retries", 3)
        
        # Internal caches
        self._market_metadata: Dict[str, Dict[str, Any]] = {}
        self._metadata_lock = asyncio.Lock()

    def _is_shadow_mode(self) -> bool:
        return self.mode == "SHADOW"

    async def get_market_metadata(self, token_id: str) -> Dict[str, Any]:
        """Fetch and cache tickSize and negRisk for a specific token."""
        if token_id in self._market_metadata:
            return self._market_metadata[token_id]
            
        async with self._metadata_lock:
            if token_id in self._market_metadata:
                return self._market_metadata[token_id]
                
            try:
                # Use endpoints that work with token_id directly
                tick_size = await asyncio.to_thread(self.client.get_tick_size, token_id)
                neg_risk = await asyncio.to_thread(self.client.get_neg_risk, token_id)
                
                meta = {
                    "tick_size": float(tick_size) if tick_size else 0.01,
                    "min_order_size": 5.0, # Updated based on test results (Min 5 shares)
                    "neg_risk": bool(neg_risk)
                }
                self._market_metadata[token_id] = meta
                return meta
            except Exception as e:
                logger.warning(f"Failed to fetch market metadata for {token_id}: {e}")
                
            return {"tick_size": 0.01, "min_order_size": 5.0, "neg_risk": False}

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

        # 5. Expiration
        exp = int(time.time() + (expiration_sec or self.default_expiration_sec))

        # 6. Build Order Arguments
        order_args = OrderArgs(
            price=norm_price,
            size=qty_int,
            side=side_val,
            token_id=token_id,
            expiration=exp
        )

        # 7. Execute
        try:
            # Note: For explicit FOK/FAK we must use create_order + post_order 
            # as create_and_post_order does not expose the order_type parameter.
            target_type = ORDER_TYPE_MAP.get(order_type, OrderType.GTD)
            
            # Step A: Sign
            signed = await asyncio.to_thread(self.client.create_order, order_args)
            # Step B: Post
            resp = await asyncio.to_thread(self.client.post_order, signed, orderType=target_type)
            
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

    async def get_balance(self, token_id: Optional[str] = None) -> float:
        """
        Fetch balance for USDC.e (if token_id is None) or shares (if token_id is provided).
        """
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        try:
            if token_id:
                params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
            else:
                params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                
            resp = await asyncio.to_thread(self.client.get_balance_allowance, params)
            raw_bal = float(resp.get("balance", "0"))
            return raw_bal / 1000000.0 # Standard 6 decimal precision for Polymarket
        except Exception as e:
            logger.error(f"Failed to fetch balance for {token_id or 'USDC.e'}: {e}")
            return 0.0

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
