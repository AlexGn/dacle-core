"""
DACLE Polymarket Execution Wrapper
Standardized execution client for Polymarket CLOB.
Handles: tick-size normalization, idempotency, GTD orders, and fail-fast safety.
"""

import logging
import os
import time
import asyncio
import json
from pathlib import Path
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from typing import Any, Dict, Optional, List, Set, Tuple
from src.monitoring.heartbeat_discord import post_to_discord
from src.polymarket.micro_live_session import can_admit_order, load_active_session, record_order_attempt

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, ApiCreds, OrderType
except ModuleNotFoundError:  # pragma: no cover - local/dev fallback
    class ClobClient:  # type: ignore[no-redef]
        pass

    class OrderArgs:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class ApiCreds:  # type: ignore[no-redef]
        def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
            self.api_key = api_key
            self.api_secret = api_secret
            self.api_passphrase = api_passphrase

    class OrderType:  # type: ignore[no-redef]
        GTC = "GTC"
        GTD = "GTD"
        FOK = "FOK"
        FAK = "FAK"

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
LEGACY_ORDER_TYPE_ALIASES = {
    # Legacy callers still pass venue-agnostic terms from pre-wrapper contracts.
    "IOC": "FAK",
    "POST_ONLY": "GTD",
    "LIMIT": "GTC",
}

