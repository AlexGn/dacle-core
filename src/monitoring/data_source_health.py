#!/usr/bin/env python3
"""
Data Source Health Monitoring - Session 260 (Phase 1, Task 1.1)

Tracks API success/failure rates for all data sources to prevent silent degradation.
Provides health scoring, alerting, and Telegram integration.

Key Features:
1. Track success/failure rates per source (24h rolling window)
2. Calculate health score (0-100%) based on recent performance
3. Alert when source drops below threshold (<50% success rate)
4. Integrate with Telegram alerts (data quality badge)
5. Integrate with Sentry (degradation alerts)

Usage:
    # Record API call result
    from src.monitoring.data_source_health import DataSourceHealthMonitor

    monitor = DataSourceHealthMonitor()

    # Method 1: Decorator
    @monitor.track("cryptorank")
    def fetch_tge_data(token):
        response = requests.get(f"https://api.cryptorank.io/v1/tge/{token}")
        return response.json()

    # Method 2: Context manager
    with monitor.track_call("coingecko"):
        data = fetch_from_coingecko(token)

    # Method 3: Manual
    try:
        data = fetch_data()
        monitor.record_success("hyperliquid")
    except Exception as e:
        monitor.record_failure("hyperliquid", str(e))

    # Query health
    health = monitor.get_source_health("cryptorank")
    print(f"CryptoRank health: {health['health_score']:.0%}")

    # Get overall data quality
    quality = monitor.get_overall_quality()
    print(f"Overall data quality: {quality:.0%}")

Storage: data/health/source_metrics.json

Author: Claude Code (Session 260, Phase 1)
Date: 2025-12-27
"""

import json
import logging
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
HEALTH_METRICS_PATH = PROJECT_ROOT / "data" / "health" / "source_metrics.json"

# Configuration
HEALTH_WINDOW_HOURS = 24  # Track last 24 hours
MIN_CALLS_FOR_HEALTH = 5  # Need 5+ calls to calculate health
SUCCESS_RATE_THRESHOLD = 0.50  # Alert if <50% success rate
DEGRADED_THRESHOLD = 0.70  # Warn if <70% success rate
RETENTION_HOURS = 168  # Keep 7 days of history


