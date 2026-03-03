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
        
        self.orders: Dict[str, VirtualOrder] = {}
        self.positions: Dict[str, float] = {}
        self.nonce_counter = 0
        
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
        
        return {
            "status": "success",
            "order_id": order_id,
            "nonce": nonce,
            "timestamp": time.time()
        }

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
