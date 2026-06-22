"""DACLE Sovereign Wrapper — contracts.

Tier enum, reason codes, decision dataclass, Redis key constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel, Field

KEY_SOVEREIGN_KILL = "sovereign:kill:v1"
KEY_SOVEREIGN_TIER = "sovereign:tier:v1"
KEY_SOVEREIGN_OVERRIDE = "sovereign:override:v1"
KEY_SOVEREIGN_GAP_PROTECTION = "sovereign:gap_protection_active:v1"
CHAN_SOVEREIGN_EVENTS = "sovereign:events"


class SovereignTier(str, Enum):
    R0 = "R0"  # SHADOW: logging only, $0 cap
    R1 = "R1"  # SHADOW: shadow execution, $0 cap
    R2 = "R2"  # DRY_RUN: dry-run admit, 1.0x cap
    R3 = "R3"  # DRY_RUN: dry-run admit, 1.25x cap
    R4 = "R4"  # SOAK: soak only, 1.5x cap
    R5 = "R5"  # LIVE: live execution, 1.5x cap
    R6 = "R6"  # FULL_LIVE: live execution, 2.0x cap

    @staticmethod
    def map_from_polymarket_tier(poly_tier: str) -> "SovereignTier":
        token = (poly_tier or "").strip().upper()
        for tier in SovereignTier:
            if tier.value == token:
                return tier
        return SovereignTier.R0

    @staticmethod
    def map_from_lighter_mode(mode: str) -> "SovereignTier":
        m = (mode or "").strip().upper()
        return {
            "DISABLED": SovereignTier.R0,
            "PAPER": SovereignTier.R1,
            "PROBE": SovereignTier.R2,
            "CANARY": SovereignTier.R3,
            "BOUNDED_LIVE": SovereignTier.R5,
            "FULL_LIVE": SovereignTier.R6,
        }.get(m, SovereignTier.R0)

    def at_least(self, other: "SovereignTier") -> bool:
        order = list(SovereignTier)
        return order.index(self) >= order.index(other)


class SovereignReasonCode(str, Enum):
    APPROVED = "APPROVED"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    POSITION_LIMIT_EXCEEDED = "POSITION_LIMIT_EXCEEDED"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    GAP_PROTECTION_ACTIVE = "GAP_PROTECTION_ACTIVE"
    TIME_STOP_EXCEEDED = "TIME_STOP_EXCEEDED"
    TIER_INSUFFICIENT = "TIER_INSUFFICIENT"
    SOVEREIGN_REDIS_UNAVAILABLE = "SOVEREIGN_REDIS_UNAVAILABLE"
    SOVEREIGN_AUDIT_WRITE_FAILED = "SOVEREIGN_AUDIT_WRITE_FAILED"
    SOVEREIGN_CONFIG_INVALID = "SOVEREIGN_CONFIG_INVALID"
    SOVEREIGN_INTERNAL_ERROR = "SOVEREIGN_INTERNAL_ERROR"
    OPERATOR_OVERRIDE_ALLOWED = "OPERATOR_OVERRIDE_ALLOWED"


class SovereignImmutableError(Exception):
    """Raised when code attempts to mutate sovereign-immutable config at runtime."""


@dataclass
class SovereignDecision:
    approved: bool
    reason_code: SovereignReasonCode
    pillar: str
    intent_id: str
    gate_results: Dict[str, str] = field(default_factory=dict)
    override_applied: bool = False
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "reason_code": self.reason_code.value,
            "pillar": self.pillar,
            "intent_id": self.intent_id,
            "gate_results": self.gate_results,
            "override_applied": self.override_applied,
            "ts": self.ts,
        }


class KillSwitch(BaseModel):
    """
    Immediate Halt Contract.
    Written by: OpenClaw (Discord) or Safety Supervisor.
    Read by: Scalper Daemon (Hot Loop).
    """

    active: bool = False
    ts: datetime = Field(default_factory=datetime.utcnow)
    actor: str
    reason: str
    ttl_sec: int = 3600
