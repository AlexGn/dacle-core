"""
TA Outcome Tracker - Learning from computed TA score accuracy.

Logs computed TA scores and tracks actual trade outcomes to enable
self-calibration of scoring parameters over time.

Session 360: Initial implementation for score distribution learning.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Outcome tracking file
OUTCOME_LOG_PATH = Path("data/ta/outcome_log.json")


def _load_outcomes() -> list[dict]:
    """Load existing outcome log."""
    if not OUTCOME_LOG_PATH.exists():
        OUTCOME_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        return []
    try:
        with open(OUTCOME_LOG_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_outcomes(outcomes: list[dict]) -> None:
    """Save outcome log atomically."""
    OUTCOME_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = OUTCOME_LOG_PATH.with_suffix(".tmp")
    with open(temp_path, "w") as f:
        json.dump(outcomes, f, indent=2)
    temp_path.replace(OUTCOME_LOG_PATH)


def log_ta_score(
    token: str,
    direction: str,
    ta_score: float,
    enhanced_conviction: float,
    confluence_count: int,
    tier_breakdown: dict[str, float],
    entry: float,
    sl: float,
    tp: float,
) -> str:
    """
    Log a computed TA score for later outcome tracking.

    Returns a tracking_id that can be used to record the outcome.
    """
    tracking_id = f"{token}_{direction}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    record = {
        "tracking_id": tracking_id,
        "token": token,
        "direction": direction,
        "ta_score": ta_score,
        "enhanced_conviction": enhanced_conviction,
        "confluence_count": confluence_count,
        "tier_breakdown": tier_breakdown,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "outcome": None,  # To be filled when trade completes
        "outcome_recorded_at": None,
    }

    outcomes = _load_outcomes()
    outcomes.append(record)
    _save_outcomes(outcomes)

    logger.info(f"Logged TA score {ta_score:.1f} for {token} {direction} (id: {tracking_id})")
    return tracking_id


def record_outcome(
    tracking_id: str,
    outcome: str,  # "WIN", "LOSS", "BREAKEVEN", "SKIPPED"
    actual_pnl_pct: Optional[float] = None,
    notes: Optional[str] = None,
) -> bool:
    """
    Record the actual outcome for a previously logged TA score.

    Returns True if the tracking_id was found and updated.
    """
    outcomes = _load_outcomes()

    for record in outcomes:
        if record.get("tracking_id") == tracking_id:
            record["outcome"] = outcome
            record["actual_pnl_pct"] = actual_pnl_pct
            record["notes"] = notes
            record["outcome_recorded_at"] = datetime.now(timezone.utc).isoformat()
            _save_outcomes(outcomes)
            logger.info(f"Recorded outcome {outcome} for {tracking_id}")
            return True

    logger.warning(f"Tracking ID not found: {tracking_id}")
    return False


def get_score_accuracy_stats() -> dict:
    """
    Calculate accuracy statistics by score range.

    Returns stats showing win rate by conviction score bucket,
    enabling calibration of scoring thresholds.
    """
    outcomes = _load_outcomes()
    completed = [o for o in outcomes if o.get("outcome") is not None]

    if not completed:
        return {"message": "No completed outcomes yet", "total": 0}

    # Bucket by enhanced_conviction score
    buckets = {
        "9-10": {"wins": 0, "losses": 0, "breakeven": 0, "skipped": 0},
        "8-9": {"wins": 0, "losses": 0, "breakeven": 0, "skipped": 0},
        "7-8": {"wins": 0, "losses": 0, "breakeven": 0, "skipped": 0},
        "6-7": {"wins": 0, "losses": 0, "breakeven": 0, "skipped": 0},
        "5-6": {"wins": 0, "losses": 0, "breakeven": 0, "skipped": 0},
        "<5": {"wins": 0, "losses": 0, "breakeven": 0, "skipped": 0},
    }

    for record in completed:
        score = record.get("enhanced_conviction", 0)
        outcome = record.get("outcome", "").upper()

        if score >= 9:
            bucket = "9-10"
        elif score >= 8:
            bucket = "8-9"
        elif score >= 7:
            bucket = "7-8"
        elif score >= 6:
            bucket = "6-7"
        elif score >= 5:
            bucket = "5-6"
        else:
            bucket = "<5"

        if outcome == "WIN":
            buckets[bucket]["wins"] += 1
        elif outcome == "LOSS":
            buckets[bucket]["losses"] += 1
        elif outcome == "BREAKEVEN":
            buckets[bucket]["breakeven"] += 1
        elif outcome == "SKIPPED":
            buckets[bucket]["skipped"] += 1

    # Calculate win rates per bucket
    stats = {"total": len(completed), "buckets": {}}
    for bucket, counts in buckets.items():
        total_trades = counts["wins"] + counts["losses"] + counts["breakeven"]
        if total_trades > 0:
            # Win rate includes breakeven as "not losing" per L034
            win_rate = (counts["wins"] + counts["breakeven"]) / total_trades * 100
        else:
            win_rate = None

        stats["buckets"][bucket] = {
            **counts,
            "total_trades": total_trades,
            "win_rate": win_rate,
        }

    return stats


def get_calibration_suggestions() -> list[str]:
    """
    Analyze outcome data and suggest scoring calibration adjustments.

    Returns a list of actionable suggestions based on win rate patterns.
    """
    stats = get_score_accuracy_stats()
    suggestions = []

    if stats.get("total", 0) < 10:
        return ["Need at least 10 completed trades for calibration suggestions"]

    buckets = stats.get("buckets", {})

    # Check if high scores have high win rates (expected)
    high_score_wr = buckets.get("8-9", {}).get("win_rate")
    mid_score_wr = buckets.get("6-7", {}).get("win_rate")

    if high_score_wr is not None and mid_score_wr is not None:
        if high_score_wr < mid_score_wr:
            suggestions.append(
                f"⚠️ 8-9 score win rate ({high_score_wr:.0f}%) is lower than "
                f"6-7 score ({mid_score_wr:.0f}%). Consider tightening high score criteria."
            )

    # Check if 9-10 scores are too common (should be rare)
    nine_ten = buckets.get("9-10", {})
    if nine_ten.get("total_trades", 0) > stats["total"] * 0.3:
        suggestions.append(
            f"⚠️ 9-10 scores are {nine_ten['total_trades']}/{stats['total']} "
            f"({nine_ten['total_trades']/stats['total']*100:.0f}%) — should be ~10%. "
            "Consider increasing _CONFLUENCE_DIVISOR."
        )

    # Check if low scores have decent win rates (may be undervalued)
    low_score_wr = buckets.get("5-6", {}).get("win_rate")
    if low_score_wr is not None and low_score_wr > 60:
        suggestions.append(
            f"ℹ️ 5-6 score win rate is {low_score_wr:.0f}% — these setups may be "
            "undervalued. Consider reviewing scoring weights."
        )

    if not suggestions:
        suggestions.append("✅ Score distribution looks healthy based on outcomes")

    return suggestions
