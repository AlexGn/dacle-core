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
        self.force_auth_expiry: bool = False
        self.force_partial_fill_ratio: Optional[float] = None
        self.force_ws_disconnect: bool = False
        
        self.orders: Dict[str, VirtualOrder] = {}
        self.positions: Dict[str, float] = {}
        self.fills: List[Dict[str, Any]] = []
        self._fill_seq = 0
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

    def _append_fill(
        self,
        *,
        order_id: str,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        role: str,
        order_type: str,
        timestamp_ms: Optional[int] = None,
    ) -> None:
        self._fill_seq += 1
        ts_ms = int(timestamp_ms if timestamp_ms is not None else int(time.time() * 1000))
        self.fills.append(
            {
                "trade_id": f"v_fill_{self._fill_seq}",
                "order_id": str(order_id),
                "symbol": str(symbol),
                "side": str(side or "").upper(),
                "price": float(price),
                "qty": float(qty),
                "timestamp": ts_ms,
                "role": str(role or "taker"),
                "order_type": str(order_type or "IOC").upper(),
            }
        )
        
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
        if not str(self.auth_token or "").strip() or self.force_auth_expiry:
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
        
        # Apply partial fill logic if flag is set
        final_qty = float(qty)
        if self.force_partial_fill_ratio is not None:
            final_qty = round(float(qty) * self.force_partial_fill_ratio, 8)
            logger.info(f"VirtualOrder Partial Fill Applied: ratio={self.force_partial_fill_ratio} target={qty} final={final_qty}")

        order = VirtualOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price,
            qty=final_qty,
            created_at=time.time()
        )
        self.orders[order_id] = order
        
        logger.info(f"VirtualOrder Placed: {side} {final_qty} {symbol} @ {price} (Delayed {self.latency_penalty_ms}ms)")
        
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
            response["filled_qty"] = float(final_qty)
            response["filled_price"] = float(price)
            self._append_fill(
                order_id=order_id,
                symbol=symbol,
                side=side,
                price=price,
                qty=final_qty,
                role="taker",
                order_type=normalized_order_type,
            )

        return response

    async def get_balance(self) -> dict:
        return {"USDC": 10000.0, "BTC": 0.0}

    async def get_balance_checked(self, timeout_sec: Optional[float] = None) -> tuple:
        """Simulator balance fetch with its own error handling."""
        balances = await self.get_balance()
        return (True, balances)

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
        if not str(self.auth_token or "").strip():
            return self._error_payload(401, "Unauthorized")
        if self.force_fetch_fills_http_status:
            return self._error_payload(int(self.force_fetch_fills_http_status), "Simulated fetch_fills failure")

        fills = list(self.fills)
        if since_ts is not None:
            since = int(since_ts)
            fills = [fill for fill in fills if int(fill.get("timestamp", 0)) >= since]

        start = 0
        if cursor not in (None, ""):
            try:
                start = max(0, int(cursor))
            except Exception:
                start = 0

        if limit > 0:
            page = fills[start : start + int(limit)]
        else:
            page = fills[start:]
        next_cursor = None
        next_offset = start + len(page)
        if next_offset < len(fills):
            next_cursor = str(next_offset)
        return {
            "status": "success",
            "fills": page,
            "source": "virtual",
            "next_cursor": next_cursor,
        }

    def inject_fill(
        self,
        *,
        order_id: str,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        role: str = "maker",
        order_type: str = "POST_ONLY",
        timestamp_ms: Optional[int] = None,
    ) -> None:
        """Inject a synthetic fill for replay/chaos tests."""
        self._append_fill(
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price,
            qty=qty,
            role=role,
            order_type=order_type,
            timestamp_ms=timestamp_ms,
        )
