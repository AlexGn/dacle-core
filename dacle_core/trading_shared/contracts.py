"""
DACLE shared trading contracts.

Canonical home for contract primitives consumed by the shared trading layer
(src/trading_shared/*) so the dependency graph stays one-way:
pillar -> shared, never shared -> pillar.

Symbols here may be re-exported by pillar-specific contracts modules
(e.g. src/lighter/contracts.py) for backward compatibility, but the
canonical definition lives here.

Moved from src/lighter/contracts.py during Phase 1 pillar decoupling.
"""

from __future__ import annotations

from enum import Enum

# --- Redis keys (shared across pillars) ---
KEY_GLOBAL_EXPOSURE_CENTS_V1 = "scalper:global_exposure_cents:v1"


class GateRejectCode(str, Enum):
    """Risk-gate outcomes in the daemon."""

    CLEAR = "CLEAR"
    RISK_MODE_BLOCKED = "RISK_MODE_BLOCKED"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    NO_PERMISSION_LOADED = "NO_PERMISSION_LOADED"
    CIRCUIT_BREAKER_OPEN = "CIRCUIT_BREAKER_OPEN"
    PERMISSION_STALE = "PERMISSION_STALE"
    LONG_PROHIBITED = "LONG_PROHIBITED"
    SHORT_PROHIBITED = "SHORT_PROHIBITED"
    EXECUTION_IN_PROGRESS = "EXECUTION_IN_PROGRESS"
    SYNC_DEGRADED = "SYNC_DEGRADED"
    GLOBAL_EXPOSURE_CAP_EXCEEDED = "GLOBAL_EXPOSURE_CAP_EXCEEDED"
    ACCOUNT_STATE_UNKNOWN = "ACCOUNT_STATE_UNKNOWN"
    ACCOUNT_EXPOSURE_MISMATCH = "ACCOUNT_EXPOSURE_MISMATCH"
    CONTEXT_STALE = "CONTEXT_STALE"
    CRASH_VETO = "CRASH_VETO"
    BVG_FAILED = "BVG_FAILED"
    WS_DEGRADED_EXIT_ONLY = "WS_DEGRADED_EXIT_ONLY"
    EXIT_AUTH_FAILED = "EXIT_AUTH_FAILED"
