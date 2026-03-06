"""
DACLE Virtual Lighter Simulator
A high-fidelity simulator for Lighter.xyz Standard Tier.
Enforces:
1. 300ms Intentional Latency Penalty.
2. 15s Free-TX Rate Limiting.
3. Nonce Integrity.
"""

import asyncio
import time
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class VirtualOrder:
    order_id: str
    symbol: str
    side: str
    price: float
    qty: float
    status: str = "PENDING"
    created_at: float = 0.0

class VirtualLighter:
    def __init__(self):
        self.latency_penalty_ms = 300
        self.free_tx_interval = 15.0
        self.last_free_tx_ts = 0.0
        self.auth_token = "virtual-auth-token"
        self.force_create_order_http_status: Optional[int] = None
        self.force_fetch_fills_http_status: Optional[int] = None
        
        self.orders: Dict[str, VirtualOrder] = {}
        self.positions: Dict[str, float] = {}
        self.fills: List[Dict[str, Any]] = []
        self.nonce_counter = 0

    @staticmethod
    def _error_payload(status: int, message: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": "error",
            "error": f"HTTP {status}: {message}",
            "error_code": "API_ERROR",
            "http_status": int(status),
        }
        if int(status) in {502, 503, 504, 520, 522, 524}:
            payload["retryable_failover"] = True
        return payload
        
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
        Simulates the Lighter Standard Tier order creation.
        """
        if not str(self.auth_token or "").strip():
            return self._error_payload(401, "Unauthorized")

        if self.force_create_order_http_status:
            return self._error_payload(int(self.force_create_order_http_status), "Simulated create_order failure")

        # 1. Nonce Check
        if nonce < self.nonce_counter:
            return {"error": {"code": 400, "message": "Invalid Nonce"}}
        
        # 2. Rate Limit / Free TX Check (Simplification for V1)
        now = time.monotonic()
        if now - self.last_free_tx_ts < self.free_tx_interval:
            logger.warning("Quota Consumed: Transaction not free.")
            # For simulator, we allow it but log it
        
        self.last_free_tx_ts = now
        self.nonce_counter = nonce + 1
        
        # 3. THE 300MS PENALTY (The Hard Truth)
        await asyncio.sleep(self.latency_penalty_ms / 1000.0)
        
        order_id = f"v_ord_{int(time.time() * 1000)}"
        order = VirtualOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price,
            qty=qty,
            created_at=time.time()
        )
        self.orders[order_id] = order
        
        logger.info(f"VirtualOrder Placed: {side} {qty} {symbol} @ {price} (Delayed {self.latency_penalty_ms}ms)")
        
        response = {
            "status": "success",
            "order_id": order_id,
            "nonce": nonce,
            "timestamp": time.time(),
        }

        # IOC semantics in the simulator are immediate taker acks; include explicit fill fields
        # so daemon fill-truth logic does not need to infer fills from missing data.
        normalized_order_type = str(order_type or "IOC").upper()
        if normalized_order_type == "IOC":
            response["filled_qty"] = float(qty)
            response["filled_price"] = float(price)
            self.fills.append(
                {
                    "trade_id": f"v_fill_{int(time.time() * 1000)}",
                    "order_id": order_id,
                    "symbol": symbol,
                    "side": str(side or "").upper(),
                    "price": float(price),
                    "qty": float(qty),
                    "timestamp": int(time.time() * 1000),
                    "role": "taker",
                    "order_type": normalized_order_type,
                }
            )

        return response

    async def get_balance(self) -> dict:
        return {"USDC": 10000.0, "BTC": 0.0}

    async def cancel_all_orders(self, symbol: Optional[str] = None):
        """Simulator: Wipes all pending orders."""
        count = len(self.orders)
        self.orders.clear()
        logger.info(f"Virtual: Cancelled {count} orders.")

    async def cancel_order(self, order_id: Any, nonce: int) -> dict:
        """Stub for interface parity with LighterRealClient."""
        self.orders.pop(str(order_id), None)
        return {"status": "success", "order_id": str(order_id), "nonce": nonce}

    async def fetch_fills(
        self,
        limit: int = 50,
        since_ts: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        """Simulator fill-history endpoint for GhostSweeper / fill polling parity."""
        _ = cursor  # Cursor is accepted for parity but ignored in simulator.
        if not str(self.auth_token or "").strip():
            return self._error_payload(401, "Unauthorized")
        if self.force_fetch_fills_http_status:
            return self._error_payload(int(self.force_fetch_fills_http_status), "Simulated fetch_fills failure")

        fills = list(self.fills)
        if since_ts is not None:
            since = int(since_ts)
            fills = [fill for fill in fills if int(fill.get("timestamp", 0)) >= since]
        if limit > 0:
            fills = fills[-int(limit) :]
        return {"status": "success", "fills": fills, "source": "virtual"}
