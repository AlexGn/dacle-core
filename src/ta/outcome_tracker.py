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
    """Load existing outcome log with shared file locking.

    Session 374d: Issue #4 fix — uses atomic_read for concurrent safety.
    """
    if not OUTCOME_LOG_PATH.exists():
        OUTCOME_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        return []
    try:
        from src.utils.atomic_state import atomic_read
        data = atomic_read(str(OUTCOME_LOG_PATH))
        return data if isinstance(data, list) else []
    except ImportError:
        # Fallback if atomic_state not available
        try:
            with open(OUTCOME_LOG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    except (json.JSONDecodeError, IOError):
        return []


def _save_outcomes(outcomes: list[dict]) -> None:
    """Save outcome log atomically via temp file + rename.

    Session 374d: Issue #4 fix — atomic write for concurrent safety.
    Note: Cannot use atomic_write() because it expects a dict (adds _last_updated).
    Outcome log is a list, so we use the same temp+rename pattern directly.
    """
    import os
    import tempfile

    OUTCOME_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(
        dir=OUTCOME_LOG_PATH.parent,
        prefix=f".{OUTCOME_LOG_PATH.stem}_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(temp_fd, "w") as f:
            json.dump(outcomes, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.rename(temp_path, str(OUTCOME_LOG_PATH))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


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
    setup_type: str = "RECOVERY",
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
        "setup_type": setup_type,
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


TRADE_LOG_PATH = Path("data/trades/trade_log.json")


def sync_ta_outcomes(
    trade_log_path: Optional[str] = None,
    match_window_days: int = 14,
) -> dict:
    """
    Match unrecorded TA predictions to actual trade outcomes from trade_log.json.

    Matches by: token (exact) + direction (exact) + logged_at within ±match_window_days
    of trade entry date.

    Returns {matched: [...], unmatched: [...], total_synced: int}.
    """
    log_path = Path(trade_log_path) if trade_log_path else TRADE_LOG_PATH
    if not log_path.exists():
        logger.warning(f"Trade log not found: {log_path}")
        return {"matched": [], "unmatched": [], "total_synced": 0}

    try:
        with open(log_path) as f:
            trade_log = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load trade log: {e}")
        return {"matched": [], "unmatched": [], "total_synced": 0}

    # Normalize trade log to list
    trades = trade_log if isinstance(trade_log, list) else trade_log.get("trades", [])

    outcomes = _load_outcomes()
    unmatched_entries = [o for o in outcomes if o.get("outcome") is None]

    if not unmatched_entries:
        return {"matched": [], "unmatched": [], "total_synced": 0}

    # Session 374d: Issue #9 fix — build indexed trade lookup O(N+M)
    # instead of O(N*M) nested loop. Index by (token, direction) key.
    from collections import defaultdict
    trade_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for trade in trades:
        trade_token = (trade.get("token") or trade.get("symbol", "")).upper()
        trade_direction = (trade.get("direction") or trade.get("side", "")).upper()
        trade_result = (trade.get("result") or trade.get("outcome", "")).upper()
        if not trade_result or trade_result not in ("WIN", "LOSS", "BREAKEVEN"):
            continue
        trade_date_str = trade.get("entry_date") or trade.get("open_time") or trade.get("date", "")
        if not trade_date_str:
            continue
        try:
            trade_date = datetime.fromisoformat(str(trade_date_str).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        trade_index[(trade_token, trade_direction)].append({
            "trade": trade,
            "date": trade_date,
            "result": trade_result,
        })

    matched = []
    still_unmatched = []

    for entry in unmatched_entries:
        token = entry.get("token", "").upper()
        direction = entry.get("direction", "").upper()
        logged_at_str = entry.get("logged_at", "")

        if not logged_at_str:
            still_unmatched.append(entry["tracking_id"])
            continue

        try:
            logged_at = datetime.fromisoformat(logged_at_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            still_unmatched.append(entry["tracking_id"])
            continue

        # O(1) lookup by (token, direction), then scan only matching trades
        candidates = trade_index.get((token, direction), [])
        best_match = None
        best_lag = None
        for candidate in candidates:
            lag_days = abs((candidate["date"] - logged_at).total_seconds()) / 86400
            if lag_days <= match_window_days:
                if best_lag is None or lag_days < best_lag:
                    best_match = candidate["trade"]
                    best_lag = lag_days

        if best_match:
            outcome = (best_match.get("result") or best_match.get("outcome", "")).upper()
            pnl = best_match.get("pnl_pct") or best_match.get("realized_pnl_pct")
            record_outcome(
                tracking_id=entry["tracking_id"],
                outcome=outcome,
                actual_pnl_pct=pnl,
                notes=f"Auto-synced from trade_log (lag: {best_lag:.1f}d)",
            )
            matched.append(entry["tracking_id"])
        else:
            still_unmatched.append(entry["tracking_id"])

    return {
        "matched": matched,
        "unmatched": still_unmatched,
        "total_synced": len(matched),
    }


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
