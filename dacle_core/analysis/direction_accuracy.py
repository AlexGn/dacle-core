"""Direction Accuracy Tracking — Sprint P2 (Session 430)."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from dacle_core.utils.atomic_write import atomic_json_write

PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_PATH = PROJECT_ROOT / "data" / "state" / "direction_accuracy.json"


class DirectionAccuracyTracker:
    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else DEFAULT_PATH
        self._periods = self._load()

    def _load(self) -> list:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                return data.get("periods", [])
            except Exception:
                return []
        return []

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self._path, {"periods": self._periods})

    def close_period(self, from_bias, to_bias, btc_price_start, btc_price_end, start_ts, end_ts):
        # Idempotency: skip if a period with the same start_ts+end_ts already exists
        for existing in self._periods:
            if existing.get("start_ts") == start_ts and existing.get("end_ts") == end_ts:
                return

        btc_change_pct = ((btc_price_end - btc_price_start) / btc_price_start) * 100

        if from_bias == "NEUTRAL":
            correct = None  # NEUTRAL excluded from accuracy
        elif from_bias == "BULLISH":
            correct = btc_change_pct > 0
        elif from_bias == "BEARISH":
            correct = btc_change_pct < 0
        else:
            correct = None

        period = {
            "bias": from_bias,
            "btc_price_start": btc_price_start,
            "btc_price_end": btc_price_end,
            "btc_change_pct": round(btc_change_pct, 2),
            "correct": correct,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "closed_by": to_bias,
        }
        self._periods.append(period)
        self._save()

    def get_periods(self) -> list:
        return list(self._periods)

    def get_accuracy_stats(self, days=90) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        relevant = []
        for p in self._periods:
            try:
                end_dt = datetime.fromisoformat(p["end_ts"])
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                if end_dt >= cutoff:
                    relevant.append(p)
            except Exception:
                relevant.append(p)

        scored = [p for p in relevant if p.get("correct") is not None]
        neutral = [p for p in relevant if p.get("bias") == "NEUTRAL"]

        total = len(scored)
        correct = sum(1 for p in scored if p["correct"])
        hit_rate = round((correct / total * 100), 1) if total > 0 else 0.0

        by_bias = {}
        for bias in ["BULLISH", "BEARISH"]:
            bias_periods = [p for p in scored if p["bias"] == bias]
            bias_correct = sum(1 for p in bias_periods if p["correct"])
            if bias_periods:
                by_bias[bias] = {
                    "periods": len(bias_periods),
                    "correct": bias_correct,
                    "hit_rate": round((bias_correct / len(bias_periods) * 100), 1),
                }
            else:
                by_bias[bias] = {"periods": 0, "correct": 0, "hit_rate": 0.0}

        by_bias["NEUTRAL"] = {"periods": len(neutral), "excluded": True}

        return {
            "total_periods": total,
            "correct_calls": correct,
            "hit_rate": hit_rate,
            "by_bias": by_bias,
        }
