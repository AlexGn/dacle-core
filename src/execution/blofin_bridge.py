import os
import logging
import time
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone

import ccxt
from src.execution.v2_models import ExecutionErrorCode, ExecutionState
from src.execution.state_manager import ExecutionStateManager
from src.utils.latency_logger import LatencyAuditLogger

logger = logging.getLogger(__name__)

class BlofinExecutionBridge:
    """
    Execution bridge for Blofin exchange (PH2-05).
    Handles order placement, cancellation, and status tracking.
    """

    def __init__(self):
        self.exchange = None
        self._account_exchanges: Dict[str, Any] = {}
        self._default_account_id = self._normalize_account_id(None)
        self.latency_logger = LatencyAuditLogger()
        self._init_exchange()

    @staticmethod
    def _env_bool(name: str, default: bool = False) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _normalize_account_id(account_id: Optional[str]) -> str:
        candidate = str(account_id or "").strip()
        if candidate:
            return candidate
        fallback = str(os.getenv("EXECUTION_DEFAULT_ACCOUNT_ID", "primary") or "").strip()
        return fallback or "primary"

    @staticmethod
    def _account_env_suffix(account_id: str) -> str:
        raw = str(account_id or "").strip()
        if not raw:
            return "PRIMARY"
        cleaned = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")
        return (cleaned or "PRIMARY").upper()

    @staticmethod
    def _force_ipv4() -> None:
        # Session 389b: Force IPv4 for whitelist consistency
        import socket
        import urllib3.util.connection as urllib3_conn

        urllib3_conn.allowed_gai_family = lambda: socket.AF_INET

    def _resolve_account_credentials(self, account_id: Optional[str]) -> Optional[Tuple[str, str, str]]:
        resolved_account = self._normalize_account_id(account_id)
        suffix = self._account_env_suffix(resolved_account)

        scoped_key = os.getenv(f"BLOFIN_API_KEY_{suffix}")
        scoped_secret = os.getenv(f"BLOFIN_API_SECRET_{suffix}")
        scoped_passphrase = os.getenv(f"BLOFIN_PASSPHRASE_{suffix}")
        scoped_values = (scoped_key, scoped_secret, scoped_passphrase)
        if any(scoped_values):
            if all(scoped_values):
                return str(scoped_key), str(scoped_secret), str(scoped_passphrase)
            logger.error("Incomplete Blofin scoped credentials for account_id=%s", resolved_account)
            return None

        base_key = os.getenv("BLOFIN_API_KEY")
        base_secret = os.getenv("BLOFIN_API_SECRET")
        base_passphrase = os.getenv("BLOFIN_PASSPHRASE")
        if resolved_account == self._default_account_id:
            if all((base_key, base_secret, base_passphrase)):
                return str(base_key), str(base_secret), str(base_passphrase)
            return None

        if self._env_bool("SWING_BLOFIN_ALLOW_DEFAULT_CREDENTIAL_FALLBACK", default=False):
            live_mode = self._env_bool("BLOFIN_LIVE_ENABLED", default=False)
            allow_live_fallback = self._env_bool(
                "SWING_BLOFIN_ALLOW_DEFAULT_CREDENTIAL_FALLBACK_LIVE",
                default=False,
            )
            if live_mode and not allow_live_fallback:
                logger.error(
                    "Blocked default Blofin credentials fallback for account_id=%s in live mode",
                    resolved_account,
                )
                return None
            if all((base_key, base_secret, base_passphrase)):
                logger.warning("Using default Blofin credentials fallback for account_id=%s", resolved_account)
                return str(base_key), str(base_secret), str(base_passphrase)

        return None

    @staticmethod
    def _build_exchange(api_key: str, api_secret: str, passphrase: str):
        return ccxt.blofin({
            'apiKey': api_key,
            'secret': api_secret,
            'password': passphrase,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',
            }
        })

    def _exchange_for_account(self, account_id: Optional[str]) -> Optional[Any]:
        resolved_account = self._normalize_account_id(account_id)

        existing = self._account_exchanges.get(resolved_account)
        if existing is not None:
            return existing

        if resolved_account == self._default_account_id and self.exchange is not None:
            self._account_exchanges[resolved_account] = self.exchange
            return self.exchange

        creds = self._resolve_account_credentials(resolved_account)
        if not creds:
            if resolved_account == self._default_account_id:
                logger.warning("Blofin credentials missing - bridge operating in limited mode")
            else:
                logger.warning("Blofin credentials missing for account_id=%s", resolved_account)
            return None

        try:
            exchange = self._build_exchange(*creds)
            self._force_ipv4()
        except Exception as e:
            logger.error("Failed to initialize Blofin Bridge for account_id=%s: %s", resolved_account, e)
            return None

        self._account_exchanges[resolved_account] = exchange
        if resolved_account == self._default_account_id:
            self.exchange = exchange
        return exchange

    def _init_exchange(self) -> None:
        """Initialize default account exchange with credentials."""
        self.exchange = self._exchange_for_account(self._default_account_id)

    def _validate_execution_context(
        self,
        idempotency_key: str,
        execution_context_token: Optional[str],
        expected_context_state: ExecutionState,
    ) -> Optional[Dict[str, Any]]:
        ok, reason = ExecutionStateManager().validate_bridge_context_token(
            execution_context_token or "",
            idempotency_key=idempotency_key,
            required_state=expected_context_state,
        )
        if ok:
            return None
        logger.error("Bridge execution context rejected for %s: %s", idempotency_key, reason)
        return {
            "error": ExecutionErrorCode.ERR_CONTEXT_GUARD_FAILED,
            "reason": f"CONTEXT_{reason}",
            "entry_order_id": None,
        }

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
        execution_policy: str = "LIMIT_ONLY",
        execution_context_token: Optional[str] = None,
        expected_context_state: ExecutionState = ExecutionState.PROTECTION_SUBMITTING,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Submit a bracket order (Entry + SL + TP) to Blofin (Phase 2).
        """
        t3_submit = time.monotonic_ns()
        blofin_symbol = f"{symbol.replace('-', '/')}:USDT"
        side_norm = side.lower().strip()
        if side_norm not in {"long", "buy", "short", "sell"}:
            logger.error("Rejecting bracket submit for %s: invalid side=%s", symbol, side)
            return {
                "error": ExecutionErrorCode.ERR_ORDER_SUBMIT_FAILED,
                "reason": "INVALID_SIDE",
                "entry_order_id": None,
            }
        ccxt_side = "buy" if side_norm in {"long", "buy"} else "sell"
        
        logger.info(f"Submitting BRACKET for {symbol}: Entry {price}, SL {sl_price}, TP {tp_price} [dry_run={dry_run}, policy={execution_policy}, tif={time_in_force}]")
        
        if not os.getenv("BLOFIN_LIVE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
            if not dry_run:
                return {
                    "error": ExecutionErrorCode.ERR_ORDER_SUBMIT_FAILED,
                    "reason": "LIVE_EXECUTION_DISABLED_IN_BRIDGE",
                    "entry_order_id": None,
                }

        context_err = self._validate_execution_context(
            idempotency_key=idempotency_key,
            execution_context_token=execution_context_token,
            expected_context_state=expected_context_state,
        )
        if context_err:
            return context_err

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

        exchange = self._exchange_for_account(account_id)
        if not exchange:
            return {
                "error": ExecutionErrorCode.ERR_INTERNAL_RETRY_EXHAUSTED,
                "reason": "ACCOUNT_EXCHANGE_NOT_CONFIGURED",
            }

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
            order = exchange.create_order(
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

    def cancel_order(
        self,
        symbol: str,
        order_id: str,
        dry_run: bool = True,
        account_id: Optional[str] = None,
    ) -> bool:
        """Cancel an existing order."""
        if dry_run:
            logger.info(f"DRY RUN: Canceled order {order_id}")
            return True

        exchange = self._exchange_for_account(account_id)
        if not exchange:
            return False

        try:
            blofin_symbol = f"{symbol.replace('-', '/')}:USDT"
            exchange.cancel_order(order_id, blofin_symbol)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def emergency_flatten(
        self,
        symbol: str,
        side: str,
        qty: float,
        idempotency_key: str,
        dry_run: bool = True,
        account_id: Optional[str] = None,
    ) -> bool:
        """
        Best-effort emergency flatten after protection arming failure.
        Places reduce-only market order in the opposite direction.
        """
        if qty <= 0:
            logger.error("Emergency flatten rejected for %s: invalid qty=%s", symbol, qty)
            return False

        side_norm = str(side or "").strip().lower()
        if side_norm not in {"long", "buy", "short", "sell"}:
            logger.error("Emergency flatten rejected for %s: invalid side=%s", symbol, side)
            return False
        close_side = "sell" if side_norm in {"long", "buy"} else "buy"

        if dry_run:
            logger.warning(
                "DRY RUN: Emergency flatten %s side=%s qty=%s idempotency_key=%s",
                symbol,
                close_side,
                qty,
                idempotency_key,
            )
            return True

        exchange = self._exchange_for_account(account_id)
        if not exchange:
            return False

        try:
            blofin_symbol = f"{symbol.replace('-', '/')}:USDT"
            exchange.create_order(
                symbol=blofin_symbol,
                type="market",
                side=close_side,
                amount=qty,
                params={
                    "reduceOnly": True,
                    "clientOrderId": f"{idempotency_key}:flatten",
                },
            )
            logger.warning("Emergency flatten submitted for %s qty=%s", symbol, qty)
            return True
        except Exception as e:
            logger.error("Emergency flatten failed for %s: %s", symbol, e)
            return False

    def get_order_status(self, symbol: str, order_id: str, account_id: Optional[str] = None) -> Dict[str, Any]:
        """Fetch current order status and fills."""
        exchange = self._exchange_for_account(account_id)
        if not exchange:
            return {}

        try:
            blofin_symbol = f"{symbol.replace('-', '/')}:USDT"
            order = exchange.fetch_order(order_id, blofin_symbol)
            
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
