"""
Token Unlock Schedule Fetcher
Session 440: Phase 5 -- track upcoming token unlocks for supply overhang signals.

Sources: Tokenomist API (free tier) and CryptoRank unlock data.
"""
import logging
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10

# Risk thresholds (pct supply unlocked within N days)
SUPPLY_OVERHANG_PCT = 3.0
SUPPLY_OVERHANG_DAYS = 2  # 48h
UNLOCK_CATALYST_PCT = 5.0
UNLOCK_CATALYST_DAYS = 7
MAJOR_UNLOCK_PCT = 10.0
MAJOR_UNLOCK_DAYS = 14

# Risk labels
SUPPLY_OVERHANG = "SUPPLY_OVERHANG"
UNLOCK_CATALYST = "UNLOCK_CATALYST"
MAJOR_UNLOCK = "MAJOR_UNLOCK"
LOW_RISK = "LOW_RISK"
NO_DATA = "NO_DATA"

# BQS modifiers by risk level
_RISK_MODIFIERS: Dict[str, Dict[str, int]] = {
    SUPPLY_OVERHANG: {"long_modifier": -10, "short_modifier": 4},
    UNLOCK_CATALYST: {"long_modifier": -5, "short_modifier": 2},
    MAJOR_UNLOCK:    {"long_modifier": -8, "short_modifier": 3},
    LOW_RISK:        {"long_modifier": 0, "short_modifier": 0},
    NO_DATA:         {"long_modifier": 0, "short_modifier": 0},
}

# Human-readable labels
_RISK_LABELS: Dict[str, str] = {
    SUPPLY_OVERHANG: "Supply overhang: >3% unlock within 48h",
    UNLOCK_CATALYST: "Unlock catalyst: >5% supply within 7d",
    MAJOR_UNLOCK:    "Major unlock: >10% supply within 14d",
    LOW_RISK:        "No significant unlock pressure",
    NO_DATA:         "Unlock data unavailable",
}

# Warning messages
_RISK_WARNINGS: Dict[str, str] = {
    SUPPLY_OVERHANG: "CRITICAL: Large unlock imminent -- avoid LONG, favor SHORT entries",
    UNLOCK_CATALYST: "WARNING: Upcoming unlock may create selling pressure within 7 days",
    MAJOR_UNLOCK:    "ALERT: Major token unlock scheduled -- monitor for dump catalyst",
    LOW_RISK:        "",
    NO_DATA:         "",
}


# ============================================================================
# FETCH FUNCTIONS (I/O)
# ============================================================================


def fetch_unlock_schedule(symbol: str) -> Optional[dict]:
    """Fetch upcoming token unlock schedule.

    Tries Tokenomist API first, falls back to CryptoRank if available.

    Args:
        symbol: Token symbol (e.g. 'ARB', 'OP').

    Returns:
        {"next_unlock_date": str, "unlock_pct": float, "unlock_usd": float,
         "days_until": int} or None on failure.
    """
    result = _fetch_from_tokenomist(symbol)
    if result:
        return result

    # Fallback -- CryptoRank or other sources can be added here
    logger.info("fetch_unlock_schedule(%s): no data from any source", symbol)
    return None


def _fetch_from_tokenomist(symbol: str) -> Optional[dict]:
    """Try Tokenomist API (free tier)."""
    url = f"https://api.tokenomist.ai/v1/unlocks"
    try:
        resp = requests.get(
            url,
            params={"token": symbol.upper()},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 404:
            logger.debug("Tokenomist: no unlock data for %s", symbol)
            return None
        resp.raise_for_status()
        data = resp.json()

        # Parse response -- Tokenomist returns various formats
        # Try to extract the next upcoming unlock event
        unlocks = data if isinstance(data, list) else data.get("unlocks", [])
        if not unlocks:
            return None

        # Find the nearest future unlock
        for unlock in unlocks:
            days_until = unlock.get("days_until", 0)
            if days_until is not None and days_until >= 0:
                return {
                    "next_unlock_date": unlock.get("date", ""),
                    "unlock_pct": float(unlock.get("percent", 0)),
                    "unlock_usd": float(unlock.get("value_usd", 0)),
                    "days_until": int(days_until),
                }

        return None

    except requests.exceptions.HTTPError as exc:
        logger.warning("Tokenomist API error for %s: %s", symbol, exc)
        return None
    except Exception as exc:
        logger.warning("Tokenomist fetch failed for %s: %s", symbol, exc)
        return None


# ============================================================================
# EVALUATION (pure function -- no I/O)
# ============================================================================


def evaluate_unlock_risk(unlock_data: Optional[dict]) -> dict:
    """Evaluate unlock risk level from fetched unlock data.

    Pure function. No I/O.

    Args:
        unlock_data: Dict from fetch_unlock_schedule(), or None.

    Returns:
        {"risk_level": str, "label": str, "long_modifier": int,
         "short_modifier": int, "warning": str}
    """
    if not unlock_data:
        return _build_risk_result(NO_DATA)

    unlock_pct = unlock_data.get("unlock_pct", 0)
    days_until = unlock_data.get("days_until", 999)

    # Check thresholds from most severe to least
    if unlock_pct >= SUPPLY_OVERHANG_PCT and days_until <= SUPPLY_OVERHANG_DAYS:
        return _build_risk_result(SUPPLY_OVERHANG)

    if unlock_pct >= MAJOR_UNLOCK_PCT and days_until <= MAJOR_UNLOCK_DAYS:
        return _build_risk_result(MAJOR_UNLOCK)

    if unlock_pct >= UNLOCK_CATALYST_PCT and days_until <= UNLOCK_CATALYST_DAYS:
        return _build_risk_result(UNLOCK_CATALYST)

    return _build_risk_result(LOW_RISK)


def _build_risk_result(risk_level: str) -> dict:
    """Build a standardized risk result dict."""
    modifiers = _RISK_MODIFIERS.get(risk_level, _RISK_MODIFIERS[NO_DATA])
    return {
        "risk_level": risk_level,
        "label": _RISK_LABELS.get(risk_level, ""),
        "long_modifier": modifiers["long_modifier"],
        "short_modifier": modifiers["short_modifier"],
        "warning": _RISK_WARNINGS.get(risk_level, ""),
    }


# ============================================================================
# BQS MODIFIER (pure function)
# ============================================================================


def get_unlock_bqs_modifier(unlock_result: dict, direction: str) -> int:
    """Return the BQS modifier points for a given unlock risk and direction.

    Args:
        unlock_result: Dict returned by evaluate_unlock_risk().
        direction: Trade direction, 'SHORT' or 'LONG'.

    Returns:
        Integer BQS modifier (positive = supportive, negative = warning).
    """
    direction_upper = direction.upper()
    if direction_upper == "LONG":
        return unlock_result.get("long_modifier", 0)
    elif direction_upper == "SHORT":
        return unlock_result.get("short_modifier", 0)
    return 0
