"""Direction History v2 — Session 430

Tracks market direction bias readings over time with shift detection
and trend analysis. Replaces the simple list in market_direction_history.json
with structured persistence, confirmation-based shift detection, and
trend strength analysis.
"""

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.atomic_write import atomic_json_write

MAX_ENTRIES = 540

_DEFAULT_V2_PATH = Path("data/state/direction_history_v2.json")
_DEFAULT_V1_PATH = Path("data/state/market_direction_history.json")


@dataclass
class DirectionHistoryEntry:
    timestamp: str
    bias: str
    score: float
    confidence_pct: float
    signal_count: int
    btc_price: float


class DirectionHistory:
    """Manages direction bias history with persistence and shift detection."""

    def __init__(
        self,
        path: Optional[Path] = None,
        v1_path: Optional[Path] = None,
    ):
        self._path = Path(path) if path else _DEFAULT_V2_PATH
        # Only use default v1 path when v2 path is also default
        if v1_path is not None:
            self._v1_path: Optional[Path] = Path(v1_path)
        elif path is None:
            self._v1_path = _DEFAULT_V1_PATH
        else:
            self._v1_path = None
        self._entries: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        """Load entries from v2 file, migrating from v1 if needed."""
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._entries = data.get("entries", [])
            except (json.JSONDecodeError, KeyError):
                self._entries = []
            return

        # V2 doesn't exist — try v1 migration
        if self._v1_path and self._v1_path.exists():
            self._migrate_v1()

    def _migrate_v1(self) -> None:
        """Import entries from v1 market_direction_history.json format."""
        import logging
        _log = logging.getLogger(__name__)
        try:
            v1_data = json.loads(self._v1_path.read_text())
        except json.JSONDecodeError:
            _log.warning(
                "DirectionHistory: v1 migration skipped — invalid JSON in %s",
                self._v1_path,
            )
            return
        try:
            v1_entries = v1_data.get("entries", [])
        except KeyError:
            _log.warning(
                "DirectionHistory: v1 migration skipped — malformed v1 data (missing entries key)"
            )
            return
        missing_price = 0
        for e in v1_entries:
            if "btc_price" not in e:
                missing_price += 1
            self._entries.append({
                "timestamp": e.get("timestamp", ""),
                "bias": e.get("bias", "UNKNOWN"),
                "score": e.get("score", 0.0),
                "confidence_pct": e.get("confidence_pct", 0),
                "signal_count": e.get("signal_count", 0),
                "btc_price": e.get("btc_price", 0.0),
            })
        if missing_price:
            _log.info(
                "DirectionHistory: v1 migration — %d/%d entries missing btc_price (defaulting to 0.0)",
                missing_price,
                len(v1_entries),
            )
        self._save()

    def _save(self) -> None:
        """Persist entries atomically."""
        atomic_json_write(self._path, {"entries": self._entries})

    def add_entry(self, entry: DirectionHistoryEntry) -> None:
        """Append a new direction reading, capping at MAX_ENTRIES."""
        self._entries.append(asdict(entry))
        if len(self._entries) > MAX_ENTRIES:
            self._entries = self._entries[-MAX_ENTRIES:]
        self._save()

    def get_entries(self) -> List[Dict[str, Any]]:
        """Return all stored entries (oldest first)."""
        return list(self._entries)

    def get_current_trend(self) -> Dict[str, Any]:
        """Analyze the current trend from the most recent consecutive same-bias entries."""
        if not self._entries:
            return {
                "bias": None,
                "entry_count": 0,
                "duration_hours": 0,
                "avg_score": 0.0,
                "trend_strength": "UNKNOWN",
            }

        current_bias = self._entries[-1]["bias"]
        trend_entries = []

        # Walk backwards collecting entries with same bias
        for e in reversed(self._entries):
            if e["bias"] == current_bias:
                trend_entries.append(e)
            else:
                break

        trend_entries.reverse()  # Back to chronological order

        # Duration
        if len(trend_entries) >= 2:
            first_ts = datetime.fromisoformat(trend_entries[0]["timestamp"])
            last_ts = datetime.fromisoformat(trend_entries[-1]["timestamp"])
            duration_hours = (last_ts - first_ts).total_seconds() / 3600
        else:
            duration_hours = 0

        scores = [e["score"] for e in trend_entries]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        # Trend strength: compare first half vs second half
        trend_strength = self._calc_trend_strength(scores)

        return {
            "bias": current_bias,
            "entry_count": len(trend_entries),
            "duration_hours": duration_hours,
            "avg_score": avg_score,
            "trend_strength": trend_strength,
        }

    @staticmethod
    def _calc_trend_strength(scores: List[float]) -> str:
        """Compare first half vs second half of scores to determine strength."""
        if len(scores) < 2:
            return "STABLE"

        mid = len(scores) // 2
        first_half = scores[:mid]
        second_half = scores[mid:]

        first_avg = sum(abs(s) for s in first_half) / len(first_half)
        second_avg = sum(abs(s) for s in second_half) / len(second_half)

        if second_avg > first_avg + 0.05:
            return "STRENGTHENING"
        elif second_avg < first_avg - 0.05:
            return "WEAKENING"
        return "STABLE"

    def get_shift_history(self, days: int = 30) -> List[Dict[str, Any]]:
        """Return all bias shifts within the given number of days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        timestamps = []
        for entry in self._entries:
            try:
                timestamps.append(datetime.fromisoformat(entry["timestamp"]))
            except (KeyError, TypeError, ValueError):
                continue
        apply_cutoff = any(ts >= cutoff for ts in timestamps)
        shifts = []

        for i in range(1, len(self._entries)):
            prev = self._entries[i - 1]
            curr = self._entries[i]
            if prev["bias"] != curr["bias"]:
                ts = datetime.fromisoformat(curr["timestamp"])
                if not apply_cutoff or ts >= cutoff:
                    shifts.append({
                        "from_bias": prev["bias"],
                        "to_bias": curr["bias"],
                        "timestamp": curr["timestamp"],
                        "score": curr["score"],
                    })

        return shifts

    def detect_shift(
        self, current: DirectionHistoryEntry
    ) -> Optional[Dict[str, Any]]:
        """Detect a confirmed direction shift.

        A shift requires 2 consecutive readings of the new bias after
        the previous trend. Returns shift info dict if confirmed, None otherwise.
        """
        if not self._entries:
            return None

        current_dict = asdict(current)
        new_bias = current_dict["bias"]

        # Find the established bias (the bias before any pending shift)
        # Walk backwards to find the last bias that differs from the tail
        last_entry_bias = self._entries[-1]["bias"]

        if new_bias == last_entry_bias:
            # Check if we have 2+ consecutive of this bias,
            # and they differ from the bias before them
            # Count consecutive same-bias entries from the end
            consecutive = 0
            for e in reversed(self._entries):
                if e["bias"] == new_bias:
                    consecutive += 1
                else:
                    break

            # If exactly 1 prior reading of this bias + current = 2 → confirmed
            if consecutive == 1:
                # Find the established bias before this pending one
                established_bias = self._find_established_bias(new_bias)
                if established_bias and established_bias != new_bias:
                    prev_duration = self._calc_prev_trend_duration(established_bias)
                    return {
                        "from_bias": established_bias,
                        "to_bias": new_bias,
                        "timestamp": current_dict["timestamp"],
                        "prev_trend_duration_hours": prev_duration,
                    }
            # More than 1 consecutive = shift was already confirmed earlier
            return None
        else:
            # New bias differs from last entry — this is the first reading
            # of a potential shift. Not confirmed yet.
            return None

    def _find_established_bias(self, exclude_bias: str) -> Optional[str]:
        """Find the bias that was established before a pending shift."""
        for e in reversed(self._entries):
            if e["bias"] != exclude_bias:
                return e["bias"]
        return None

    def _calc_prev_trend_duration(self, bias: str) -> float:
        """Calculate how long the previous trend lasted in hours."""
        # Collect contiguous entries of this bias (before the pending shift)
        trend_entries = []
        found_bias = False

        for e in reversed(self._entries):
            if e["bias"] == bias:
                found_bias = True
                trend_entries.append(e)
            elif found_bias:
                break

        if len(trend_entries) < 2:
            return 0.0

        trend_entries.reverse()
        first_ts = datetime.fromisoformat(trend_entries[0]["timestamp"])
        last_ts = datetime.fromisoformat(trend_entries[-1]["timestamp"])
        return (last_ts - first_ts).total_seconds() / 3600
