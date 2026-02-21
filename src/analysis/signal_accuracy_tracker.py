"""Per-signal accuracy tracking for market direction signals.

Phase 4.1: Tracks which individual signals correctly predict direction
by recording signal snapshots alongside each direction update, then
evaluating against actual price movement.

Usage:
    tracker = SignalAccuracyTracker(storage_dir=Path("data/state"))
    tracker.record_snapshot(timestamp, bias, score, signals)
    # ... after 4h price data is available ...
    results = tracker.evaluate(timestamp, actual_direction="UP")
    rates = tracker.get_hit_rates()
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class _Snapshot:
    timestamp: str
    bias: str
    score: float
    signals: list  # List of {"name", "score", "weight"}
    evaluation: Optional[dict] = None  # Set after evaluate()


class SignalAccuracyTracker:
    """Track per-signal prediction accuracy across direction updates."""

    def __init__(self, storage_dir: Optional[Path] = None):
        """Initialize tracker.

        Args:
            storage_dir: Directory for persistent storage. None = in-memory only.
        """
        self._storage_dir = storage_dir
        self.snapshots: list[_Snapshot] = []
        self._evaluations: dict[str, dict] = {}  # timestamp -> {signal_name: {correct: bool}}

        if storage_dir:
            self._load()

    def record_snapshot(
        self,
        timestamp: str,
        bias: str,
        score: float,
        signals: list,
    ) -> None:
        """Record signal snapshot for a direction update.

        Args:
            timestamp: ISO timestamp of the direction update.
            bias: "BULLISH", "NEUTRAL", or "BEARISH".
            score: Composite score (-1 to +1).
            signals: List of dicts with "name", "score", "weight".
        """
        snap = _Snapshot(
            timestamp=timestamp,
            bias=bias,
            score=score,
            signals=signals,
        )
        self.snapshots.append(snap)
        self._save()

    def evaluate(
        self,
        timestamp: str,
        actual_direction: str,
    ) -> dict:
        """Evaluate signal predictions against actual price direction.

        Args:
            timestamp: Timestamp of the snapshot to evaluate.
            actual_direction: "UP" or "DOWN" (actual price movement).

        Returns:
            Dict of signal_name -> {"correct": bool, "predicted": str, "actual": str}
        """
        snap = next((s for s in self.snapshots if s.timestamp == timestamp), None)
        if snap is None:
            return {}

        results = {}
        for sig in snap.signals:
            name = sig.get("name", "")
            sig_score = sig.get("score", 0)

            if sig_score == 0:
                # Neutral signals don't predict → skip
                continue

            predicted = "UP" if sig_score > 0 else "DOWN"
            correct = predicted == actual_direction

            results[name] = {
                "correct": correct,
                "predicted": predicted,
                "actual": actual_direction,
                "signal_score": sig_score,
            }

        snap.evaluation = results
        self._evaluations[timestamp] = results
        self._save()
        return results

    def get_hit_rates(self) -> dict:
        """Compute per-signal hit rates across all evaluated snapshots.

        Returns:
            Dict of signal_name -> {"total": int, "correct": int, "hit_rate": float}
        """
        signal_stats: dict[str, dict] = {}

        for ts, evaluation in self._evaluations.items():
            for name, result in evaluation.items():
                if name not in signal_stats:
                    signal_stats[name] = {"total": 0, "correct": 0}
                signal_stats[name]["total"] += 1
                if result.get("correct"):
                    signal_stats[name]["correct"] += 1

        # Compute hit rates
        for name, stats in signal_stats.items():
            total = stats["total"]
            stats["hit_rate"] = round(
                (stats["correct"] / total * 100) if total > 0 else 0.0, 1
            )

        return signal_stats

    def _save(self) -> None:
        """Persist to disk if storage_dir is set."""
        if not self._storage_dir:
            return
        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            path = self._storage_dir / "signal_accuracy_data.json"
            data = {
                "snapshots": [
                    {
                        "timestamp": s.timestamp,
                        "bias": s.bias,
                        "score": s.score,
                        "signals": s.signals,
                        "evaluation": s.evaluation,
                    }
                    for s in self.snapshots
                ],
            }
            path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.debug(f"Signal accuracy save failed: {e}")

    def _load(self) -> None:
        """Load from disk if storage_dir is set."""
        if not self._storage_dir:
            return
        try:
            path = self._storage_dir / "signal_accuracy_data.json"
            if not path.exists():
                return
            data = json.loads(path.read_text())
            for snap_data in data.get("snapshots", []):
                snap = _Snapshot(
                    timestamp=snap_data["timestamp"],
                    bias=snap_data["bias"],
                    score=snap_data["score"],
                    signals=snap_data["signals"],
                    evaluation=snap_data.get("evaluation"),
                )
                self.snapshots.append(snap)
                if snap.evaluation:
                    self._evaluations[snap.timestamp] = snap.evaluation
        except Exception as e:
            logger.debug(f"Signal accuracy load failed: {e}")
