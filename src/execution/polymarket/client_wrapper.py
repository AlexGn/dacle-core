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
        self.mode = config.get("mode", "SHADOW").upper()

        # Batch C: Private RPC
        self.base_url = os.getenv("POLY_CLOB_API_BASE_URL") or config.get("host", "https://clob.polymarket.com")
        self.fallback_url = "https://clob.polymarket.com"

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

        # Internal caches with TTL
        self._market_metadata: Dict[str, Dict[str, Any]] = {}
        self._metadata_lock = asyncio.Lock()
        self._metadata_ttl_sec: float = 3600.0

        # Latency monitoring (rolling last 10)
        self._order_latencies: List[float] = []
        self._latency_lock = asyncio.Lock()
        order_guard_cfg = config.get("order_guard", {})
        self._order_guard_cfg = order_guard_cfg if isinstance(order_guard_cfg, dict) else {}
        legacy_policy_cfg = config.get("execution_policy", {})
        self._legacy_execution_policy_cfg = (
            legacy_policy_cfg if isinstance(legacy_policy_cfg, dict) else {}
        )
        live_policy_cfg = config.get("live_execution_policy", {})
        self._live_execution_policy_cfg = (
            live_policy_cfg if isinstance(live_policy_cfg, dict) else {}
        )
        self._order_submission_audit_path = str(
            config.get(
                "order_submission_audit_path",
                "data/audit/polymarket_order_submissions.jsonl",
            )
        )

    def _is_shadow_mode(self) -> bool:
        return self.mode == "SHADOW"

    def _dry_run_admit_enabled(self) -> bool:
        return self.mode == "LIVE" and self._live_execution_policy_enabled() and self._execution_mode() == "dry_run_admit"

    def _is_execution_shadowed(self) -> bool:
        return self._is_shadow_mode() or self._dry_run_admit_enabled()

    def _discord_entry_alerts_enabled(self) -> bool:
        if self.mode != "LIVE":
            return False
        raw = str(os.getenv("POLY_DISCORD_ENTRY_ALERTS", "true")).strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False
        return bool(str(os.getenv("DISCORD_BOT_TOKEN", "")).strip())

    @staticmethod
    def _is_truthy_env(raw: Optional[str]) -> bool:
        if raw is None:
            return False
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _order_guard_enabled(self) -> bool:
        env_override = os.getenv("POLY_ORDER_GUARD_ENABLED")
        if env_override is not None:
            return self._is_truthy_env(env_override)
        return bool(self._order_guard_cfg.get("enabled", False))

    def _live_execution_policy_enabled(self) -> bool:
        env_override = os.getenv("POLY_LIVE_EXECUTION_POLICY_ENABLED")
        if env_override is not None:
            return self._is_truthy_env(env_override)
        if self._live_execution_policy_cfg:
            return bool(self._live_execution_policy_cfg.get("enabled", False))
        return bool(self._legacy_execution_policy_cfg)

    def _execution_mode(self) -> str:
        raw = (
            os.getenv("POLY_LIVE_EXECUTION_MODE")
            or self._live_execution_policy_cfg.get("execution_mode")
            or self._legacy_execution_policy_cfg.get("mode")
            or "live"
        )
        return str(raw).strip().lower().replace("-", "_")

    def _policy_error_code(self) -> str:
        if self._live_execution_policy_cfg:
            return "LIVE_POLICY_REJECT"
        return "ORDER_POLICY_REJECT"

    def _resolve_policy_allowed_token_ids(self, key: str) -> Set[str]:
        configured = self._live_execution_policy_cfg.get(key)
        if isinstance(configured, list) and configured:
            return {str(t).strip() for t in configured if str(t).strip()}
        configured_targets = self.config.get("target_assets")
        if isinstance(configured_targets, list):
            return {str(t).strip() for t in configured_targets if str(t).strip()}
        return set()

    def _validate_live_execution_policy(
        self,
        *,
        token_id: str,
        strategy: Optional[str],
        qty: float,
        price: float,
        policy_context: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if self.mode != "LIVE" or not self._live_execution_policy_enabled():
            return None

        ctx = policy_context if isinstance(policy_context, dict) else {}
        required_fields = ("intent_source", "strategy_id", "approval_mode", "session_id", "reason_code")
        missing = [field for field in required_fields if not str(ctx.get(field) or "").strip()]
        error_code = self._policy_error_code()
        if missing and self._live_execution_policy_cfg:
            return {
                "status": "error",
                "error_code": error_code,
                "error": f"live execution policy reject: missing policy context fields {','.join(missing)}",
            }

        mode = self._execution_mode()
        intent_source = str(ctx.get("intent_source") or "").strip().lower()
        approval_mode = str(ctx.get("approval_mode") or "").strip().lower()
        notional = float(price) * float(qty)

        if self._legacy_execution_policy_cfg:
            legacy_cfg = self._legacy_execution_policy_cfg
            if mode == "soak_only":
                if not intent_source or not approval_mode:
                    return {
                        "status": "error",
                        "error_code": error_code,
                        "error": "order execution policy reject: missing soak provenance",
                    }
                allowed_sources = {
                    str(s).strip().lower()
                    for s in (legacy_cfg.get("allowed_intent_sources") or [])
                    if str(s).strip()
                }
                if allowed_sources and intent_source not in allowed_sources:
                    return {
                        "status": "error",
                        "error_code": error_code,
                        "error": f"order execution policy reject: source '{intent_source}' not allowed",
                    }
                allowed_approval_modes = {
                    str(s).strip().lower()
                    for s in (legacy_cfg.get("allowed_approval_modes") or [])
                    if str(s).strip()
                }
                if allowed_approval_modes and approval_mode not in allowed_approval_modes:
                    return {
                        "status": "error",
                        "error_code": error_code,
                        "error": f"order execution policy reject: approval_mode '{approval_mode}' not allowed",
                    }
                if bool(legacy_cfg.get("require_operator_reason")) and not str(ctx.get("operator_reason") or "").strip():
                    return {
                        "status": "error",
                        "error_code": error_code,
                        "error": "order execution policy reject: operator_reason required",
                    }
                return None

            if mode == "approved_live" and bool(legacy_cfg.get("require_provenance")):
                legacy_required = ("intent_source", "strategy_id", "approval_mode", "session_id")
                missing_legacy = [field for field in legacy_required if not str(ctx.get(field) or "").strip()]
                if missing_legacy:
                    return {
                        "status": "error",
                        "error_code": error_code,
                        "error": f"order execution policy reject: missing provenance {','.join(missing_legacy)}",
                    }
                return None

        if mode == "soak_only":
            if approval_mode != "diagnostic":
                return {
                    "status": "error",
                    "error_code": error_code,
                    "error": "live execution policy reject: soak_only requires diagnostic approval",
                }
            allowed_sources = {
                str(s).strip().lower()
                for s in (self._live_execution_policy_cfg.get("diagnostic_allowed_sources") or [])
                if str(s).strip()
            }
            if allowed_sources and intent_source not in allowed_sources:
                return {
                    "status": "error",
                    "error_code": error_code,
                    "error": f"live execution policy reject: source '{intent_source}' not allowlisted",
                }
            allowed_tokens = self._resolve_policy_allowed_token_ids("diagnostic_allowed_token_ids")
            if allowed_tokens and token_id not in allowed_tokens:
                return {
                    "status": "error",
                    "error_code": error_code,
                    "error": f"live execution policy reject: token '{token_id}' not diagnostic-allowlisted",
                }
            max_notional = float(
                self._live_execution_policy_cfg.get("diagnostic_max_order_notional_usd", 0.0) or 0.0
            )
            if max_notional > 0 and notional > max_notional:
                return {
                    "status": "error",
                    "error_code": error_code,
                    "error": f"live execution policy reject: diagnostic notional {notional} exceeds {max_notional}",
                }
            return None

        allowed_sources = {
            str(s).strip().lower()
            for s in (self._live_execution_policy_cfg.get("live_allowed_sources") or [])
            if str(s).strip()
        }
        if allowed_sources and intent_source not in allowed_sources:
            return {
                "status": "error",
                "error_code": error_code,
                "error": f"live execution policy reject: source '{intent_source}' not live-allowlisted",
            }

        allowed_tokens = self._resolve_policy_allowed_token_ids("live_allowed_token_ids")
        if allowed_tokens and token_id not in allowed_tokens:
            return {
                "status": "error",
                "error_code": error_code,
                "error": f"live execution policy reject: token '{token_id}' not live-allowlisted",
            }

        if not str(strategy or "").strip():
            return {
                "status": "error",
                "error_code": error_code,
                "error": "live execution policy reject: strategy is required",
            }
        return None

    def _resolve_allowed_strategies(self) -> Set[str]:
        configured = self._order_guard_cfg.get("allowed_strategies")
        if isinstance(configured, list) and configured:
            return {str(s).strip().lower() for s in configured if str(s).strip()}
        return {"maker", "fee_bearing_maker", "sniper", "combinatorial"}

    def _validate_intent_freshness(
        self,
        intent_metadata: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if self.intent_max_age_sec <= 0:
            return None
        metadata = intent_metadata if isinstance(intent_metadata, dict) else {}
        last_update_ts = metadata.get("last_update_ts")
        try:
            issued_at = float(last_update_ts)
        except (TypeError, ValueError):
            return None
        age_sec = time.time() - issued_at
        if age_sec <= self.intent_max_age_sec:
            return None
        return {
            "status": "error",
            "error_code": "STALE_INTENT",
            "error": f"intent age {age_sec:.3f}s exceeds max {self.intent_max_age_sec:.3f}s",
        }

    def _resolve_allowed_token_ids(self) -> Set[str]:
        env_tokens = os.getenv("POLY_ORDER_GUARD_ALLOWED_TOKENS", "").strip()
        if env_tokens:
            return {t.strip() for t in env_tokens.split(",") if t.strip()}
        configured = self._order_guard_cfg.get("allowed_token_ids")
        if isinstance(configured, list):
            return {str(t).strip() for t in configured if str(t).strip()}
        return set()

    def _resolve_strategy_qty_band(self, strategy: str) -> Tuple[float, float]:
        bands = self._order_guard_cfg.get("strategy_qty_bands", {})
        if isinstance(bands, dict):
            strategy_band = bands.get(strategy)
            if isinstance(strategy_band, dict):
                min_qty = float(strategy_band.get("min_qty", 0.0) or 0.0)
                max_qty = float(strategy_band.get("max_qty", 0.0) or 0.0)
                if max_qty > 0:
                    return min_qty, max_qty

        if strategy == "maker":
            base = float(self.config.get("maker", {}).get("default_qty", 5.0) or 5.0)
            return 0.0, float(self._order_guard_cfg.get("maker_max_qty", base * 2.0) or (base * 2.0))
        if strategy == "fee_bearing_maker":
            base = float(self.config.get("fee_bearing_maker", {}).get("default_qty", 5.0) or 5.0)
            return 0.0, float(self._order_guard_cfg.get("fee_bearing_maker_max_qty", base * 2.0) or (base * 2.0))
        if strategy == "sniper":
            return 0.0, float(self._order_guard_cfg.get("sniper_max_qty", 200.0) or 200.0)
        if strategy == "combinatorial":
            return 0.0, float(self._order_guard_cfg.get("combinatorial_max_qty", 200.0) or 200.0)
        return 0.0, float(self._order_guard_cfg.get("default_max_qty", 200.0) or 200.0)

    def _validate_live_order_guard(
        self,
        *,
        token_id: str,
        strategy: Optional[str],
        qty: float,
        price: float,
    ) -> Optional[Dict[str, Any]]:
        if self.mode != "LIVE" or not self._order_guard_enabled():
            return None

        strategy_norm = str(strategy or "").strip().lower()
        if not strategy_norm:
            logger.critical("ORDER_GUARD_REJECT: missing strategy token=%s qty=%.6f price=%.6f", token_id, qty, price)
            return {
                "status": "error",
                "error_code": "ORDER_GUARD_REJECT",
                "error": "order guard reject: strategy is required in LIVE mode",
            }

        allowed_strategies = self._resolve_allowed_strategies()
        if strategy_norm not in allowed_strategies:
            logger.critical(
                "ORDER_GUARD_REJECT: strategy=%s not in allowlist token=%s qty=%.6f price=%.6f",
                strategy_norm,
                token_id,
                qty,
                price,
            )
            return {
                "status": "error",
                "error_code": "ORDER_GUARD_REJECT",
                "error": f"order guard reject: strategy '{strategy_norm}' not allowed",
            }

        allowed_token_ids = self._resolve_allowed_token_ids()
        if allowed_token_ids and token_id not in allowed_token_ids:
            logger.critical(
                "ORDER_GUARD_REJECT: token=%s not in allowlist strategy=%s qty=%.6f price=%.6f",
                token_id,
                strategy_norm,
                qty,
                price,
            )
            return {
                "status": "error",
                "error_code": "ORDER_GUARD_REJECT",
                "error": f"order guard reject: token '{token_id}' not allowed",
            }

        min_qty, max_qty = self._resolve_strategy_qty_band(strategy_norm)
        if qty < min_qty:
            logger.critical(
                "ORDER_GUARD_REJECT: qty %.6f < min %.6f strategy=%s token=%s",
                qty,
                min_qty,
                strategy_norm,
                token_id,
            )
            return {
                "status": "error",
                "error_code": "ORDER_GUARD_REJECT",
                "error": f"order guard reject: qty {qty} below min {min_qty} for {strategy_norm}",
            }
        if max_qty > 0 and qty > max_qty:
            logger.critical(
                "ORDER_GUARD_REJECT: qty %.6f > max %.6f strategy=%s token=%s",
                qty,
                max_qty,
                strategy_norm,
                token_id,
            )
            return {
                "status": "error",
                "error_code": "ORDER_GUARD_REJECT",
                "error": f"order guard reject: qty {qty} exceeds max {max_qty} for {strategy_norm}",
            }

        max_notional = float(self._order_guard_cfg.get("max_order_notional_usd", 0.0) or 0.0)
        notional = float(price) * float(qty)
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

    def _append_order_submission_audit(
        self,
        *,
        token_id: str,
        side: str,
        qty: float,
        price: float,
        order_type: str,
        strategy: str,
        order_id: str,
        client_order_id: str,
        policy_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            path = Path(self._order_submission_audit_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": time.time(),
                "mode": self.mode,
                "token_id": token_id,
                "side": side,
                "qty": qty,
                "price": price,
                "order_type": order_type,
                "strategy": strategy,
                "order_id": order_id,
                "client_order_id": client_order_id,
                "policy_context": policy_context or {},
            }
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
        except Exception as exc:
            logger.warning("Failed to append order submission audit: %s", exc)

    def _resolve_gtd_expiration_epoch(self, expiration_sec: Optional[int] = None) -> int:
        requested_window = (
            self.default_expiration_sec if expiration_sec is None else expiration_sec
        )
        try:
            requested_window_int = int(requested_window)
        except (TypeError, ValueError):
            requested_window_int = int(self.default_expiration_sec)
        requested_window_int = max(1, requested_window_int)
        effective_window = max(requested_window_int, self.gtd_min_expiration_lead_sec)
        return int(time.time()) + effective_window + self.gtd_expiration_safety_sec

    def _format_entry_alert(
        self,
        *,
        token_id: str,
        side: str,
        qty: float,
        price: float,
        order_type: str,
        order_id: str,
        mode: str,
        strategy: str,
    ) -> str:
        return (
            f"[Polymarket Entry] mode={mode} strategy={strategy} side={side} qty={qty:.4f} "
            f"price={price:.4f} type={order_type} token={token_id} order_id={order_id}"
        )

    async def _post_entry_alert(
        self,
        *,
        token_id: str,
        side: str,
        qty: float,
        price: float,
        order_type: str,
        order_id: str,
        mode: str,
        strategy: str,
    ) -> None:
        message = self._format_entry_alert(
            token_id=token_id,
            side=side,
            qty=qty,
            price=price,
            order_type=order_type,
            order_id=order_id,
            mode=mode,
            strategy=strategy,
        )
        posted = await post_to_discord("polymarket", message)
        if not posted:
            logger.debug("Polymarket entry Discord alert was not posted")

    @staticmethod
    def _on_background_task_done(task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception as exc:
            logger.debug(f"Background alert task failed: {exc}")

    def _fire_and_forget_entry_alert(
        self,
        *,
        token_id: str,
        side: str,
        qty: float,
        price: float,
        order_type: str,
        order_id: str,
        mode: str,
        strategy: str,
    ) -> None:
        if not self._discord_entry_alerts_enabled():
            return
        task = asyncio.create_task(
            self._post_entry_alert(
                token_id=token_id,
                side=side,
                qty=qty,
                price=price,
                order_type=order_type,
                order_id=order_id,
                mode=mode,
                strategy=strategy,
            )
        )
        task.add_done_callback(self._on_background_task_done)

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

    def normalize_qty(self, qty: float, precision: Optional[int] = None) -> float:
        """Normalize quantity to configured precision using round-down semantics."""
        use_precision = self.qty_precision if precision is None else max(0, int(precision))
        try:
            q_dec = Decimal(str(qty))
            step = Decimal("1").scaleb(-use_precision)
            return float(q_dec.quantize(step, rounding=ROUND_DOWN))
        except (InvalidOperation, ValueError, TypeError):
            return 0.0

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
                    "error": f"Quantity precision invalid: {qty} (precision={self.qty_precision})",
                }

        return {"ok": True, "normalized_price": norm_price, "normalized_qty": norm_qty}

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
        side_val = side.upper()
        if side_val not in ("BUY", "SELL"):
            return {"status": "error", "error": f"Invalid side: {side}"}

        requested_order_type = order_type.upper()
        order_type = LEGACY_ORDER_TYPE_ALIASES.get(requested_order_type, requested_order_type)
        if order_type not in VALID_ORDER_TYPES:
            return {"status": "error", "error": f"Invalid order_type: {requested_order_type}"}
        if requested_order_type != order_type:
            logger.info("Order type alias normalized: %s -> %s", requested_order_type, order_type)

        # 1. Resolve Metadata
        meta = await self.get_market_metadata(token_id)
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

        freshness_error = self._validate_intent_freshness(intent_metadata)
        if freshness_error:
            return freshness_error

        # 3. Idempotency Key (Client Order ID)
        if not client_order_id:
            client_order_id = f"dacle_{int(time.time() * 1000)}"

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
            logger.critical(
                "LIVE_POLICY_REJECT: token=%s strategy=%s qty=%.6f price=%.6f ctx=%s",
                token_id,
                strategy,
                qty_norm,
                norm_price,
                policy_context or {},
            )
            return policy_error

        # 4. Shadow Mode Bypass
        if self._is_execution_shadowed():
            execution_mode = "DRY_RUN_ADMIT" if self._dry_run_admit_enabled() else "SHADOW"
            order_prefix = "dryrun" if self._dry_run_admit_enabled() else "shadow"
            logger.info(
                "[%s] Post Order: %s %s @ %s (token=%s, type=%s)",
                execution_mode,
                side_val,
                qty_norm,
                norm_price,
                token_id,
                order_type,
            )
            result = {
                "status": "success",
                "shadow": self._is_shadow_mode(),
                "dry_run_admit": self._dry_run_admit_enabled(),
                "order_id": f"{order_prefix}_{client_order_id}",
                "client_order_id": client_order_id,
                "filled_qty": 0.0,
                "filled_price": 0.0
            }
            if entry_alert:
                self._fire_and_forget_entry_alert(
                    token_id=token_id,
                    side=side_val,
                    qty=qty_norm,
                    price=norm_price,
                    order_type=order_type,
                    order_id=str(result["order_id"]),
                    mode=execution_mode,
                    strategy=str(strategy or "unknown"),
                )
            return result

        # 4b. Paper Mode: place real order at $0.01 POST_ONLY+GTD, then cancel immediately
        if self.mode == "PAPER":
            paper_price = 0.01
            norm_paper = self.normalize_price(paper_price, meta["tick_size"], side_val)
            logger.info(f"[PAPER] POST_ONLY order: {side_val} {qty_norm} @ {norm_paper} (token={token_id})")
            exp = self._resolve_gtd_expiration_epoch(30)
            paper_args = OrderArgs(price=norm_paper, size=qty_norm, side=side_val, token_id=token_id, expiration=exp)
            try:
                signed = await asyncio.to_thread(self.client.create_order, paper_args)
                async with self._exec_semaphore:
                    resp = await asyncio.to_thread(self.client.post_order, signed, orderType=OrderType.GTD)
                if resp and resp.get("success"):
                    paper_order_id = resp.get("orderID")
                    logger.info(f"[PAPER] Order placed: {paper_order_id}. Cancelling immediately...")
                    await asyncio.sleep(0.5)
                    await self.cancel_order(paper_order_id)
                    if entry_alert:
                        self._fire_and_forget_entry_alert(
                            token_id=token_id,
                            side=side_val,
                            qty=qty_norm,
                            price=norm_paper,
                            order_type="GTD",
                            order_id=str(paper_order_id),
                            mode="PAPER",
                            strategy=str(strategy or "unknown"),
                        )
                    return {"status": "success", "paper": True, "order_id": paper_order_id, "client_order_id": client_order_id}
                else:
                    return {"status": "error", "error": resp.get("errorMsg", "Paper order failed"), "error_code": "PAPER_REJECT"}
            except Exception as e:
                return {"status": "error", "error": str(e), "error_code": "PAPER_EXCEPTION"}

        # 5. Expiration
        if order_type == "GTD":
            exp = self._resolve_gtd_expiration_epoch(expiration_sec)
        else:
            # GTC, FOK, FAK must have expiration 0
            exp = 0

        # 6. Build Order Arguments
        order_args = OrderArgs(
            price=norm_price,
            size=qty_norm,
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
            async with self._exec_semaphore:
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
                order_id = resp.get("orderID")
                strategy_for_audit = str(strategy or "unknown")
                self._append_order_submission_audit(
                    token_id=token_id,
                    side=side_val,
                    qty=qty_norm,
                    price=norm_price,
                    order_type=order_type,
                    strategy=strategy_for_audit,
                    order_id=str(order_id),
                    client_order_id=client_order_id,
                    policy_context=policy_context,
                )
                if entry_alert:
                    self._fire_and_forget_entry_alert(
                        token_id=token_id,
                        side=side_val,
                        qty=qty_norm,
                        price=norm_price,
                        order_type=order_type,
                        order_id=str(order_id),
                        mode=self.mode,
                        strategy=strategy_for_audit,
                    )
                return {
                    "status": "success",
                    "order_id": order_id,
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

    async def get_order_status(self, order_id: str) -> Optional[str]:
        """Fetch order status for verification/recovery flows."""
        if self._is_execution_shadowed():
            return "FILLED"

        getter = getattr(self.client, "get_order", None)
        if not callable(getter):
            return None

        try:
            resp = await asyncio.to_thread(getter, order_id)
            if isinstance(resp, dict):
                status = resp.get("status")
                return str(status).upper() if status else None
            return None
        except Exception as e:
            logger.error(f"Failed to query order status for {order_id}: {e}")
            return None

    async def get_order_fills(self, order_id: str) -> List[Dict[str, Any]]:
        """
        Fetch individual trade records for a specific order ID.
        Used for reconciliation when WebSocket messages are missed.
        """
        if self._is_execution_shadowed():
            return []

        getter = getattr(self.client, "get_trades", None)
        if not callable(getter):
            return []

        try:
            # Query trades by order ID
            resp = await asyncio.to_thread(getter, order_id=order_id)
            if isinstance(resp, list):
                return resp
            if isinstance(resp, dict):
                for key in ("data", "trades", "items", "results"):
                    value = resp.get(key)
                    if isinstance(value, list):
                        return value
            return []
        except Exception as e:
            logger.error(f"Failed to query fills for order {order_id}: {e}")
            return []

    async def get_open_orders(self) -> List[Dict[str, Any]]:
        """Fetch open orders when the underlying client supports it."""
        if self._is_execution_shadowed():
            return []

        getter = getattr(self.client, "get_open_orders", None)
        if callable(getter):
            try:
                resp = await asyncio.to_thread(getter)
                if isinstance(resp, list):
                    return resp
                if isinstance(resp, dict):
                    for key in ("data", "orders", "items", "results"):
                        value = resp.get(key)
                        if isinstance(value, list):
                            return value
            except Exception as e:
                logger.warning(f"get_open_orders failed: {e}")
            return []

        getter = getattr(self.client, "get_orders", None)
        if callable(getter):
            try:
                resp = await asyncio.to_thread(getter)
                if isinstance(resp, list):
                    return resp
                if isinstance(resp, dict):
                    for key in ("data", "orders", "items", "results"):
                        value = resp.get(key)
                        if isinstance(value, list):
                            return value
            except Exception as e:
                logger.warning(f"get_orders failed while fetching open orders: {e}")

        return []

    async def fetch_open_orders(self) -> List[Dict[str, Any]]:
        """Alias for get_open_orders to support legacy/standardized naming."""
        return await self.get_open_orders()

    async def cancel_order(self, order_id: str) -> bool:
        if self._is_execution_shadowed(): return True
        try:
            async with self._exec_semaphore:
                resp = await asyncio.to_thread(self.client.cancel, order_id)
            return bool(resp and resp.get("success"))
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def cancel_all_orders(self) -> bool:
        """Cancel all open orders for the funder address."""
        if self._is_execution_shadowed():
            logger.info("[SHADOW] cancel_all_orders requested")
            return True
        try:
            # Note: py_clob_client has cancel_all() method
            resp = await asyncio.to_thread(self.client.cancel_all)
            return bool(resp and resp.get("success"))
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
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
            url = f"{self.base_url}{request_path}?asset_type=CONDITIONAL&token_id={token_id}&signature_type={sig_type}"
        else:
            url = f"{self.base_url}{request_path}?asset_type=COLLATERAL&signature_type={sig_type}"

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
        url = f"{self.base_url}{request_path}?asset_type=COLLATERAL&signature_type={sig_type}"
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
                error_type = "auth" if resp.status_code in {401, 403} else "http"
                return {
                    "balance_usdc": 0.0,
                    "allowance_usdc": 0.0,
                    "ok": False,
                    "error": resp.text[:200],
                    "status_code": resp.status_code,
                    "error_type": error_type,
                }
        except Exception as e:
            logger.error(f"get_usdc_balance_and_allowance failed: {e}")
            message = str(e)
            error_type = "timeout" if "timed out" in message.lower() else "transport"
            return {
                "balance_usdc": 0.0,
                "allowance_usdc": 0.0,
                "ok": False,
                "error": message,
                "status_code": None,
                "error_type": error_type,
            }

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
