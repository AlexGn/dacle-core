"""Daily drift telemetry snapshots for rolling gate-report summaries."""

import fcntl
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict


class DriftTelemetry:
    SCHEMA_VERSION = 1

    def __init__(
        self,
        journal_path: str = "data/audit/polymarket_drift_log.jsonl",
        last_day_path: str = "data/audit/drift_last_day.txt",
    ):
        self.JOURNAL_PATH = journal_path
        self.LAST_DAY_PATH = last_day_path

    def try_record_daily_snapshot(self, obs: Dict[str, Any]) -> bool:
        """Write one snapshot per UTC date (idempotent under lock)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        os.makedirs(os.path.dirname(os.path.abspath(self.JOURNAL_PATH)), exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(self.LAST_DAY_PATH)), exist_ok=True)

        lock_path = self.LAST_DAY_PATH + ".lock"
        with open(lock_path, "w", encoding="utf-8") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                try:
                    existing = open(self.LAST_DAY_PATH, encoding="utf-8").read().strip()
                    if existing == today:
                        return False
                except FileNotFoundError:
                    pass

                admitted = int(obs.get("intents_admitted", 0) or 0)
                rejected = int(obs.get("intents_rejected", 0) or 0)
                total = admitted + rejected
                started_at = obs.get("started_at")
                try:
                    uptime_sec = time.time() - float(started_at) if started_at is not None else 0.0
                except (TypeError, ValueError):
                    uptime_sec = 0.0

                edge_bps_max = obs.get("max_edge_bps_since_start")
                if edge_bps_max is not None:
                    try:
                        edge_bps_max = float(edge_bps_max)
                    except (TypeError, ValueError):
                        edge_bps_max = None

                entry = {
                    "schema_version": self.SCHEMA_VERSION,
                    "date": today,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "intents_admitted": admitted,
                    "intents_rejected": rejected,
                    "fill_rate_pct": round(admitted / total * 100, 1) if total > 0 else 0.0,
                    "edge_bps_max": edge_bps_max,
                    "market_msg_count": int(obs.get("market_msg_count", 0) or 0),
                    "uptime_sec": uptime_sec,
                }

                with open(self.JOURNAL_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")

                tmp_path = self.LAST_DAY_PATH + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(today)
                os.replace(tmp_path, self.LAST_DAY_PATH)
                return True
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)

    def get_drift_summary(self, days: int = 30) -> Dict[str, Any]:
        """Return rolling day-count and simple fill/edge aggregates."""
        window_days = max(1, int(days))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days - 1)).strftime("%Y-%m-%d")
        entries = []
        try:
            with open(self.JOURNAL_PATH, encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if row.get("schema_version") != self.SCHEMA_VERSION:
                        continue
                    if str(row.get("date", "")) < cutoff:
                        continue
                    entries.append(row)
        except FileNotFoundError:
            pass

        if not entries:
            return {"day_count": 0, "edge_bps_max_7d": None, "fill_rate_avg": None}

        # Dedupe by date (last write wins by timestamp).
        by_date: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            date_key = str(entry.get("date", ""))
            if not date_key:
                continue
            existing = by_date.get(date_key)
            if existing is None or str(entry.get("ts", "")) > str(existing.get("ts", "")):
                by_date[date_key] = entry

        sorted_entries = sorted(by_date.values(), key=lambda x: str(x.get("date", "")))
        if not sorted_entries:
            return {"day_count": 0, "edge_bps_max_7d": None, "fill_rate_avg": None}

        last_7 = sorted_entries[-7:]
        edge_candidates = []
        for entry in last_7:
            edge = entry.get("edge_bps_max")
            if edge is None:
                continue
            try:
                edge_candidates.append(float(edge))
            except (TypeError, ValueError):
                continue

        fill_rates = []
        for entry in sorted_entries:
            try:
                fill_rates.append(float(entry.get("fill_rate_pct", 0.0) or 0.0))
            except (TypeError, ValueError):
                fill_rates.append(0.0)

        return {
            "day_count": len(sorted_entries),
            "edge_bps_max_7d": max(edge_candidates) if edge_candidates else None,
            "fill_rate_avg": round(sum(fill_rates) / len(fill_rates), 1),
        }
