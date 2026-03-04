import os
import logging
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

import ccxt
from src.execution.v2_models import ExecutionErrorCode, ExecutionState
from src.utils.latency_logger import LatencyAuditLogger

logger = logging.getLogger(__name__)

class BlofinExecutionBridge:
    """
    Execution bridge for Blofin exchange (PH2-05).
    Handles order placement, cancellation, and status tracking.
    """

    def __init__(self):
        self.exchange = None
        self.latency_logger = LatencyAuditLogger()
        self._init_exchange()

    def _init_exchange(self) -> None:
        """Initialize ccxt Blofin exchange with credentials."""
        try:
            api_key = os.getenv("BLOFIN_API_KEY")
            api_secret = os.getenv("BLOFIN_API_SECRET")
            passphrase = os.getenv("BLOFIN_PASSPHRASE")

            if not api_key or not api_secret or not passphrase:
                logger.warning("Blofin credentials missing - bridge operating in limited mode")
                return

            self.exchange = ccxt.blofin({
                'apiKey': api_key,
                'secret': api_secret,
                'password': passphrase,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'swap',
                }
            })
            
            # Session 389b: Force IPv4 for whitelist consistency
            import socket
            import urllib3.util.connection as urllib3_conn
            urllib3_conn.allowed_gai_family = lambda: socket.AF_INET
            
        except Exception as e:
            logger.error(f"Failed to initialize Blofin Bridge: {e}")

    def submit_bracket_order(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        sl_price: float,
        tp_price: float,
        idempotency_key: str,
        dry_run: bool = True,
        latency_meta: Optional[Dict[str, Any]] = None,
        time_in_force: str = "GTC",
        execution_policy: str = "LIMIT_ONLY"
    ) -> Dict[str, Any]:
        """
        Submit a bracket order (Entry + SL + TP) to Blofin (Phase 2).
        """
        t3_submit = time.monotonic_ns()
        blofin_symbol = f"{symbol.replace('-', '/')}:USDT"
        side_norm = side.lower().strip()
        ccxt_side = "buy" if side_norm in {"long", "buy"} else "sell"
        
        logger.info(f"Submitting BRACKET for {symbol}: Entry {price}, SL {sl_price}, TP {tp_price} [dry_run={dry_run}, policy={execution_policy}, tif={time_in_force}]")
        
        if dry_run:
            t4_ack = time.monotonic_ns()
            res = {
                "entry_order_id": f"DRY_RUN_ENTRY_{idempotency_key[:8]}",
                "sl_order_id": f"DRY_RUN_SL_{idempotency_key[:8]}",
                "tp_order_id": f"DRY_RUN_TP_{idempotency_key[:8]}",
                "state": ExecutionState.SUBMITTED,
                "status": "open",
                "protection_status": "ARMED",
                "info": {"dry_run": True, "average": price, "filled": qty, "amount": qty}
            }
            if latency_meta:
                self._log_latency_event(idempotency_key, symbol, side, price, res["info"], t3_submit, t4_ack, latency_meta)
            return res

        if not os.getenv("BLOFIN_LIVE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
            return {
                "error": ExecutionErrorCode.ERR_ORDER_SUBMIT_FAILED,
                "reason": "LIVE_EXECUTION_DISABLED_IN_BRIDGE",
                "entry_order_id": None,
            }

        if not self.exchange:
            return {"error": ExecutionErrorCode.ERR_INTERNAL_RETRY_EXHAUSTED, "reason": "Exchange not initialized"}

        try:
            # Map Execution Policy to CCXT params
            params = {
                'clientOrderId': idempotency_key,
                'stopLoss': {
                    'triggerPrice': sl_price,
                    'type': 'limit'
                },
                'takeProfit': {
                    'triggerPrice': tp_price,
                    'type': 'limit'
                }
            }
            
            # Time-In-Force
            if time_in_force:
                params['timeInForce'] = time_in_force
                
            # Post-Only (Maker Only)
            if execution_policy == "MAKER_ONLY":
                params['postOnly'] = True

            # Submit order
            order = self.exchange.create_order(
                symbol=blofin_symbol,
                type='limit',
                side=ccxt_side,
                amount=qty,
                price=price,
                params=params
            )
            t4_ack = time.monotonic_ns()
            
            t5_fill = None
            if order.get("status") == "closed":
                t5_fill = time.monotonic_ns()

            res = {
                "entry_order_id": order.get('id'),
                "protective_order_ids": {
                    "sl": order.get('stopLossOrderId', 'attached'),
                    "tp": order.get('takeProfitOrderId', 'attached')
                },
                "state": ExecutionState.SUBMITTED if order.get("status") != "closed" else ExecutionState.FILLED,
                "protection_status": "ARMED",
                "info": order
            }
            
            if latency_meta:
                self._log_latency_event(idempotency_key, symbol, side, price, order, t3_submit, t4_ack, latency_meta, t5_fill)
                
            return res

        except ccxt.NetworkError as e:
            logger.error(f"Blofin Network Error: {e}")
            return {"error": ExecutionErrorCode.ERR_EXCHANGE_TIMEOUT, "reason": str(e)}
        except ccxt.ExchangeError as e:
            logger.error(f"Blofin Exchange Error: {e}")
            return {"error": ExecutionErrorCode.ERR_EXCHANGE_REJECTED, "reason": str(e)}
        except Exception as e:
            logger.error(f"Bracket submission failed: {e}")
            return {"error": ExecutionErrorCode.ERR_ORDER_SUBMIT_FAILED, "reason": str(e)}

    def _log_latency_event(self, key, symbol, side, req_price, order, t3, t4, meta, t5=None):
        """Helper to log latency data to JSONL."""
        timestamps = {
            "t1_ingress": meta.get("t1_ingress"),
            "t2_reval_done": meta.get("t2_reval_done"),
            "t3_submit": t3,
            "t4_ack": t4,
        }
        if t5:
            timestamps["t5_first_fill"] = t5
            timestamps["t6_full_fill"] = t5 # Simplified for immediate fills
            
        prices = {
            "requested": meta.get("requested_price", req_price),
            "expected_vwap": meta.get("expected_vwap", req_price),
            "actual_vwap": order.get("average") or order.get("price") or req_price
        }
        
        fill_ratio = 1.0
        if order.get("status") != "closed":
            amount = float(order.get("amount") or 1.0)
            filled = float(order.get("filled") or 0.0)
            fill_ratio = filled / amount if amount > 0 else 0.0
        
        self.latency_logger.log_event(
            intent_id=key,
            symbol=symbol,
            side=side,
            prices=prices,
            timestamps=timestamps,
            metadata={
                "fill_ratio": round(fill_ratio, 4),
                "dry_run": "DRY_RUN" in str(order.get("id", ""))
            }
        )

    def cancel_order(self, symbol: str, order_id: str, dry_run: bool = True) -> bool:
        """Cancel an existing order."""
        if dry_run:
            logger.info(f"DRY RUN: Canceled order {order_id}")
            return True

        if not self.exchange:
            return False

        try:
            blofin_symbol = f"{symbol.replace('-', '/')}:USDT"
            self.exchange.cancel_order(order_id, blofin_symbol)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def get_order_status(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Fetch current order status and fills."""
        if not self.exchange:
            return {}

        try:
            blofin_symbol = f"{symbol.replace('-', '/')}:USDT"
            order = self.exchange.fetch_order(order_id, blofin_symbol)
            
            # Map ccxt status to ExecutionState
            status = order.get('status')
            state = ExecutionState.SUBMITTED
            if status == 'closed':
                state = ExecutionState.FILLED
            elif status == 'canceled':
                state = ExecutionState.CANCELED
            elif order.get('filled', 0) > 0:
                state = ExecutionState.PARTIALLY_FILLED
                
            return {
                "state": state,
                "filled_qty": order.get('filled', 0.0),
                "remaining_qty": order.get('remaining', 0.0),
                "avg_fill_price": order.get('average', 0.0),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to fetch status for {order_id}: {e}")
            return {}
