"""
Permission Ladder for DACLE AI Workflow.

Maps conviction scores to permission tiers for tiered autonomy.
Direction-aware: LONG requires higher conviction than SHORT per L081.

Session 324: Phase 1 - Display labels only, no execution changes.

Tiers (from lowest to highest autonomy):
- SKIP: Below minimum threshold, no alert
- WATCHLIST: Dashboard only, no Telegram
- ALERT_ONLY: Send alert, no execution capability
- CONFIRM_FIRST: Alert + await /execute command
- MICRO_AUTO: Future auto-execute at 0.25x position
- STANDARD_AUTO: Future auto-execute at 0.5x position
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PermissionTier(Enum):
    """Permission tiers from lowest to highest autonomy."""
    SKIP = "SKIP"
    WATCHLIST = "WATCHLIST"
    ALERT_ONLY = "ALERT_ONLY"
    CONFIRM_FIRST = "CONFIRM_FIRST"
    MICRO_AUTO = "MICRO_AUTO"
    STANDARD_AUTO = "STANDARD_AUTO"

    @property
    def display_emoji(self) -> str:
        """Get emoji for Telegram display."""
        return {
            PermissionTier.SKIP: "⛔",
            PermissionTier.WATCHLIST: "👁",
            PermissionTier.ALERT_ONLY: "📢",
            PermissionTier.CONFIRM_FIRST: "✋",
            PermissionTier.MICRO_AUTO: "🤖",
            PermissionTier.STANDARD_AUTO: "🚀",
        }.get(self, "❓")

    @property
    def display_description(self) -> str:
        """Get human-readable description."""
        return {
            PermissionTier.SKIP: "Below threshold",
            PermissionTier.WATCHLIST: "Dashboard monitoring only",
            PermissionTier.ALERT_ONLY: "Alert only, no execution",
            PermissionTier.CONFIRM_FIRST: "Awaiting /execute command",
            PermissionTier.MICRO_AUTO: "Would auto-execute 0.25x (Phase 2)",
            PermissionTier.STANDARD_AUTO: "Would auto-execute 0.5x (Phase 2)",
        }.get(self, "Unknown tier")


@dataclass
class PermissionDecision:
    """Result of permission tier calculation."""
    tier: PermissionTier
    conviction_score: float
    direction: str
    ml_confidence: Optional[float] = None
    position_multiplier: float = 0.0
    rationale: str = ""
    should_send_telegram: bool = True
    ml_gate_applied: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "tier": self.tier.value,
            "conviction_score": self.conviction_score,
            "direction": self.direction,
            "ml_confidence": self.ml_confidence,
            "position_multiplier": self.position_multiplier,
            "rationale": self.rationale,
            "should_send_telegram": self.should_send_telegram,
            "ml_gate_applied": self.ml_gate_applied,
        }

    def format_for_alert(self) -> str:
        """Format for Telegram alert display."""
        lines = [
            f"{self.tier.display_emoji} Permission: {self.tier.value}",
            f"   └─ {self.tier.display_description}",
        ]
        if self.ml_gate_applied:
            lines.append(f"   └─ ML gate: {self.ml_confidence:.1%} confidence")
        return "\n".join(lines)


# Default thresholds if config file not found
DEFAULT_CONFIG = {
    "enabled": True,
    "short_thresholds": {
        "skip_below": 5.5,
        "watchlist_below": 6.0,
        "alert_only_below": 6.5,
        "confirm_first_below": 7.0,
        "micro_auto_below": 7.5,
    },
    "long_thresholds": {
        "skip_below": 6.0,
        "watchlist_below": 6.5,
        "alert_only_below": 7.0,
        "confirm_first_below": 7.5,
        "micro_auto_below": 8.0,
    },
    "position_multipliers": {
        "SKIP": 0.0,
        "WATCHLIST": 0.0,
        "ALERT_ONLY": 0.0,
        "CONFIRM_FIRST": 0.0,
        "MICRO_AUTO": 0.25,
        "STANDARD_AUTO": 0.5,
    },
    "ml_confidence_gate": 0.60,
    "watchlist_sends_telegram": False,
}


def load_permission_config(config_path: Optional[Path] = None) -> dict:
    """
    Load permission configuration from JSON file.

    Args:
        config_path: Path to config file. If None, uses default location.

    Returns:
        Configuration dictionary with thresholds and settings.
    """
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config" / "permission_config.json"

    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
                logger.info(f"Loaded permission config from {config_path}")
                # Merge with defaults to ensure all keys exist
                merged = DEFAULT_CONFIG.copy()
                merged.update(config)
                return merged
        except Exception as e:
            logger.warning(f"Failed to load permission config: {e}. Using defaults.")
    else:
        logger.info(f"Permission config not found at {config_path}. Using defaults.")

    return DEFAULT_CONFIG.copy()


def get_permission_tier(
    conviction_score: float,
    direction: str = "SHORT",
    ml_confidence: Optional[float] = None,
    config: Optional[dict] = None,
) -> PermissionDecision:
    """
    Map conviction score to permission tier (direction-aware).

    Args:
        conviction_score: DACLE conviction score (0-10)
        direction: "SHORT" or "LONG" - LONG has higher thresholds per L081
        ml_confidence: Optional ML confidence (0-1). If provided and below
                       gate threshold, caps tier at ALERT_ONLY.
        config: Optional config dict. If None, loads from file.

    Returns:
        PermissionDecision with tier, rationale, and display info.
    """
    if config is None:
        config = load_permission_config()

    # Normalize direction
    direction = direction.upper()
    if direction not in ("SHORT", "LONG"):
        logger.warning(f"Invalid direction '{direction}', defaulting to SHORT")
        direction = "SHORT"

    # Select thresholds based on direction
    thresholds_key = f"{direction.lower()}_thresholds"
    thresholds = config.get(thresholds_key, config.get("short_thresholds", DEFAULT_CONFIG["short_thresholds"]))

    # Handle None or invalid scores
    if conviction_score is None or conviction_score < 0:
        return PermissionDecision(
            tier=PermissionTier.SKIP,
            conviction_score=conviction_score or 0.0,
            direction=direction,
            ml_confidence=ml_confidence,
            position_multiplier=0.0,
            rationale="Invalid or missing conviction score",
            should_send_telegram=False,
        )

    # Determine base tier from conviction score
    if conviction_score < thresholds.get("skip_below", 5.5):
        base_tier = PermissionTier.SKIP
        rationale = f"Score {conviction_score:.1f} below {direction} floor ({thresholds.get('skip_below', 5.5)})"
    elif conviction_score < thresholds.get("watchlist_below", 6.0):
        base_tier = PermissionTier.WATCHLIST
        rationale = f"Score {conviction_score:.1f} in WATCHLIST range for {direction}"
    elif conviction_score < thresholds.get("alert_only_below", 6.5):
        base_tier = PermissionTier.ALERT_ONLY
        rationale = f"Score {conviction_score:.1f} in ALERT_ONLY range for {direction}"
    elif conviction_score < thresholds.get("confirm_first_below", 7.0):
        base_tier = PermissionTier.CONFIRM_FIRST
        rationale = f"Score {conviction_score:.1f} qualifies for /execute command"
    elif conviction_score < thresholds.get("micro_auto_below", 7.5):
        base_tier = PermissionTier.MICRO_AUTO
        rationale = f"Score {conviction_score:.1f} qualifies for MICRO_AUTO (0.25x)"
    else:
        base_tier = PermissionTier.STANDARD_AUTO
        rationale = f"Score {conviction_score:.1f} qualifies for STANDARD_AUTO (0.5x)"

    # Apply ML confidence gate for MICRO_AUTO and STANDARD_AUTO
    ml_gate = config.get("ml_confidence_gate", 0.60)
    ml_gate_applied = False
    final_tier = base_tier

    if base_tier in (PermissionTier.MICRO_AUTO, PermissionTier.STANDARD_AUTO):
        if ml_confidence is not None and ml_confidence < ml_gate:
            # Downgrade to CONFIRM_FIRST if ML confidence is too low
            final_tier = PermissionTier.CONFIRM_FIRST
            ml_gate_applied = True
            rationale += f" (downgraded: ML {ml_confidence:.1%} < {ml_gate:.0%} gate)"
            logger.info(
                f"ML gate applied: {base_tier.value} -> {final_tier.value} "
                f"(ML confidence {ml_confidence:.1%} < {ml_gate:.0%})"
            )

    # Get position multiplier
    multipliers = config.get("position_multipliers", DEFAULT_CONFIG["position_multipliers"])
    position_multiplier = multipliers.get(final_tier.value, 0.0)

    # Determine if should send Telegram
    watchlist_sends_telegram = config.get("watchlist_sends_telegram", False)
    should_send_telegram = True
    if final_tier == PermissionTier.SKIP:
        should_send_telegram = False
    elif final_tier == PermissionTier.WATCHLIST and not watchlist_sends_telegram:
        should_send_telegram = False

    return PermissionDecision(
        tier=final_tier,
        conviction_score=conviction_score,
        direction=direction,
        ml_confidence=ml_confidence,
        position_multiplier=position_multiplier,
        rationale=rationale,
        should_send_telegram=should_send_telegram,
        ml_gate_applied=ml_gate_applied,
    )


def get_tier_for_token(token_data: dict, config: Optional[dict] = None) -> PermissionDecision:
    """
    Convenience function to get permission tier from token consolidated data.

    Args:
        token_data: Token's consolidated.json data
        config: Optional permission config

    Returns:
        PermissionDecision for the token
    """
    # Get direction from direction_detection
    direction_detection = token_data.get("direction_detection", {})
    direction = direction_detection.get("recommended", "SHORT")

    # Get the appropriate score based on direction
    if direction == "LONG":
        score = token_data.get("long_conviction_score", 0.0)
    else:
        score = token_data.get("conviction_score", token_data.get("short_conviction_score", 0.0))

    # Get ML confidence if available
    ml_validation = token_data.get("ml_validation", {})
    ml_confidence = ml_validation.get("dump_probability")

    return get_permission_tier(
        conviction_score=score,
        direction=direction,
        ml_confidence=ml_confidence,
        config=config,
    )
