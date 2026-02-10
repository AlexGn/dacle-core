"""
Unified Severity Taxonomy — Session 399b

Single source of truth for severity/health/urgency levels across DACLE.

Problem: 6+ separate severity systems with inconsistent definitions:
  - System Health: CRITICAL / DEGRADED / HEALTHY (strings)
  - Learning Health: HealthStatus enum (CRITICAL/DEGRADED/HEALTHY/UNKNOWN)
  - Alert Decision: URGENT / STANDARD / LOW + 30 suppression reasons
  - Exception Handler: 10 categories (no severity gradation)
  - VPS Health: binary + warnings (no enum)
  - Multi-Tier Alerting: URGENT / STANDARD / LOW_PRIORITY / SKIP

Solution: Three orthogonal enums that compose to cover all use cases.

Usage:
    from src.utils.severity import Severity, Urgency, Autonomy

    # Health checks
    status = Severity.from_health_string("DEGRADED")

    # Alert routing
    urgency = Urgency.from_alert_tier("URGENT")

    # Execution decisions
    autonomy = Autonomy.from_permission_tier("CONFIRM_FIRST")
"""

from enum import IntEnum, Enum
from typing import Dict, Optional, Tuple


class Severity(IntEnum):
    """
    Base severity level — used for all health/status reporting.

    Ordered by severity (lower value = more severe), so comparisons work:
        if severity <= Severity.DEGRADED: send_alert()

    Replaces:
      - system_health.py: "CRITICAL" / "DEGRADED" / "HEALTHY" strings
      - learning_loop_health.py: HealthStatus enum
      - vps_health_monitor.py: binary + warnings
      - BlockerResult.severity: "CRITICAL" / "WARNING" / "INFO" strings
    """
    CRITICAL = 0   # System broken, requires immediate intervention
    DEGRADED = 1   # Partial failure, functioning with reduced capability
    WARNING = 2    # Approaching threshold, monitor closely
    HEALTHY = 3    # Operating normally
    UNKNOWN = 4    # Cannot determine (data missing, service unreachable)

    @classmethod
    def from_health_string(cls, s: str) -> "Severity":
        """Map legacy health status strings to Severity."""
        _MAP = {
            "CRITICAL": cls.CRITICAL,
            "DEGRADED": cls.DEGRADED,
            "WARNING": cls.WARNING,
            "HEALTHY": cls.HEALTHY,
            "UNKNOWN": cls.UNKNOWN,
            # learning_loop_health.py uses lowercase
            "critical": cls.CRITICAL,
            "degraded": cls.DEGRADED,
            "warning": cls.WARNING,
            "healthy": cls.HEALTHY,
            "unknown": cls.UNKNOWN,
            # BlockerResult uses INFO
            "INFO": cls.HEALTHY,
        }
        return _MAP.get(s, cls.UNKNOWN)

    @classmethod
    def from_alert_tier(cls, tier: str) -> "Severity":
        """Map alert tier strings (URGENT/STANDARD/LOW) to Severity."""
        _MAP = {
            "URGENT": cls.CRITICAL,
            "STANDARD": cls.WARNING,
            "LOW": cls.HEALTHY,
        }
        return _MAP.get(tier, cls.UNKNOWN)

    @classmethod
    def from_blocker_severity(cls, s: str) -> "Severity":
        """Map BlockerResult severity strings (base_scorer.py) to Severity."""
        _MAP = {
            "CRITICAL": cls.CRITICAL,
            "WARNING": cls.WARNING,
            "INFO": cls.HEALTHY,
        }
        return _MAP.get(s, cls.UNKNOWN)

    def is_actionable(self) -> bool:
        """True if this severity requires action (CRITICAL or DEGRADED)."""
        return self <= Severity.DEGRADED

    @property
    def emoji(self) -> str:
        return {
            self.CRITICAL: "🔴",
            self.DEGRADED: "🟠",
            self.WARNING: "🟡",
            self.HEALTHY: "🟢",
            self.UNKNOWN: "⚪",
        }[self]

    @property
    def label(self) -> str:
        return self.name


class Urgency(IntEnum):
    """
    Alert routing urgency — determines notification channel and timing.

    Replaces:
      - AlertDecision.tier: "URGENT" / "STANDARD" / "LOW"
      - Multi-tier alerting: URGENT / STANDARD / LOW_PRIORITY / SKIP
    """
    IMMEDIATE = 0  # Send now, all channels (Telegram + Discord)
    STANDARD = 1   # Send within normal cycle (next check)
    DELAYED = 2    # Batch for daily summary
    SKIP = 3       # Suppress entirely

    @classmethod
    def from_alert_tier(cls, tier: str) -> "Urgency":
        """Map legacy alert tier strings to Urgency."""
        _MAP = {
            "URGENT": cls.IMMEDIATE,
            "STANDARD": cls.STANDARD,
            "LOW": cls.DELAYED,
            "LOW_PRIORITY": cls.DELAYED,
            "SKIP": cls.SKIP,
        }
        return _MAP.get(tier, cls.SKIP)

    @classmethod
    def from_severity(cls, severity: "Severity") -> "Urgency":
        """Derive urgency from severity level."""
        _MAP = {
            Severity.CRITICAL: cls.IMMEDIATE,
            Severity.DEGRADED: cls.STANDARD,
            Severity.WARNING: cls.DELAYED,
            Severity.HEALTHY: cls.SKIP,
            Severity.UNKNOWN: cls.STANDARD,
        }
        return _MAP.get(severity, cls.STANDARD)

    @property
    def label(self) -> str:
        return self.name