class DataSourceHealthMonitor:
    """
    Monitor health of data sources (APIs, databases, etc.).

    Tracks success/failure rates in a 24-hour rolling window and provides
    health scoring for alerting and quality reporting.
    """

    def __init__(self, metrics_path: Optional[Path] = None):
        """
        Initialize health monitor.

        Args:
            metrics_path: Path to metrics JSON file (default: data/health/source_metrics.json)
        """
        self.metrics_path = metrics_path or HEALTH_METRICS_PATH
        self.metrics_path.parent.mkdir(parents=True, exist_ok=True)

        # In-memory cache (reduces file I/O)
        self._cache: Dict[str, List[Dict]] = {}
        self._cache_loaded = False

    def _load_metrics(self) -> Dict[str, List[Dict]]:
        """
        Load metrics from disk.

        Returns:
            Dict mapping source name -> list of call records
        """
        if self._cache_loaded:
            return self._cache

        if self.metrics_path.exists():
            try:
                with open(self.metrics_path) as f:
                    data = json.load(f)
                    self._cache = data.get("sources", {})
                    self._cache_loaded = True
                    return self._cache
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Corrupted health metrics file, starting fresh: {e}")

        self._cache = {}
        self._cache_loaded = True
        return self._cache

    def _save_metrics(self) -> None:
        """Save metrics to disk."""
        # Clean old entries before saving
        self._cleanup_old_entries()

        data = {
            "metadata": {
                "last_updated": datetime.now().isoformat(),
                "version": "1.0",
                "retention_hours": RETENTION_HOURS,
            },
            "sources": self._cache,
        }

        with open(self.metrics_path, "w") as f:
            json.dump(data, f, indent=2)

    def _cleanup_old_entries(self) -> None:
        """Remove entries older than retention period."""
        cutoff = datetime.now() - timedelta(hours=RETENTION_HOURS)
        cutoff_ts = cutoff.timestamp()

        for source, calls in self._cache.items():
            self._cache[source] = [
                call for call in calls
                if call.get("timestamp", 0) > cutoff_ts
            ]

    def record_success(self, source: str, duration_ms: Optional[float] = None) -> None:
        """
        Record successful API call.

        Args:
            source: Source name (e.g., "cryptorank", "coingecko")
            duration_ms: Optional duration in milliseconds
        """
        metrics = self._load_metrics()

        if source not in metrics:
            metrics[source] = []

        record = {
            "timestamp": time.time(),
            "success": True,
            "duration_ms": duration_ms,
        }

        metrics[source].append(record)
        self._save_metrics()

        logger.debug(f"Recorded success for {source} ({duration_ms:.0f}ms)" if duration_ms else f"Recorded success for {source}")

    def record_failure(
        self,
        source: str,
        error: str,
        duration_ms: Optional[float] = None
    ) -> None:
        """
        Record failed API call.

        Args:
            source: Source name
            error: Error message or type
            duration_ms: Optional duration before failure
        """
        metrics = self._load_metrics()

        if source not in metrics:
            metrics[source] = []

        record = {
            "timestamp": time.time(),
            "success": False,
            "error": error[:200],  # Truncate long errors
            "duration_ms": duration_ms,
        }

        metrics[source].append(record)
        self._save_metrics()

        logger.debug(f"Recorded failure for {source}: {error[:50]}")

    def get_source_health(
        self,
        source: str,
        window_hours: int = HEALTH_WINDOW_HOURS
    ) -> Dict[str, Any]:
        """
        Get health metrics for a specific source.

        Args:
            source: Source name
            window_hours: Time window in hours (default: 24)

        Returns:
            Dict with health metrics:
            {
                "source": str,
                "health_score": float (0-1),
                "status": str ("healthy", "degraded", "critical", "insufficient_data"),
                "total_calls": int,
                "successful_calls": int,
                "failed_calls": int,
                "success_rate": float (0-1),
                "avg_duration_ms": float,
                "recent_errors": List[str],
                "last_call_timestamp": float,
            }
        """
        metrics = self._load_metrics()

        if source not in metrics:
            return {
                "source": source,
                "health_score": 0.0,
                "status": "no_data",
                "total_calls": 0,
                "successful_calls": 0,
                "failed_calls": 0,
                "success_rate": 0.0,
                "avg_duration_ms": None,
                "recent_errors": [],
                "last_call_timestamp": None,
            }

        # Filter to time window
        cutoff = datetime.now() - timedelta(hours=window_hours)
        cutoff_ts = cutoff.timestamp()

        recent_calls = [
            call for call in metrics[source]
            if call.get("timestamp", 0) > cutoff_ts
        ]

        if not recent_calls:
            return {
                "source": source,
                "health_score": 0.0,
                "status": "no_recent_data",
                "total_calls": 0,
                "successful_calls": 0,
                "failed_calls": 0,
                "success_rate": 0.0,
                "avg_duration_ms": None,
                "recent_errors": [],
                "last_call_timestamp": max(call.get("timestamp", 0) for call in metrics[source]) if metrics[source] else None,
            }

        # Calculate metrics
        total = len(recent_calls)
        successful = sum(1 for call in recent_calls if call.get("success", False))
        failed = total - successful
        success_rate = successful / total if total > 0 else 0.0

        # Average duration (successful calls only)
        durations = [call.get("duration_ms") for call in recent_calls if call.get("success") and call.get("duration_ms")]
        avg_duration = sum(durations) / len(durations) if durations else None

        # Recent errors (last 5)
        errors = [
            call.get("error", "Unknown error")
            for call in sorted(recent_calls, key=lambda x: x.get("timestamp", 0), reverse=True)
            if not call.get("success")
        ][:5]

        # Health score (0-1)
        if total < MIN_CALLS_FOR_HEALTH:
            health_score = 0.0
            status = "insufficient_data"
        else:
            health_score = success_rate

            if success_rate >= DEGRADED_THRESHOLD:
                status = "healthy"
            elif success_rate >= SUCCESS_RATE_THRESHOLD:
                status = "degraded"
            else:
                status = "critical"

        return {
            "source": source,
            "health_score": health_score,
            "status": status,
            "total_calls": total,
            "successful_calls": successful,
            "failed_calls": failed,
            "success_rate": success_rate,
            "avg_duration_ms": avg_duration,
            "recent_errors": errors,
            "last_call_timestamp": max(call.get("timestamp", 0) for call in recent_calls),
        }

    def get_all_sources_health(self) -> Dict[str, Dict[str, Any]]:
        """
        Get health metrics for all sources.

        Returns:
            Dict mapping source name -> health metrics
        """
        metrics = self._load_metrics()
        return {
            source: self.get_source_health(source)
            for source in metrics.keys()
        }

    def get_overall_quality(self) -> float:
        """
        Calculate overall data quality score (0-1).

        Weighted by number of calls per source (more active sources have higher weight).

        Returns:
            Overall quality score (0-1), or 0.0 if no data
        """
        all_health = self.get_all_sources_health()

        if not all_health:
            return 0.0

        # Weight by total calls
        weighted_sum = 0.0
        total_weight = 0

        for source, health in all_health.items():
            total_calls = health.get("total_calls", 0)
            if total_calls >= MIN_CALLS_FOR_HEALTH:
                weighted_sum += health.get("health_score", 0) * total_calls
                total_weight += total_calls

        if total_weight == 0:
            return 0.0

        return weighted_sum / total_weight

    def get_degraded_sources(self) -> List[Dict[str, Any]]:
        """
        Get list of sources with degraded or critical health.

        Returns:
            List of health dicts for degraded/critical sources, sorted by health score
        """
        all_health = self.get_all_sources_health()

        degraded = [
            health for source, health in all_health.items()
            if health.get("status") in ["degraded", "critical"]
            and health.get("total_calls", 0) >= MIN_CALLS_FOR_HEALTH
        ]

        # Sort by health score (worst first)
        degraded.sort(key=lambda x: x.get("health_score", 1.0))

        return degraded

    def check_and_alert(self) -> List[str]:
        """
        Check health and return list of alert messages for degraded sources.

        Returns:
            List of alert messages (empty if all healthy)
        """
        degraded = self.get_degraded_sources()

        if not degraded:
            return []

        alerts = []
        for health in degraded:
            source = health["source"]
            score = health["health_score"]
            status = health["status"]
            success_rate = health["success_rate"]
            total = health["total_calls"]
            failed = health["failed_calls"]

            if status == "critical":
                alerts.append(
                    f"🚨 CRITICAL: {source} at {score:.0%} health "
                    f"({success_rate:.0%} success rate, {failed}/{total} failed)"
                )
            elif status == "degraded":
                alerts.append(
                    f"⚠️ DEGRADED: {source} at {score:.0%} health "
                    f"({success_rate:.0%} success rate, {failed}/{total} failed)"
                )

        return alerts

    @contextmanager
    def track_call(self, source: str):
        """
        Context manager to track an API call.

        Usage:
            with monitor.track_call("cryptorank"):
                data = fetch_from_cryptorank()

        Args:
            source: Source name
        """
        start_time = time.time()
        error = None

        try:
            yield
        except Exception as e:
            error = e
            raise
        finally:
            duration_ms = (time.time() - start_time) * 1000

            if error is None:
                self.record_success(source, duration_ms)
            else:
                self.record_failure(source, str(error), duration_ms)

    def track(self, source: str) -> Callable:
        """
        Decorator to track a function's calls.

        Usage:
            @monitor.track("cryptorank")
            def fetch_tge_data(token):
                return requests.get(f"api/{token}").json()

        Args:
            source: Source name

        Returns:
            Decorator function
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args, **kwargs):
                with self.track_call(source):
                    return func(*args, **kwargs)
            return wrapper
        return decorator


# Global singleton instance
_monitor_instance: Optional[DataSourceHealthMonitor] = None

def get_monitor() -> DataSourceHealthMonitor:
    """
    Get global monitor instance (singleton).

    Returns:
        DataSourceHealthMonitor instance
    """
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = DataSourceHealthMonitor()
    return _monitor_instance


if __name__ == "__main__":
    # CLI for testing and manual checks
    import sys

    monitor = get_monitor()

    if len(sys.argv) < 2:
        # Show all sources health
        print("\n" + "="*70)
        print("DATA SOURCE HEALTH MONITOR")
        print("="*70)

        overall = monitor.get_overall_quality()
        print(f"\n📊 Overall Data Quality: {overall:.1%}")

        all_health = monitor.get_all_sources_health()

        if not all_health:
            print("\n⚠️  No health data available yet")
            print("Run data fetchers to collect metrics")
        else:
            print(f"\n📈 Sources Monitored: {len(all_health)}")

            # Group by status
            healthy = [h for h in all_health.values() if h["status"] == "healthy"]
            degraded = [h for h in all_health.values() if h["status"] == "degraded"]
            critical = [h for h in all_health.values() if h["status"] == "critical"]
            insufficient = [h for h in all_health.values() if h["status"] == "insufficient_data"]

            print(f"  ✅ Healthy: {len(healthy)}")
            print(f"  ⚠️  Degraded: {len(degraded)}")
            print(f"  🚨 Critical: {len(critical)}")
            print(f"  ℹ️  Insufficient Data: {len(insufficient)}")

            # Show degraded/critical details
            if degraded or critical:
                print("\n" + "-"*70)
                print("DEGRADED/CRITICAL SOURCES")
                print("-"*70)

                for health in sorted(degraded + critical, key=lambda x: x["health_score"]):
                    icon = "🚨" if health["status"] == "critical" else "⚠️"
                    print(f"\n{icon} {health['source'].upper()}")
                    print(f"  Health Score: {health['health_score']:.1%}")
                    print(f"  Success Rate: {health['success_rate']:.1%} ({health['successful_calls']}/{health['total_calls']})")
                    print(f"  Recent Errors:")
                    for error in health['recent_errors'][:3]:
                        print(f"    - {error[:60]}")

        # Check for alerts
        alerts = monitor.check_and_alert()
        if alerts:
            print("\n" + "-"*70)
            print("🔔 ACTIVE ALERTS")
            print("-"*70)
            for alert in alerts:
                print(f"  {alert}")

        print("\n" + "="*70)

    elif sys.argv[1] == "source":
        # Show specific source
        if len(sys.argv) < 3:
            print("Usage: python data_source_health.py source <source_name>")
            sys.exit(1)

        source = sys.argv[2]
        health = monitor.get_source_health(source)

        print(f"\n{'='*70}")
        print(f"HEALTH REPORT: {source.upper()}")
        print('='*70)
        print(f"\nStatus: {health['status'].upper()}")
        print(f"Health Score: {health['health_score']:.1%}")
        print(f"\nCalls (24h): {health['total_calls']}")
        print(f"  Successful: {health['successful_calls']}")
        print(f"  Failed: {health['failed_calls']}")
        print(f"  Success Rate: {health['success_rate']:.1%}")

        if health['avg_duration_ms']:
            print(f"\nAvg Duration: {health['avg_duration_ms']:.0f}ms")

        if health['recent_errors']:
            print(f"\nRecent Errors:")
            for i, error in enumerate(health['recent_errors'], 1):
                print(f"  {i}. {error}")

        if health['last_call_timestamp']:
            last_call = datetime.fromtimestamp(health['last_call_timestamp'])
            print(f"\nLast Call: {last_call.strftime('%Y-%m-%d %H:%M:%S')}")

        print('='*70 + "\n")
