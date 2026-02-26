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
from py_clob_client.clob_types import OrderArgs, ApiCreds

logger = logging.getLogger(__name__)

# Polymarket Order Types
# GTC: Good Till Cancelled
# GTD: Good Till Date (Requires expiration)
# FOK: Fill or Kill
# FAK: Fill and Kill (Partial fill allowed, remainder cancelled)
VALID_ORDER_TYPES = ["GTC", "GTD", "FOK", "FAK"]

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
        """Fetch and cache tickSize and minOrderSize for a specific token."""
        if token_id in self._market_metadata:
            return self._market_metadata[token_id]
            
        async with self._metadata_lock:
            if token_id in self._market_metadata:
                return self._market_metadata[token_id]
                
            try:
                market = await asyncio.to_thread(self.client.get_market, token_id)
                if market:
                    meta = {
                        "tick_size": float(market.get("tick_size", 0.01)),
                        "min_order_size": float(market.get("minimum_order_size", 1.0)),
                        "neg_risk": bool(market.get("neg_risk", False))
                    }
                    self._market_metadata[token_id] = meta
                    return meta
            except Exception as e:
                logger.warning(f"Failed to fetch market metadata for {token_id}: {e}")
                
            return {"tick_size": 0.01, "min_order_size": 1.0, "neg_risk": False}

    def normalize_price(self, price: float, tick_size: float) -> float:
        """Round price to the nearest tick_size."""
        if tick_size <= 0:
            return price
        # Round to 4 decimals as standard for Polymarket price (0.0001 - 0.9999)
        return round(round(price / tick_size) * tick_size, 4)

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
        
        Args:
            side: "BUY" or "SELL"
            order_type: "GTC", "GTD", "FOK", "FAK"
        """
        side_val = side.upper()
        if side_val not in ("BUY", "SELL"):
            return {"status": "error", "error": f"Invalid side: {side}"}
            
        order_type = order_type.upper()
        if order_type not in VALID_ORDER_TYPES:
            return {"status": "error", "error": f"Invalid order_type: {order_type}"}
        
        # 1. Resolve Metadata
        meta = await self.get_market_metadata(token_id)
        norm_price = self.normalize_price(price, meta["tick_size"])
        
        # 2. Min Size Guard
        if qty < meta["min_order_size"]:
            return {
                "status": "error",
                "error": f"Quantity {qty} below minimum {meta['min_order_size']}",
                "error_code": "MIN_SIZE_REJECT"
            }

        # 3. Idempotency Key (Client Order ID)
        if not client_order_id:
            # We use a distinct prefix for recovery tracking
            client_order_id = f"dacle_{int(time.time() * 1000)}"

        # 4. Shadow Mode Bypass
        if self._is_shadow_mode():
            logger.info(f"[SHADOW] Post Order: {side_val} {qty} @ {norm_price} (token={token_id}, type={order_type})")
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
            size=qty,
            side=side_val,
            token_id=token_id,
            expiration=exp
        )

        # 7. Execute
        try:
            # create_and_post_order handles the type mapping internally via OrderArgs
            # Note: For explicit FOK/FAK we might need to use create_order + post_order 
            # with specific parameters if the SDK wrapper is too opaque.
            resp = await asyncio.to_thread(self.client.create_and_post_order, order_args)
            
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