class Autonomy(IntEnum):
    """
    Execution autonomy level — how much human confirmation is needed.

    Replaces:
      - AlertDecision.permission_tier: SKIP / WATCHLIST / ALERT_ONLY / CONFIRM_FIRST / MICRO_AUTO / STANDARD_AUTO
      - DecisionLevel: EXECUTE / HIGH_CONVICTION / ACCUMULATE / WATCHLIST / SKIP / BLOCKED
    """
    BLOCKED = 0       # Cannot proceed, veto condition active
    ALERT_ONLY = 1    # Notify human, no action taken
    CONFIRM_FIRST = 2 # Propose action, wait for human approval
    AUTO = 3          # Execute automatically within guardrails

    @classmethod
    def from_permission_tier(cls, tier: str) -> "Autonomy":
        """Map legacy permission tier strings to Autonomy."""
        _MAP = {
            "SKIP": cls.BLOCKED,
            "WATCHLIST": cls.BLOCKED,
            "ALERT_ONLY": cls.ALERT_ONLY,
            "CONFIRM_FIRST": cls.CONFIRM_FIRST,
            "MICRO_AUTO": cls.AUTO,
            "STANDARD_AUTO": cls.AUTO,
        }
        return _MAP.get(tier, cls.ALERT_ONLY)

    @classmethod
    def from_decision_level(cls, level: str) -> "Autonomy":
        """Map DecisionLevel labels (base_scorer.py) to Autonomy.

        Note: Even EXECUTE maps to CONFIRM_FIRST because DACLE informs,
        David decides. Full AUTO requires the Trade Router (pending).
        """
        _MAP = {
            "EXECUTE": cls.CONFIRM_FIRST,
            "HIGH_CONVICTION": cls.ALERT_ONLY,
            "ACCUMULATE": cls.ALERT_ONLY,
            "WATCHLIST": cls.BLOCKED,
            "SKIP": cls.BLOCKED,
            "BLOCKED": cls.BLOCKED,
        }
        return _MAP.get(level, cls.ALERT_ONLY)

    @property
    def label(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Staleness thresholds — single source of truth
# ---------------------------------------------------------------------------
# Previously scattered across 4 files with conflicting values.
# Format: (hours: int, rationale: str)
# Documented here so future changes only need one edit.

STALENESS_THRESHOLDS: Dict[str, Tuple[int, str]] = {
    # Health monitoring (hours)
    "feedback_patterns": (168, "7 days — depends on David providing trade feedback"),
    "forward_validation": (840, "35 days — predictions only happen when new TGEs appear"),
    "trade_log": (24, "MEXC sync runs daily at 07:00 UTC"),

    # Token staleness by state (hours)
    "token_live": (12, "LIVE tokens need fresh data for trading"),
    "token_watching": (24, "Watched tokens checked daily"),
    "token_analyzed": (72, "Analyzed backlog, lower priority"),

    # Alert dedup windows (hours)
    "alert_post_tge": (48, "Post-TGE alerts: 48h dedup window"),
    "alert_spot_short": (168, "Spot-short DCA: 7-day dedup (long-term opportunities)"),
    "alert_indices": (48, "Index flip alerts: 48h dedup"),
    "alert_sniper": (72, "Sniper alerts: 3-day dedup"),
    "alert_ml_cache": (24, "ML validation cache: 1 day"),

    # Learning loop health (hours)
    "pattern_max_age": (48, "Feedback patterns file freshness"),
    "forward_val_max_age": (72, "Learning loop forward validation (file freshness)"),
}

# Note on the 7-day vs 72-hour discrepancy (documented Session 399b):
#   - forward_validation in system_health.py uses 7 days (168h) because it checks
#     the last PREDICTION date — predictions only happen when new TGEs appear.
#   - forward_val_max_age in learning_loop_health.py uses 3 days (72h) because it
#     checks the log FILE freshness — the file should be touched by daily maintenance.
#   Both are intentional: different checks measure different things.


def get_staleness_threshold(component: str) -> Optional[int]:
    """Get staleness threshold in hours for a component. Returns None if unknown."""
    entry = STALENESS_THRESHOLDS.get(component)
    if entry is None:
        return None
    return entry[0]
