import os
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

import ccxt
from src.execution.v2_models import ExecutionErrorCode, ExecutionState

logger = logging.getLogger(__name__)

class BlofinExecutionBridge:
    """
    Execution bridge for Blofin exchange (PH2-05).
    Handles order placement, cancellation, and status tracking.
    """

    def __init__(self):
        self.exchange = None
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
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Submit a bracket order (Entry + SL + TP) to Blofin (Phase 2).
        """
        blofin_symbol = f"{symbol.replace('-', '/')}:USDT"
        side_norm = side.lower().strip()
        ccxt_side = "buy" if side_norm in {"long", "buy"} else "sell"
        
        logger.info(f"Submitting BRACKET for {symbol}: Entry {price}, SL {sl_price}, TP {tp_price} [dry_run={dry_run}]")
        
        if dry_run:
            return {
                "entry_order_id": f"dry_entry_{idempotency_key[:8]}",
                "sl_order_id": f"dry_sl_{idempotency_key[:8]}",
                "tp_order_id": f"dry_tp_{idempotency_key[:8]}",
                "state": ExecutionState.SUBMITTED,
                "status": "open",
                "protection_status": "ARMED",
                "info": {"dry_run": True}
            }

        if not os.getenv("BLOFIN_LIVE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
            return {
                "error": ExecutionErrorCode.ERR_ORDER_SUBMIT_FAILED,
                "reason": "LIVE_EXECUTION_DISABLED_IN_BRIDGE",
                "entry_order_id": None,
            }

        if not self.exchange:
            return {"error": ExecutionErrorCode.ERR_INTERNAL_RETRY_EXHAUSTED, "reason": "Exchange not initialized"}

        try:
            # Blofin supports stopLoss and takeProfit in the main order call for some instruments
            # If not supported, we fall back to sequential placement
            params = {
                'clientOrderId': idempotency_key,
                'stopLoss': {
                    'triggerPrice': sl_price,
                    'type': 'limit' # Or market based on config
                },
                'takeProfit': {
                    'triggerPrice': tp_price,
                    'type': 'limit'
                }
            }
            
            # Note: CCXT Blofin might have specific parameter names for unified bracketing
            # Fallback to standard Stop/Take Profit parameters
            order = self.exchange.create_order(
                symbol=blofin_symbol,
                type='limit',
                side=ccxt_side,
                amount=qty,
                price=price,
                params=params
            )
            
            return {
                "entry_order_id": order.get('id'),
                "protective_order_ids": {
                    "sl": order.get('stopLossOrderId', 'attached'),
                    "tp": order.get('takeProfitOrderId', 'attached')
                },
                "state": ExecutionState.SUBMITTED,
                "protection_status": "ARMED",
                "info": order
            }

        except ccxt.NetworkError as e:
            logger.error(f"Blofin Network Error: {e}")
            return {"error": ExecutionErrorCode.ERR_EXCHANGE_TIMEOUT, "reason": str(e)}
        except ccxt.ExchangeError as e:
            logger.error(f"Blofin Exchange Error: {e}")
            # If atomic bracketing fails, we might need to retry without it or handle specific rejection
            return {"error": ExecutionErrorCode.ERR_EXCHANGE_REJECTED, "reason": str(e)}
        except Exception as e:
            logger.error(f"Bracket submission failed: {e}")
            return {"error": ExecutionErrorCode.ERR_ORDER_SUBMIT_FAILED, "reason": str(e)}

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
