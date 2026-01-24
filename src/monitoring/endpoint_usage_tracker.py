"""
Endpoint Usage Tracker - Session 371 P0.3

Middleware and utilities for tracking API endpoint usage patterns.
This helps identify which endpoints are actively used vs zero-traffic candidates for deprecation.

Usage:
    from src.monitoring.endpoint_usage_tracker import EndpointUsageMiddleware
    app.add_middleware(EndpointUsageMiddleware)

The tracker logs to data/analytics/endpoint_usage.json with:
- Request counts per endpoint (path + method)
- First and last access timestamps
- Daily usage aggregations

After 30 days of data collection, run the analysis:
    python -c "from src.monitoring.endpoint_usage_tracker import analyze_usage; analyze_usage()"
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Dict, Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

# Constants
PROJECT_ROOT = Path(__file__).parent.parent.parent
USAGE_FILE = PROJECT_ROOT / "data" / "analytics" / "endpoint_usage.json"
DAILY_USAGE_DIR = PROJECT_ROOT / "data" / "analytics" / "daily_usage"

# Lock for thread-safe file writes
_file_lock = Lock()


class EndpointUsageMiddleware(BaseHTTPMiddleware):
    """
    Middleware to track API endpoint usage for deprecation analysis.

    Tracks:
    - Total request count per endpoint
    - First and last access timestamps
    - HTTP method distribution

    Excludes:
    - Static files (/dashboard/*, /assets/*)
    - Documentation (/docs, /redoc, /openapi.json)
    - Health checks (/health, /health/long)

    Note: This is a lightweight middleware that writes asynchronously
    to avoid impacting request latency.
    """

    # Paths to exclude from tracking (noise, not useful for deprecation analysis)
    EXCLUDED_PREFIXES = (
        "/dashboard/",
        "/assets/",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/_next/",
        "/favicon",
    )

    # Health endpoints - track separately as infrastructure
    HEALTH_ENDPOINTS = {"/health", "/health/long", "/api/system-status"}

    def __init__(self, app, skip_health: bool = True):
        """
        Initialize the usage tracker.

        Args:
            app: The FastAPI application
            skip_health: Whether to skip health check endpoints (default True)
        """
        super().__init__(app)
        self.skip_health = skip_health
        self._ensure_directories()
        self._usage_buffer: Dict[str, Dict[str, Any]] = {}
        self._buffer_count = 0
        self._flush_threshold = 100  # Flush to disk every 100 requests

    def _ensure_directories(self):
        """Ensure analytics directories exist."""
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DAILY_USAGE_DIR.mkdir(parents=True, exist_ok=True)

    def _should_track(self, path: str) -> bool:
        """Check if this path should be tracked."""
        # Skip excluded prefixes
        if any(path.startswith(prefix) for prefix in self.EXCLUDED_PREFIXES):
            return False

        # Skip health endpoints if configured
        if self.skip_health and path in self.HEALTH_ENDPOINTS:
            return False

        # Track all /api/* endpoints
        return path.startswith("/api/")

    async def dispatch(self, request: Request, call_next):
        """Process request and track usage."""
        path = request.url.path
        method = request.method

        # Process request first (don't block on tracking)
        response = await call_next(request)

        # Track after response is sent
        if self._should_track(path):
            self._track_request(path, method, response.status_code)

        return response

    def _track_request(self, path: str, method: str, status_code: int):
        """
        Track a single request (thread-safe, buffered).

        Uses a buffer to batch writes and reduce I/O overhead.
        """
        endpoint_key = f"{method}:{path}"
        now = datetime.now(timezone.utc).isoformat()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with _file_lock:
            # Update buffer
            if endpoint_key not in self._usage_buffer:
                self._usage_buffer[endpoint_key] = {
                    "method": method,
                    "path": path,
                    "first_seen": now,
                    "last_seen": now,
                    "total_count": 0,
                    "success_count": 0,
                    "error_count": 0,
                    "daily_counts": {},
                }

            entry = self._usage_buffer[endpoint_key]
            entry["last_seen"] = now
            entry["total_count"] += 1

            if 200 <= status_code < 400:
                entry["success_count"] += 1
            else:
                entry["error_count"] += 1

            # Track daily usage
            entry["daily_counts"][today] = entry["daily_counts"].get(today, 0) + 1

            self._buffer_count += 1

            # Flush to disk periodically
            if self._buffer_count >= self._flush_threshold:
                self._flush_to_disk()

    def _flush_to_disk(self):
        """Flush buffer to disk (must be called with lock held)."""
        if not self._usage_buffer:
            return

        try:
            # Load existing data
            existing = {}
            if USAGE_FILE.exists():
                try:
                    with open(USAGE_FILE, 'r') as f:
                        existing = json.load(f)
                except (json.JSONDecodeError, IOError):
                    logger.warning("Could not read existing usage file, starting fresh")

            # Merge buffer into existing
            for endpoint_key, new_data in self._usage_buffer.items():
                if endpoint_key in existing:
                    # Merge counts
                    existing[endpoint_key]["total_count"] += new_data["total_count"]
                    existing[endpoint_key]["success_count"] += new_data["success_count"]
                    existing[endpoint_key]["error_count"] += new_data["error_count"]
                    existing[endpoint_key]["last_seen"] = new_data["last_seen"]

                    # Merge daily counts
                    for day, count in new_data["daily_counts"].items():
                        existing[endpoint_key]["daily_counts"][day] = (
                            existing[endpoint_key]["daily_counts"].get(day, 0) + count
                        )
                else:
                    existing[endpoint_key] = new_data

            # Add metadata
            existing["_metadata"] = {
                "last_flush": datetime.now(timezone.utc).isoformat(),
                "total_endpoints": len([k for k in existing if not k.startswith("_")]),
            }

            # Write atomically
            temp_file = USAGE_FILE.with_suffix(".tmp")
            with open(temp_file, 'w') as f:
                json.dump(existing, f, indent=2, sort_keys=True)
            temp_file.replace(USAGE_FILE)

            # Clear buffer
            self._usage_buffer.clear()
            self._buffer_count = 0

        except Exception as e:
            logger.error(f"Failed to flush usage data: {e}")


def load_usage_data() -> Dict[str, Any]:
    """Load current usage data from disk."""
    if not USAGE_FILE.exists():
        return {}

    try:
        with open(USAGE_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load usage data: {e}")
        return {}


def analyze_usage(min_days: int = 7, output_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Analyze endpoint usage patterns and identify deprecation candidates.

    Args:
        min_days: Minimum days of data required for reliable analysis (default 7)
        output_file: Optional path to write analysis report

    Returns:
        Analysis report with:
        - active_endpoints: Endpoints with recent traffic
        - zero_traffic: Endpoints with no traffic (deprecation candidates)
        - low_traffic: Endpoints with <1 request/day average
        - high_traffic: Most frequently used endpoints
    """
    data = load_usage_data()

    if not data or "_metadata" not in data:
        return {"error": "No usage data available. Wait for tracking to accumulate data."}

    # Analyze endpoints
    now = datetime.now(timezone.utc)
    endpoints = []

    for key, entry in data.items():
        if key.startswith("_"):
            continue

        try:
            first_seen = datetime.fromisoformat(entry["first_seen"].replace("Z", "+00:00"))
            last_seen = datetime.fromisoformat(entry["last_seen"].replace("Z", "+00:00"))
            days_tracked = max(1, (now - first_seen).days)
            days_since_last = (now - last_seen).days
            daily_avg = entry["total_count"] / days_tracked

            endpoints.append({
                "key": key,
                "method": entry["method"],
                "path": entry["path"],
                "total_count": entry["total_count"],
                "daily_avg": round(daily_avg, 2),
                "days_tracked": days_tracked,
                "days_since_last": days_since_last,
                "success_rate": round(
                    entry["success_count"] / entry["total_count"] * 100
                    if entry["total_count"] > 0 else 0, 1
                ),
            })
        except Exception as e:
            logger.warning(f"Could not analyze endpoint {key}: {e}")

    # Sort by usage
    endpoints.sort(key=lambda x: x["total_count"], reverse=True)

    # Categorize
    report = {
        "generated_at": now.isoformat(),
        "total_endpoints_tracked": len(endpoints),
        "high_traffic": [e for e in endpoints if e["daily_avg"] >= 10][:20],
        "medium_traffic": [e for e in endpoints if 1 <= e["daily_avg"] < 10],
        "low_traffic": [e for e in endpoints if 0 < e["daily_avg"] < 1],
        "zero_traffic_candidates": [],  # Would need external endpoint list to identify
        "stale_endpoints": [e for e in endpoints if e["days_since_last"] >= 7],
        "all_endpoints": endpoints,
    }

    # Summary statistics
    report["summary"] = {
        "high_traffic_count": len(report["high_traffic"]),
        "medium_traffic_count": len(report["medium_traffic"]),
        "low_traffic_count": len(report["low_traffic"]),
        "stale_count": len(report["stale_endpoints"]),
    }

    if output_file:
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)
        logger.info(f"Usage analysis written to {output_file}")

    return report


def get_endpoint_stats(path: str, method: str = "GET") -> Optional[Dict[str, Any]]:
    """Get usage statistics for a specific endpoint."""
    data = load_usage_data()
    key = f"{method}:{path}"
    return data.get(key)


def reset_usage_data():
    """Reset all usage data (for testing or fresh start)."""
    with _file_lock:
        if USAGE_FILE.exists():
            USAGE_FILE.unlink()
        logger.info("Usage data reset")
