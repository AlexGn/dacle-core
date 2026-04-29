"""Sovereign Wrapper — cross-pillar admission gate.

Hot-path gate sequence on every validate_intent() call:
  kill switch → position limits → rate limits → gap protection → time-stop → override

Fail-closed: any RedisError or internal exception → REJECT.
Shadow mode (enabled=false): evaluates gates, always APPROVES, logs warnings.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from src.governance.aoat import AuditTrail
from src.governance.contracts import (
    CHAN_SOVEREIGN_EVENTS,
    KEY_SOVEREIGN_KILL,
    SovereignDecision,
    SovereignImmutableError,
    SovereignReasonCode,
    SovereignTier,
)
from src.governance.gap_protection import GapProtector
from src.governance.kill_switch import SovereignKillSwitch
from src.governance.override import OperatorOverride
from src.governance.position_limits import PositionLimitGuard, PositionLimits, load_position_limits

logger = logging.getLogger(__name__)


class SovereignConfig:
    """Immutable config loaded once from config/sovereign.yaml."""

    def __init__(self, path: str = "config/sovereign.yaml"):
        with open(path, "r") as f:
            raw = yaml.safe_load(f)
        sovereign = raw.get("sovereign", {})
        self.enabled: bool = sovereign.get("enabled", False)
        self.tier: SovereignTier = SovereignTier(sovereign.get("tier", "R3"))
        ks = sovereign.get("kill_switch", {})
        self.kill_redis_key: str = ks.get("redis_key", KEY_SOVEREIGN_KILL)
        self.kill_pubsub_channel: str = ks.get("pubsub_channel", CHAN_SOVEREIGN_EVENTS)
        self.kill_default_ttl_sec: int = ks.get("default_ttl_sec", 3600)
        gp = sovereign.get("gap_protection", {})
        self.gap_threshold_pct: float = float(gp.get("threshold_pct", 0.03))
        self.gap_window_sec: int = int(gp.get("window_seconds", 300))
        self.gap_flag_ttl_sec: int = int(gp.get("redis_flag_ttl_sec", 600))
        ts = sovereign.get("time_stop", {})
        self.time_stop_enabled: bool = ts.get("enabled", True)
        self.time_stop_hours: float = float(ts.get("timeout_hours", 2.0))
        rl = sovereign.get("rate_limits", {})
        self.rate_limit_enabled: bool = rl.get("enabled", True)
        self.rate_limit_per_minute: int = int(rl.get("per_minute", 60))
        self.position_limits: PositionLimits = load_position_limits(path)


class SovereignWrapper:
    """Cross-pillar admission gate with fail-closed semantics.

    Usage:
        wrapper = SovereignWrapper(config_path="config/sovereign.yaml")
        decision = await wrapper.validate_intent(intent_dict)
        if not decision.approved:
            logger.warning("SOVEREIGN REJECT: %s", decision.reason_code)
    """

    def __init__(
        self,
        config_path: str = "config/sovereign.yaml",
        redis: Optional[Any] = None,
        audit_path: str = "data/audit/sovereign_decisions.jsonl",
    ):
        self.config = SovereignConfig(config_path)
        self._redis = redis
        self._kill_switch = SovereignKillSwitch(redis) if redis else None
        self._position_guard = PositionLimitGuard(self.config.position_limits)
        self._gap_protector = GapProtector(
            threshold_pct=self.config.gap_threshold_pct,
            window_seconds=self.config.gap_window_sec,
            redis=redis,
            redis_flag_ttl_sec=self.config.gap_flag_ttl_sec,
        )
        self._override = OperatorOverride(redis=redis)
        self._audit = AuditTrail(audit_path)
        self._rate_bucket: list = []
        self._shadow_mode = not self.config.enabled
        # Env override: SOVEREIGN_ENABLED=1 forces enabled regardless of YAML
        env_flag = os.environ.get("SOVEREIGN_ENABLED", "").strip().lower()
        if env_flag in ("1", "true", "yes"):
            self._shadow_mode = False
        elif env_flag in ("0", "false", "no"):
            self._shadow_mode = True

    @property
    def enabled(self) -> bool:
        return not self._shadow_mode

    @property
    def shadow_mode(self) -> bool:
        return self._shadow_mode

    @property
    def gap_protector(self) -> GapProtector:
        return self._gap_protector

    def _check_rate_limit(self) -> bool:
        """Simple sliding-window rate limit (in-memory, per-process)."""
        if not self.config.rate_limit_enabled:
            return True
        now = time.time()
        cutoff = now - 60.0
        self._rate_bucket = [t for t in self._rate_bucket if t > cutoff]
        if len(self._rate_bucket) >= self.config.rate_limit_per_minute:
            return False
        self._rate_bucket.append(now)
        return True

    async def _check_kill_switch(self) -> Optional[SovereignReasonCode]:
        """Check kill switch. Returns None if clear, or reason code if active."""
        if self._kill_switch is None:
            return None
        try:
            active = await self._kill_switch.is_active()
            if active:
                return SovereignReasonCode.KILL_SWITCH_ACTIVE
        except Exception as e:
            logger.error("SOVEREIGN kill switch check failed: %s", e)
            return SovereignReasonCode.KILL_SWITCH_ACTIVE  # fail-closed
        return None

    def _check_position_limits(
        self, intent: Dict[str, Any]
    ) -> Optional[SovereignReasonCode]:
        """Check position limits. Returns None if clear."""
        notional = float(intent.get("notional_usd", 0) or 0)
        current_total = float(intent.get("_current_total_notional_usd", 0) or 0)
        ok, msg = self._position_guard.check_total_notional(current_total, notional)
        if not ok:
            logger.warning("SOVEREIGN position limit: %s", msg)
            return SovereignReasonCode.POSITION_LIMIT_EXCEEDED
        count = int(intent.get("_current_open_positions", 0) or 0)
        ok2, msg2 = self._position_guard.check_position_count(count)
        if not ok2:
            logger.warning("SOVEREIGN position count: %s", msg2)
            return SovereignReasonCode.POSITION_LIMIT_EXCEEDED
        daily_loss = float(intent.get("_current_daily_loss_usd", 0) or 0)
        ok3, msg3 = self._position_guard.check_daily_loss(daily_loss)
        if not ok3:
            logger.warning("SOVEREIGN daily loss: %s", msg3)
            return SovereignReasonCode.POSITION_LIMIT_EXCEEDED
        return None

    def _check_gap_protection(self, intent: Dict[str, Any]) -> Optional[SovereignReasonCode]:
        """Check gap protection. Returns None if clear."""
        market_id = intent.get("market_id") or intent.get("_market_id")
        if market_id and self._gap_protector.is_gap_active_local():
            return SovereignReasonCode.GAP_PROTECTION_ACTIVE
        return None

    def _check_time_stop(self, intent: Dict[str, Any]) -> Optional[SovereignReasonCode]:
        """Check time-stop (2h default). Returns None if clear."""
        if not self.config.time_stop_enabled:
            return None
        entry_ts = intent.get("_entry_time")
        if entry_ts is None:
            return None
        if isinstance(entry_ts, str):
            try:
                from datetime import datetime, timezone
                entry_ts = datetime.fromisoformat(entry_ts).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return None
        if hasattr(entry_ts, "total_seconds"):
            pass  # timedelta — not applicable
        elif hasattr(entry_ts, "__sub__"):
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            elapsed = (now - entry_ts).total_seconds() / 3600.0
            if elapsed > self.config.time_stop_hours:
                return SovereignReasonCode.TIME_STOP_EXCEEDED
        return None

    async def _check_override(self, pillar: str, intent_id: str) -> bool:
        """Check for a valid operator override. Returns True if override found and valid."""
        try:
            token = await self._override.fetch_pending()
            if token is None:
                return False
            if not self._override.validate_override(token, pillar, intent_id):
                return False
            # Consume override (delete from Redis)
            await self._override.revoke_override(actor="sovereign_wrapper")
            return True
        except Exception as e:
            logger.error("SOVEREIGN override check failed: %s", e)
            return False

    async def validate_intent(self, intent: Dict[str, Any]) -> SovereignDecision:
        """Main gate: evaluate all checks and return a decision.

        In shadow mode (enabled=false), all gates are evaluated but the
        decision is always APPROVED with any warnings logged.
        In production mode, any REJECT is final unless an override exists.
        """
        pillar = intent.get("pillar", "unknown")
        intent_id = intent.get("intent_id") or intent.get("_intent_id", "unknown")
        gate_results: Dict[str, str] = {}
        override_applied = False

        # --- Gate sequence ---
        # 1. Kill switch
        kill_reason = await self._check_kill_switch()
        if kill_reason:
            gate_results["kill_switch"] = kill_reason.value
        # 2. Position limits
        pos_reason = self._check_position_limits(intent)
        if pos_reason:
            gate_results["position_limits"] = pos_reason.value
        # 3. Rate limit
        if not self._check_rate_limit():
            gate_results["rate_limit"] = SovereignReasonCode.RATE_LIMIT_EXCEEDED.value
        # 4. Gap protection
        gap_reason = self._check_gap_protection(intent)
        if gap_reason:
            gate_results["gap_protection"] = gap_reason.value
        # 5. Time-stop
        ts_reason = self._check_time_stop(intent)
        if ts_reason:
            gate_results["time_stop"] = ts_reason.value

        rejected = len(gate_results) > 0

        # 6. Override check (only if rejected)
        if rejected:
            has_override = await self._check_override(pillar, str(intent_id))
            if has_override:
                override_applied = True

        # Build decision
        if self._shadow_mode:
            # Shadow mode: always APPROVE, but log any gates that would have blocked
            if rejected:
                logger.warning(
                    "SOVEREIGN SHADOW: would reject %s/%s — gates: %s",
                    pillar, intent_id, gate_results,
                )
            decision = SovereignDecision(
                approved=True,
                reason_code=SovereignReasonCode.APPROVED,
                pillar=pillar,
                intent_id=str(intent_id),
                gate_results=gate_results,
                override_applied=False,
            )
        elif rejected and override_applied:
            decision = SovereignDecision(
                approved=True,
                reason_code=SovereignReasonCode.OPERATOR_OVERRIDE_ALLOWED,
                pillar=pillar,
                intent_id=str(intent_id),
                gate_results=gate_results,
                override_applied=True,
            )
            logger.warning(
                "SOVEREIGN OVERRIDE: %s/%s — override applied for gates: %s",
                pillar, intent_id, gate_results,
            )
        elif rejected:
            # Fail-closed: reject
            first_reason = list(gate_results.values())[0]
            decision = SovereignDecision(
                approved=False,
                reason_code=SovereignReasonCode(first_reason),
                pillar=pillar,
                intent_id=str(intent_id),
                gate_results=gate_results,
                override_applied=False,
            )
            logger.warning(
                "SOVEREIGN REJECT: %s/%s — reason: %s gates: %s",
                pillar, intent_id, first_reason, gate_results,
            )
        else:
            decision = SovereignDecision(
                approved=True,
                reason_code=SovereignReasonCode.APPROVED,
                pillar=pillar,
                intent_id=str(intent_id),
                gate_results=gate_results,
                override_applied=False,
            )

        # Fire-and-forget audit
        try:
            self._audit.record_decision(decision)
        except Exception as e:
            logger.error("SOVEREIGN audit write failed: %s", e)
            if not self._shadow_mode:
                # In production mode, audit failure is a hard error (fail-closed)
                return SovereignDecision(
                    approved=False,
                    reason_code=SovereignReasonCode.SOVEREIGN_AUDIT_WRITE_FAILED,
                    pillar=pillar,
                    intent_id=str(intent_id),
                    gate_results=gate_results,
                )

        return decision