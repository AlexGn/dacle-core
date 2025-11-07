"""
Health check endpoints for production monitoring

Provides HTTP endpoints for liveness and readiness probes.
Used by load balancers, orchestrators, and monitoring systems.

Security: HIGH-REL-001 - Health checks for production reliability
"""

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


class HealthStatus:
    """
    Tracks the health status of the application
    """

    def __init__(self):
        self.bot_ready = False
        self.db_connected = False
        self.redis_connected = False
        self.last_error: Optional[str] = None

    def set_bot_ready(self, ready: bool):
        """Update bot ready status"""
        self.bot_ready = ready
        if ready:
            logger.info("Health check: Bot is ready")
        else:
            logger.warning("Health check: Bot is not ready")

    def set_db_connected(self, connected: bool, error: Optional[str] = None):
        """Update database connection status"""
        self.db_connected = connected
        if not connected:
            self.last_error = error
            logger.warning(f"Health check: Database disconnected - {error}")

    def set_redis_connected(self, connected: bool, error: Optional[str] = None):
        """Update Redis connection status"""
        self.redis_connected = connected
        if not connected:
            self.last_error = error
            logger.warning(f"Health check: Redis disconnected - {error}")

    def is_healthy(self) -> bool:
        """
        Check if application is alive (liveness probe)
        Returns True if the application process is running
        """
        return True  # If we can respond, we're alive

    def is_ready(self) -> bool:
        """
        Check if application is ready to serve traffic (readiness probe)
        Returns True if bot is connected and dependencies are available
        """
        return self.bot_ready and self.db_connected

    def get_status_dict(self) -> Dict[str, any]:
        """Get detailed status as dictionary"""
        return {
            "healthy": self.is_healthy(),
            "ready": self.is_ready(),
            "bot_ready": self.bot_ready,
            "db_connected": self.db_connected,
            "redis_connected": self.redis_connected,
            "last_error": self.last_error,
        }


# Global health status instance
_health_status = HealthStatus()


def get_health_status() -> HealthStatus:
    """Get the global health status instance"""
    return _health_status


class HealthCheckHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler for health check endpoints
    """

    def do_GET(self):
        """Handle GET requests"""
        status = get_health_status()

        if self.path == "/health":
            # Liveness probe - is the process alive?
            self._respond_health(status)
        elif self.path == "/ready":
            # Readiness probe - is the app ready to serve traffic?
            self._respond_ready(status)
        elif self.path == "/status":
            # Detailed status for debugging
            self._respond_status(status)
        else:
            self._respond_not_found()

    def _respond_health(self, status: HealthStatus):
        """Respond to /health endpoint"""
        if status.is_healthy():
            self._send_json_response(200, {"status": "healthy"})
        else:
            self._send_json_response(503, {"status": "unhealthy"})

    def _respond_ready(self, status: HealthStatus):
        """Respond to /ready endpoint"""
        if status.is_ready():
            self._send_json_response(200, {"status": "ready"})
        else:
            response = {
                "status": "not_ready",
                "bot_ready": status.bot_ready,
                "db_connected": status.db_connected,
            }
            if status.last_error:
                response["error"] = status.last_error
            self._send_json_response(503, response)

    def _respond_status(self, status: HealthStatus):
        """Respond to /status endpoint with detailed info"""
        self._send_json_response(200, status.get_status_dict())

    def _respond_not_found(self):
        """Respond with 404"""
        self._send_json_response(
            404,
            {
                "error": "Not found",
                "available_endpoints": ["/health", "/ready", "/status"],
            },
        )

    def _send_json_response(self, status_code: int, data: dict):
        """Send JSON response"""
        import json

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        """Override to use Python logging instead of stderr"""
        logger.debug(f"Health check request: {format % args}")


class HealthCheckServer:
    """
    HTTP server for health check endpoints
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        """
        Initialize health check server

        Args:
            host: Host to bind to (default: 0.0.0.0 for all interfaces)
            port: Port to listen on (default: 8080)
        """
        self.host = host
        self.port = port
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        """Start the health check server in a background thread"""
        if self.server is not None:
            logger.warning("Health check server already running")
            return

        try:
            self.server = HTTPServer((self.host, self.port), HealthCheckHandler)
            self.thread = threading.Thread(target=self._run_server, daemon=True)
            self.thread.start()
            logger.info(
                f"Health check server started on http://{self.host}:{self.port}"
            )
            logger.info(f"  - Liveness:  http://{self.host}:{self.port}/health")
            logger.info(f"  - Readiness: http://{self.host}:{self.port}/ready")
            logger.info(f"  - Status:    http://{self.host}:{self.port}/status")
        except Exception as e:
            logger.error(f"Failed to start health check server: {e}")
            raise

    def _run_server(self):
        """Run the HTTP server (called in background thread)"""
        try:
            self.server.serve_forever()
        except Exception as e:
            logger.error(f"Health check server error: {e}")

    def stop(self):
        """Stop the health check server"""
        if self.server:
            logger.info("Stopping health check server...")
            self.server.shutdown()
            self.server = None
            if self.thread:
                self.thread.join(timeout=5)
                self.thread = None


# Utility functions for checking dependencies


def check_database_health(supabase_client) -> bool:
    """
    Check if database connection is healthy

    Args:
        supabase_client: Supabase client instance

    Returns:
        True if database is accessible, False otherwise
    """
    try:
        # Simple query to check connectivity
        result = supabase_client.table("projects").select("id").limit(1).execute()
        return True
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        get_health_status().set_db_connected(False, str(e))
        return False


def check_redis_health(redis_client) -> bool:
    """
    Check if Redis connection is healthy

    Args:
        redis_client: Redis client instance (can be None if Redis is optional)

    Returns:
        True if Redis is accessible or not configured, False if configured but failing
    """
    if redis_client is None:
        # Redis not configured, consider it healthy
        return True

    try:
        redis_client.ping()
        return True
    except Exception as e:
        logger.warning(f"Redis health check failed: {e}")
        get_health_status().set_redis_connected(False, str(e))
        return False


async def run_periodic_health_checks(
    supabase_client, redis_client=None, interval: int = 30
):
    """
    Run periodic health checks in the background

    Args:
        supabase_client: Supabase client for DB checks
        redis_client: Redis client for cache checks (optional)
        interval: Check interval in seconds (default: 30)
    """
    import asyncio

    logger.info(f"Starting periodic health checks (every {interval}s)")

    while True:
        try:
            # Check database
            db_healthy = check_database_health(supabase_client)
            get_health_status().set_db_connected(db_healthy)

            # Check Redis if configured
            if redis_client:
                redis_healthy = check_redis_health(redis_client)
                get_health_status().set_redis_connected(redis_healthy)

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("Health check task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in periodic health checks: {e}")
            await asyncio.sleep(interval)
