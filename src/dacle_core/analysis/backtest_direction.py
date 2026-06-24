"""Backtest framework for market direction scoring.

Phase 4.5: Replay historical direction readings against actual price
outcomes to evaluate overall and per-signal accuracy.

Usage:
    from src.analysis.backtest_direction import BacktestRunner
    runner = BacktestRunner()
    results = runner.run(history)
    print(results["overall_hit_rate"])
    print(results["per_signal_hit_rate"])
"""

import logging
from typing import List

logger = logging.getLogger(__name__)


class BacktestRunner:
    """Replay historical direction readings against price outcomes."""

    def run(self, history: List[dict]) -> dict:
        """Run backtest on historical data.

        Args:
            history: List of dicts, each with:
                - timestamp: ISO timestamp
                - bias: "BULLISH" / "NEUTRAL" / "BEARISH"
                - score: Composite score (-1 to +1)
                - signals: List of {"name", "score", "weight"}
                - actual_price_change: Float % (positive = UP, negative = DOWN)

        Returns:
            {
                "overall_hit_rate": float,
                "total_periods": int,
                "correct_periods": int,
                "per_signal_hit_rate": {signal_name: {"total", "correct", "hit_rate"}},
                "by_bias": {"BULLISH": {"total", "correct", "hit_rate"}, ...}
            }
        """
        if not history:
            return {
                "overall_hit_rate": 0.0,
                "total_periods": 0,
                "correct_periods": 0,
                "per_signal_hit_rate": {},
                "by_bias": {},
            }

        total = 0
        correct = 0
        per_signal: dict = {}
        by_bias: dict = {}

        for entry in history:
            bias = entry.get("bias", "NEUTRAL")
            actual_change = entry.get("actual_price_change", 0)
            signals = entry.get("signals", [])

            if bias == "NEUTRAL":
                continue  # NEUTRAL doesn't predict direction

            actual_dir = "UP" if actual_change > 0 else "DOWN"
            predicted_dir = "UP" if bias == "BULLISH" else "DOWN"

            total += 1
            is_correct = predicted_dir == actual_dir
            if is_correct:
                correct += 1

            # Per-bias stats
            if bias not in by_bias:
                by_bias[bias] = {"total": 0, "correct": 0}
            by_bias[bias]["total"] += 1
            if is_correct:
                by_bias[bias]["correct"] += 1

            # Per-signal stats
            for sig in signals:
                name = sig.get("name", "")
                sig_score = sig.get("score", 0)
                if sig_score == 0:
                    continue

                if name not in per_signal:
                    per_signal[name] = {"total": 0, "correct": 0}
                per_signal[name]["total"] += 1

                sig_predicted = "UP" if sig_score > 0 else "DOWN"
                if sig_predicted == actual_dir:
                    per_signal[name]["correct"] += 1

        # Compute hit rates
        overall_hit_rate = round((correct / total * 100) if total > 0 else 0.0, 1)

        for name, stats in per_signal.items():
            t = stats["total"]
            stats["hit_rate"] = round((stats["correct"] / t * 100) if t > 0 else 0.0, 1)

        for bias_key, stats in by_bias.items():
            t = stats["total"]
            stats["hit_rate"] = round((stats["correct"] / t * 100) if t > 0 else 0.0, 1)

        return {
            "overall_hit_rate": overall_hit_rate,
            "total_periods": total,
            "correct_periods": correct,
            "per_signal_hit_rate": per_signal,
            "by_bias": by_bias,
        }
