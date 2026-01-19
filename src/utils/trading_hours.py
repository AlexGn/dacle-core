"""
Trading Hours Restrictions Module

Implements L093: Weekend Trading Restriction (Gemini-approved Session 337)

Based on David's trading outcomes:
- Weekend P&L: -$51.75/month (66% of December profit)
- Root cause: Low liquidity on weekends leads to manipulation wicks and stop hunts

Rules (Gemini-approved):
- Friday 16:00+ UTC: 50% position size ("Friday Sunset")
- Saturday: 25% position size (conviction >8.5 only)
- Sunday: HARD BLOCK (no new entries)

Rationale:
- Friday: Institutional exit creates liquidity vacuum
- Saturday: Retail manipulation environment
- Sunday: "Sunday Night Dump" pattern (CeFi → DeFi arb exploits)
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple
from enum import Enum


class TradingRestriction(Enum):
    """Trading restriction levels"""
    NORMAL = "NORMAL"
    FRIDAY_SUNSET = "FRIDAY_SUNSET"
    SATURDAY_REDUCED = "SATURDAY_REDUCED"
    SUNDAY_BLOCK = "SUNDAY_BLOCK"


def get_trading_restriction(override_datetime: datetime = None) -> Dict:
    """
    L093: Weekend Trading Restriction (GEMINI APPROVED)

    Returns restriction status based on current UTC time.

    Args:
        override_datetime: Optional datetime for testing (default: current UTC time)

    Returns:
        Dict with keys:
            - restriction: TradingRestriction enum value
            - position_multiplier: float (0.0 = blocked, 0.25 = 25%, 0.5 = 50%, 1.0 = normal)
            - can_trade: bool (False = hard block)
            - min_conviction: float or None (minimum conviction score required)
            - message: str or None (human-readable restriction message)
            - label: str or None (label for alerts)
    """
    now = override_datetime or datetime.now(timezone.utc)
    day = now.weekday()  # 0=Monday, 1=Tuesday, ..., 4=Friday, 5=Saturday, 6=Sunday
    hour = now.hour

    # Sunday: HARD BLOCK
    if day == 6:
        return {
            "restriction": TradingRestriction.SUNDAY_BLOCK,
            "position_multiplier": 0.0,
            "can_trade": False,
            "min_conviction": None,
            "message": "🚫 BLOCKED: Sunday trading disabled per L093 - Low liquidity, high manipulation risk",
            "label": "[SUNDAY BLOCKED]",
            "rationale": "Sunday Night Dump pattern + CeFi-DeFi arbitrage exploitation"
        }

    # Saturday: 25% position, conviction >8.5 only
    elif day == 5:
        return {
            "restriction": TradingRestriction.SATURDAY_REDUCED,
            "position_multiplier": 0.25,
            "can_trade": True,
            "min_conviction": 8.5,
            "message": "⚠️ SATURDAY: 25% position size, conviction >8.5 required (L093)",
            "label": "[SATURDAY RISK]",
            "rationale": "Retail manipulation environment - only highest conviction setups"
        }

    # Friday 4PM+ UTC: 50% position ("Friday Sunset")
    elif day == 4 and hour >= 16:
        return {
            "restriction": TradingRestriction.FRIDAY_SUNSET,
            "position_multiplier": 0.5,
            "can_trade": True,
            "min_conviction": None,
            "message": "⚠️ FRIDAY SUNSET: 50% position size (L093) - Institutional exit window",
            "label": "[FRIDAY RISK]",
            "rationale": "Institutional exit creates liquidity vacuum entering weekend"
        }

    # Normal trading hours (Monday-Thursday, Friday before 4PM)
    else:
        return {
            "restriction": TradingRestriction.NORMAL,
            "position_multiplier": 1.0,
            "can_trade": True,
            "min_conviction": None,
            "message": None,
            "label": None,
            "rationale": "Normal trading hours - full liquidity"
        }


def check_conviction_vs_restriction(conviction_score: float, restriction: Dict) -> Tuple[bool, str]:
    """
    Validate if conviction score meets restriction requirements.

    Args:
        conviction_score: Conviction score (0-10)
        restriction: Dict from get_trading_restriction()

    Returns:
        Tuple of (is_allowed, reason)
    """
    # Hard block (Sunday)
    if not restriction["can_trade"]:
        return (False, restriction["message"])

    # Check minimum conviction requirement (Saturday)
    min_conviction = restriction.get("min_conviction")
    if min_conviction is not None:
        if conviction_score < min_conviction:
            return (
                False,
                f"BLOCKED: {restriction['restriction'].value} requires conviction >{min_conviction} (current: {conviction_score:.1f})"
            )
        else:
            return (
                True,
                f"ALLOWED: Conviction {conviction_score:.1f} meets {restriction['restriction'].value} minimum {min_conviction}"
            )

    # No conviction requirement (Friday Sunset, Normal)
    return (True, f"ALLOWED: {restriction['restriction'].value}")


def get_effective_position_size(base_position_size_usd: float, restriction: Dict) -> float:
    """
    Calculate effective position size after applying weekend restriction.

    Args:
        base_position_size_usd: Intended position size in USD
        restriction: Dict from get_trading_restriction()

    Returns:
        Effective position size in USD (base * multiplier)
    """
    multiplier = restriction["position_multiplier"]
    return base_position_size_usd * multiplier


def format_restriction_warning(restriction: Dict, conviction_score: float = None) -> str:
    """
    Format human-readable restriction warning for alerts/playbooks.

    Args:
        restriction: Dict from get_trading_restriction()
        conviction_score: Optional conviction score to include

    Returns:
        Formatted warning string
    """
    lines = []

    # Main message
    if restriction["message"]:
        lines.append(restriction["message"])

    # Rationale
    lines.append(f"Rationale: {restriction['rationale']}")

    # Position multiplier
    if restriction["position_multiplier"] < 1.0:
        lines.append(f"Effective Position Multiplier: {restriction['position_multiplier']:.1f}x")

    # Conviction score
    if conviction_score is not None:
        lines.append(f"Conviction: {conviction_score:.1f}/10")

    # Minimum conviction requirement
    if restriction.get("min_conviction"):
        lines.append(f"Minimum Conviction Required: {restriction['min_conviction']}/10")

    return "\n".join(lines)


def is_weekend_risk_period() -> bool:
    """
    Quick check if current time is in weekend risk period (Friday 4PM+ through Sunday).

    Returns:
        True if Friday 4PM+, Saturday, or Sunday
    """
    restriction = get_trading_restriction()
    return restriction["restriction"] != TradingRestriction.NORMAL


def get_next_normal_trading_hours() -> datetime:
    """
    Calculate when normal trading hours resume.

    Returns:
        Datetime (UTC) when restriction lifts
    """
    now = datetime.now(timezone.utc)
    day = now.weekday()

    # If Sunday, return Monday 00:00 UTC
    if day == 6:
        days_until_monday = 1
        next_normal = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return next_normal + timedelta(days=days_until_monday)

    # If Saturday, return Monday 00:00 UTC
    elif day == 5:
        days_until_monday = 2
        next_normal = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return next_normal + timedelta(days=days_until_monday)

    # If Friday 4PM+, return Monday 00:00 UTC
    elif day == 4 and now.hour >= 16:
        days_until_monday = 3
        next_normal = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return next_normal + timedelta(days=days_until_monday)

    # If Friday before 4PM, return same day 4PM
    elif day == 4:
        return now.replace(hour=16, minute=0, second=0, microsecond=0)

    # Normal hours - return current time
    else:
        return now


# Convenience functions for quick checks
def is_sunday_blocked() -> bool:
    """Quick check if Sunday hard block is active."""
    return get_trading_restriction()["restriction"] == TradingRestriction.SUNDAY_BLOCK


def is_saturday_restricted() -> bool:
    """Quick check if Saturday restriction is active."""
    return get_trading_restriction()["restriction"] == TradingRestriction.SATURDAY_REDUCED


def is_friday_sunset() -> bool:
    """Quick check if Friday sunset restriction is active."""
    return get_trading_restriction()["restriction"] == TradingRestriction.FRIDAY_SUNSET