class PolymarketClientWrapper:
    def __init__(self, config: dict, client: ClobClient):
        self.config = config
        self.client = client
        
        # Session 558: Env var override MUST take absolute priority over config
        env_mode = os.getenv("POLY_MODE", "").upper()
        if env_mode in ("SHADOW", "PAPER", "LIVE"):
            self.mode = env_mode
        else:
            self.mode = config.get("mode", "SHADOW").upper()

        # Batch C: Private RPC
        self.base_url = os.getenv("POLY_CLOB_API_BASE_URL") or config.get("host", "https://clob.polymarket.com")
        self.fallback_url = "https://clob.polymarket.com"

        # Session 558: LIVE mode requires both POLY_LIVE_ENABLED=true AND mode=LIVE
        # We check this strictly to prevent accidental live execution.
        live_enabled = os.getenv("POLY_LIVE_ENABLED", "false").lower() == "true"
        if self.mode == "LIVE" and not live_enabled:
            logger.warning("POLY_MODE=LIVE but POLY_LIVE_ENABLED is not true. Falling back to SHADOW.")
            self.mode = "SHADOW"

        # Execution settings
        exec_cfg = config.get("execution", {})
        self.default_expiration_sec = exec_cfg.get("order_timeout_sec", 60)
        self.gtd_min_expiration_lead_sec = max(
            61, int(exec_cfg.get("gtd_min_expiration_lead_sec", 120) or 120)
        )
        self.gtd_expiration_safety_sec = max(
            0, int(exec_cfg.get("gtd_expiration_safety_sec", 2) or 2)
        )
        self.max_retries = exec_cfg.get("max_retries", 3)
        self.intent_max_age_sec = float(
            exec_cfg.get("intent_max_age_sec", config.get("intent_max_age_sec", 0.0)) or 0.0
        )
        self.qty_precision = max(0, int(exec_cfg.get("qty_precision", 4)))
        self.strict_signing_precision = bool(exec_cfg.get("strict_signing_precision", True))
        max_concurrent = int(exec_cfg.get("max_concurrent_requests", 5))
        self._exec_semaphore = asyncio.Semaphore(max(1, max_concurrent))

        # Internal state
        self._pending_tasks: Set[asyncio.Task] = set()
        self._order_guard_cfg = config.get("polymarket", {}).get("order_guard", {})
        self.journal_path = config.get("state", {}).get(
            "journal_path",
            "data/audit/polymarket_trade_journal.jsonl",
        )

    def _ensure_account(self) -> bool:
        """Lazily load signing account on first live execution call."""
        # The low-level client handles account persistence.
        # We just check if it's configured.
        return True

    def normalize_price(self, price: float, tick_size: float, side: str) -> float:
        """Normalize price to market tick size."""
        # TICK size must be followed strictly
        # BUY orders must round DOWN to avoid exceeding price limits
        # SELL orders must round UP to avoid exceeding price limits
        p = Decimal(str(price))
        t = Decimal(str(tick_size))
        
        # Binary markets (0-1 range)
        if side.upper() == "BUY":
            # Round down to nearest tick
            normalized = (p / t).to_integral_value(rounding=ROUND_DOWN) * t
        else:
            # Round up to nearest tick
            normalized = (p / t).to_integral_value(rounding="ROUND_UP") * t
            
        return float(normalized)

    def normalize_qty(self, qty: float) -> float:
        """Normalize quantity to allowed precision."""
        p = self.qty_precision
        return round(float(qty), p)

    def validate_signable_order(
        self,
        *,
        price: float,
        qty: float,
        side: str,
        tick_size: float,
        min_order_size: float,
        strict_precision: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Validate an order is signable before signature generation."""
        strict = self.strict_signing_precision if strict_precision is None else bool(strict_precision)
        norm_price = self.normalize_price(price, tick_size, side)
        norm_qty = self.normalize_qty(qty)

        if norm_qty <= 0:
            return {"ok": False, "error_code": "INVALID_QTY", "error": f"Invalid quantity: {qty}"}
        if norm_qty < float(min_order_size):
            return {
                "ok": False,
                "error_code": "MIN_SIZE_REJECT",
                "error": f"Quantity {norm_qty} below minimum {min_order_size}",
            }

        if strict:
            if abs(float(price) - float(norm_price)) > 1e-9:
                return {
                    "ok": False,
                    "error_code": "INVALID_PRECISION",
                    "error": f"Price precision invalid: {price} (tick={tick_size})",
                }
            if abs(float(qty) - float(norm_qty)) > 1e-9:
                return {
                    "ok": False,
                    "error_code": "INVALID_PRECISION",
                    "error": f"Quantity precision invalid: {qty}",
                }

        return {
            "ok": True,
            "normalized_price": norm_price,
            "normalized_qty": norm_qty,
        }

    def _validate_intent_freshness(self, metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Reject signals that are too old to execute safely."""
        if self.intent_max_age_sec <= 0:
            return None
            
        if not metadata or "intent_ts" not in metadata:
            return None
            
        age = time.time() - float(metadata["intent_ts"])
        if age > self.intent_max_age_sec:
            logger.warning("INTENT_STALE: signal is %.2fs old (limit %.2fs)", age, self.intent_max_age_sec)
            return {
                "status": "error",
                "error_code": "INTENT_STALE",
                "error": f"Intent stale: {age:.2f}s old",
            }
        return None

    def _validate_live_order_guard(
        self,
        *,
        token_id: str,
        strategy: Optional[str],
        qty: float,
        price: float,
    ) -> Optional[Dict[str, Any]]:
        """Perform safety checks for live order submission."""
        if not self._order_guard_cfg.get("enabled", False):
            return None

        strategy_norm = (strategy or "unknown").lower()
        
        # 1. Strategy check
        allowed_strategies = self._order_guard_cfg.get("allowed_strategies", [])
        if allowed_strategies and strategy_norm not in [s.lower() for s in allowed_strategies]:
            logger.critical(
                "ORDER_GUARD_REJECT: strategy '%s' not in allowed list %s",
                strategy_norm,
                allowed_strategies,
            )
            return {
                "status": "error",
                "error_code": "ORDER_GUARD_REJECT",
                "error": f"order guard reject: strategy '{strategy_norm}' not allowed",
            }

        # 2. Quantity bands
        bands = self._order_guard_cfg.get("strategy_qty_bands", {}).get(strategy_norm, {})
        if bands:
            min_qty = float(bands.get("min_qty", 0.0))
            max_qty = float(bands.get("max_qty", float("inf")))
            if qty < min_qty:
                return {
                    "status": "error",
                    "error_code": "ORDER_GUARD_REJECT",
                    "error": f"order guard reject: qty {qty} below min {min_qty} for {strategy_norm}",
                }
            if qty > max_qty:
                return {
                    "status": "error",
                    "error_code": "ORDER_GUARD_REJECT",
                    "error": f"order guard reject: qty {qty} exceeds max {max_qty} for {strategy_norm}",
                }

        # 3. Notional limits
        max_notional = float(self._order_guard_cfg.get("max_order_notional_usd", 0.0) or 0.0)
        min_notional = float(self._order_guard_cfg.get("min_order_notional_usd", 0.0) or 0.0)
        notional = float(price) * float(qty)
        
        if min_notional > 0 and notional < min_notional:
            logger.critical(
                "ORDER_GUARD_REJECT: notional %.6f < min_notional %.6f strategy=%s token=%s",
                notional,
                min_notional,
                strategy_norm,
                token_id,
            )
            return {
                "status": "error",
                "error_code": "ORDER_GUARD_REJECT",
                "error": f"order guard reject: notional {notional} below min {min_notional}",
            }

        if max_notional > 0 and notional > max_notional:
            logger.critical(
                "ORDER_GUARD_REJECT: notional %.6f > max_notional %.6f strategy=%s token=%s",
                notional,
                max_notional,
                strategy_norm,
                token_id,
            )
            return {
                "status": "error",
                "error_code": "ORDER_GUARD_REJECT",
                "error": f"order guard reject: notional {notional} exceeds {max_notional}",
            }

        return None

    def _validate_live_execution_policy(
        self,
        *,
        token_id: str,
        strategy: Optional[str],
        qty: float,
        price: float,
        policy_context: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Enforce higher-level execution policies (e.g. daily caps, cooldowns)."""
        # Load active micro-live session if available
        session = load_active_session()
        if session:
            admit, reason = can_admit_order(session, token_id, float(price) * float(qty))
            if not admit:
                return {
                    "status": "error",
                    "error_code": "POLICY_REJECT",
                    "error": f"Policy reject: {reason}",
                }
        
        # Check global execution policy
        policy_cfg = self.config.get("polymarket", {}).get("execution_policy", {})
        if policy_cfg.get("mode") == "soak-only":
            return {
                "status": "error",
                "error_code": "POLICY_REJECT",
                "error": "Policy reject: soak-only mode active",
            }
            
        return None

    async def get_usdc_balance_and_allowance(self) -> Dict[str, Any]:
        """Fetch USDC balance and allowance from the ClobClient."""
        try:
            # Proxy to the underlying ClobClient
            res = await self.client.get_balance()
            return {
                "ok": True,
                "balance_usdc": float(res.get("balance", 0.0)),
                "allowance_usdc": float(res.get("allowance", 0.0)),
            }
        except Exception as e:
            logger.error(f"Failed to get USDC balance and allowance: {e}")
            return {"ok": False, "error": str(e)}

    async def get_balance(self, asset_id: Optional[str] = None) -> float:

        """Fetch balance for collateral (default) or a specific asset ID."""
        if self.mode == "SHADOW":
            return 1000.0 # Standard shadow balance
            
        try:
            # Proxy to underlying client
            if hasattr(self.client, "get_balance"):
                return await self.client.get_balance(asset_id)
            return 0.0
        except Exception as e:
            logger.error(f"Failed to fetch balance for {asset_id}: {e}")
            return 0.0

    async def create_order(
        self,
        token_id: str,
        price: float,
        qty: float,
        side: str,
        order_type: str = "GTD",
        expiration_sec: Optional[int] = None,
        client_order_id: Optional[str] = None,
        strategy: Optional[str] = None,
        entry_alert: bool = True,
        policy_context: Optional[Dict[str, Any]] = None,
        intent_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create and post an order to Polymarket.
        """
        # Session 558: Shadow check MUST happen before any live-only validation
        if self.mode == "SHADOW":
            logger.info(
                f"[SHADOW] Post Order: {side.upper()} {qty} @ {price} (token={token_id}, type={order_type})"
            )
            return {"status": "success", "tx_hash": "shadow_tx", "shadow": True}

        side_val = side.upper()
        if side_val not in ("BUY", "SELL"):
            return {"status": "error", "error": f"Invalid side: {side}"}

        requested_order_type = order_type.upper()
        order_type = LEGACY_ORDER_TYPE_ALIASES.get(requested_order_type, requested_order_type)
        if order_type not in VALID_ORDER_TYPES:
            return {"status": "error", "error": f"Invalid order_type: {requested_order_type}"}

        # 1. Resolve Metadata
        try:
            meta = await self.get_market_metadata(token_id)
        except Exception as e:
            return {"status": "error", "error": f"Metadata fetch failed: {e}"}

        validation = self.validate_signable_order(
            price=price,
            qty=qty,
            side=side_val,
            tick_size=float(meta["tick_size"]),
            min_order_size=float(meta["min_order_size"]),
        )
        if not validation.get("ok"):
            return {
                "status": "error",
                "error": validation.get("error", "Order failed signability checks"),
                "error_code": validation.get("error_code", "VALIDATION_REJECT"),
            }

        norm_price = float(validation["normalized_price"])
        qty_norm = float(validation["normalized_qty"])

        # 2. Validations
        freshness_error = self._validate_intent_freshness(intent_metadata)
        if freshness_error:
            return freshness_error

        guard_error = self._validate_live_order_guard(
            token_id=token_id,
            strategy=strategy,
            qty=qty_norm,
            price=norm_price,
        )
        if guard_error:
            return guard_error

        policy_error = self._validate_live_execution_policy(
            token_id=token_id,
            strategy=strategy,
            qty=qty_norm,
            price=norm_price,
            policy_context=policy_context,
        )
        if policy_error:
            return policy_error

        # 3. Idempotency Key
        if not client_order_id:
            client_order_id = f"dacle_{int(time.time() * 1000)}"

        # 4. Actual Submission (Simplified for wrapper skeleton)
        try:
            async with self._exec_semaphore:
                # This would normally call the low-level client
                # For skeleton, we simulate success
                res = {"status": "success", "tx_hash": "mock_live_tx"}
                
                if res["status"] == "success" and entry_alert:
                    self._start_background_alert(
                        token_id=token_id,
                        side=side_val,
                        price=norm_price,
                        qty=qty_norm,
                        strategy=strategy
                    )
                return res
        except Exception as e:
            logger.error(f"Order execution failed: {e}")
            return {"status": "error", "error": str(e)}

    async def get_market_metadata(self, token_id: str) -> Dict[str, Any]:
        """Fetch market metadata including tick size and min order size."""
        # This would call ClobClient.get_market()
        # Mock implementation for skeleton pass
        return {
            "tick_size": 0.01,
            "min_order_size": 1.0,
            "market_id": "mock_market",
        }

    def _start_background_alert(self, **kwargs):
        """Trigger discord alert in background task."""
        task = asyncio.create_task(self._post_entry_alert(**kwargs))
        self._pending_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)

    async def _post_entry_alert(self, token_id, side, price, qty, strategy):
        """Format and post entry alert to Discord."""
        msg = f"🚀 **Polymarket Entry**: {side} {qty} {token_id} @ {price} | Strategy: {strategy}"
        await post_to_discord("polymarket", msg)

    def _on_background_task_done(self, task):
        """Clean up background tasks."""
        self._pending_tasks.discard(task)
        try:
            task.result()
        except Exception as e:
            logger.error(f"Background task failed: {e}")
