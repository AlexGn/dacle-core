"""
Sherlock Macro Levels Loader
Session 337 - Phase 2

Loads daily Sherlock macro updates from JSON file with fallback to defaults.
Provides dynamic macro levels for L088 alignment checks.
"""

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)

# File path for Sherlock's daily macro updates
SHERLOCK_LEVELS_PATH = Path("data/macro/sherlock_macro_levels.json")

# Staleness threshold: warn if levels are >48 hours old
STALENESS_THRESHOLD_HOURS = 48

# Default levels (fallback if file doesn't exist)
DEFAULT_LEVELS = {
    "btcdom": {
        "resistance_high": 58.0,
        "resistance_low": 56.0,
        "current_value": None
    },
    "total3": {
        "support": 700,
        "resistance": 850,
        "current_value_b": None
    },
    "others_d": {
        "support": 30.0,
        "resistance": 36.0,
        "current_value": None
    },
    "btc_structure": {
        "structure_bias": "BEARISH"
    }
}


def load_sherlock_macro_levels(warn_if_stale: bool = True) -> Dict:
    """
    Load Sherlock macro levels from JSON file.

    Args:
        warn_if_stale: If True, log warning if levels are >48h old

    Returns:
        Dict with btcdom, total3, others_d, btc_structure levels

    Falls back to DEFAULT_LEVELS if file missing or invalid.

    Example return:
        {
            "btcdom": {"resistance_high": 58.5, "resistance_low": 57.2},
            "total3": {"support": 680, "resistance": 850},
            "others_d": {"support": 6.5, "resistance": 7.8},
            "btc_structure": {"structure_bias": "BEARISH"}
        }
    """
    if not SHERLOCK_LEVELS_PATH.exists():
        logger.warning(
            f"Sherlock macro levels file not found: {SHERLOCK_LEVELS_PATH}. "
            f"Using default levels. Upload first update via dashboard at /sherlock-macro-update.html"
        )
        return DEFAULT_LEVELS.copy()

    try:
        with open(SHERLOCK_LEVELS_PATH, 'r') as f:
            data = json.load(f)

        # Check staleness if requested
        if warn_if_stale and "updated_at" in data:
            updated_at = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - updated_at).total_seconds() / 3600

            if age_hours > STALENESS_THRESHOLD_HOURS:
                logger.warning(
                    f"⚠️ Sherlock macro levels are STALE: {age_hours:.1f}h old "
                    f"(threshold: {STALENESS_THRESHOLD_HOURS}h). "
                    f"Last updated: {data['updated_at']} by {data.get('updated_by', 'unknown')}. "
                    f"David should submit a new update via dashboard."
                )

        # Return levels dict
        return data.get("levels", DEFAULT_LEVELS.copy())

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(
            f"Invalid Sherlock macro levels file: {e}. "
            f"Using defaults. File may be corrupted."
        )
        return DEFAULT_LEVELS.copy()


def get_level_metadata() -> Dict:
    """
    Get metadata about current Sherlock levels (last updated, by whom, etc.).

    Returns:
        Dict with updated_at, updated_by, source, notes, is_stale
    """
    if not SHERLOCK_LEVELS_PATH.exists():
        return {
            "exists": False,
            "updated_at": None,
            "updated_by": "System",
            "source": "Default",
            "notes": "No updates received yet",
            "is_stale": True,
            "age_hours": None
        }

    try:
        with open(SHERLOCK_LEVELS_PATH, 'r') as f:
            data = json.load(f)

        # Calculate age
        updated_at = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - updated_at).total_seconds() / 3600
        is_stale = age_hours > STALENESS_THRESHOLD_HOURS

        return {
            "exists": True,
            "updated_at": data.get("updated_at"),
            "updated_by": data.get("updated_by", "Unknown"),
            "source": data.get("source", "Sherlock | Kaizen"),
            "notes": data.get("notes", ""),
            "is_stale": is_stale,
            "age_hours": age_hours
        }

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Error reading metadata: {e}")
        return {
            "exists": True,
            "updated_at": None,
            "updated_by": "Unknown",
            "source": "Unknown",
            "notes": f"Error: {e}",
            "is_stale": True,
            "age_hours": None
        }


def get_update_history(limit: int = 10) -> list:
    """
    Get recent update history.

    Args:
        limit: Maximum number of updates to return (default: 10)

    Returns:
        List of recent updates, most recent first
    """
    if not SHERLOCK_LEVELS_PATH.exists():
        return []

    try:
        with open(SHERLOCK_LEVELS_PATH, 'r') as f:
            data = json.load(f)

        history = data.get("update_history", [])

        # Return last N updates (most recent first)
        return history[-limit:][::-1]

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Error reading update history: {e}")
        return []


def check_level_staleness() -> Dict:
    """
    Check if Sherlock levels are stale.

    Returns:
        Dict with is_stale, age_hours, last_updated, threshold_hours
    """
    if not SHERLOCK_LEVELS_PATH.exists():
        return {
            "is_stale": True,
            "age_hours": None,
            "last_updated": None,
            "threshold_hours": STALENESS_THRESHOLD_HOURS,
            "reason": "No updates file exists"
        }

    try:
        with open(SHERLOCK_LEVELS_PATH, 'r') as f:
            data = json.load(f)

        updated_at = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - updated_at).total_seconds() / 3600

        return {
            "is_stale": age_hours > STALENESS_THRESHOLD_HOURS,
            "age_hours": age_hours,
            "last_updated": data["updated_at"],
            "updated_by": data.get("updated_by"),
            "threshold_hours": STALENESS_THRESHOLD_HOURS,
            "reason": f"{age_hours:.1f}h old" if age_hours > STALENESS_THRESHOLD_HOURS else "Fresh"
        }

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Error checking staleness: {e}")
        return {
            "is_stale": True,
            "age_hours": None,
            "last_updated": None,
            "threshold_hours": STALENESS_THRESHOLD_HOURS,
            "reason": f"File error: {e}"
        }
