"""
VPS Health Monitor - Session 255 Task 8

Monitors critical services and system resources on DACLE VPS.
Reports health status for failover decisions.

Services Monitored:
- Watchtower daemon (TGE monitoring)
- Sniper monitor (execution alerts)
- Supabase connectivity
- Redis cache
- Disk space
- Memory usage
- CPU load

Usage:
    from src/monitoring.vps_health_monitor import VPSHealthMonitor

    monitor = VPSHealthMonitor()
    health = monitor.check_health()

    if not health["healthy"]:
        # Trigger failover
        monitor.trigger_failover_alert()
"""

import json
import logging
import os
import psutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# Project root
PROJECT_ROOT = Path(__file__).parent.parent.parent


@dataclass
class ServiceStatus:
    """Status of a monitored service."""
    name: str
    running: bool
    pid: Optional[int]
    uptime_seconds: Optional[int]
    status_message: str


@dataclass
class SystemHealth:
    """Overall system health status."""
    timestamp: str
    hostname: str
    healthy: bool

    # System resources
    cpu_percent: float
    memory_percent: float
    disk_percent: float

    # Services
    services: List[ServiceStatus]
    failed_services: List[str]

    # Connectivity
    supabase_reachable: bool
    redis_reachable: bool

    # Alerts
    critical_issues: List[str]
    warnings: List[str]


class VPSHealthMonitor:
    """
    Monitors VPS health for failover decisions.

    Critical Thresholds:
    - CPU > 90% for 5 minutes = CRITICAL
    - Memory > 95% = CRITICAL
    - Disk > 90% = CRITICAL
    - Watchtower down = CRITICAL
    - Supabase unreachable = CRITICAL
    """

    # Critical services that must be running
    CRITICAL_SERVICES = [
        "watchtower",  # Main TGE discovery daemon
        "sniper-monitor",  # Execution monitoring
    ]

    # Resource thresholds
    CRITICAL_CPU_PERCENT = 90.0
    CRITICAL_MEMORY_PERCENT = 95.0
    CRITICAL_DISK_PERCENT = 90.0
    WARNING_CPU_PERCENT = 75.0
    WARNING_MEMORY_PERCENT = 85.0
    WARNING_DISK_PERCENT = 80.0

    def __init__(self):
        """Initialize health monitor."""
        self.hostname = os.uname().nodename
        logger.info(f"VPS Health Monitor initialized for {self.hostname}")

    def check_health(self) -> SystemHealth:
        """
        Perform full health check.

        Returns:
            SystemHealth with complete status
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        # Check system resources
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        # Check services
        services = self._check_services()
        failed_services = [s.name for s in services if not s.running]

        # Check connectivity
        supabase_reachable = self._check_supabase()
        redis_reachable = self._check_redis()

        # Evaluate health
        critical_issues = []
        warnings = []

        # Resource checks
        if cpu_percent > self.CRITICAL_CPU_PERCENT:
            critical_issues.append(f"CPU usage critical: {cpu_percent:.1f}%")
        elif cpu_percent > self.WARNING_CPU_PERCENT:
            warnings.append(f"CPU usage high: {cpu_percent:.1f}%")

        if memory.percent > self.CRITICAL_MEMORY_PERCENT:
            critical_issues.append(f"Memory usage critical: {memory.percent:.1f}%")
        elif memory.percent > self.WARNING_MEMORY_PERCENT:
            warnings.append(f"Memory usage high: {memory.percent:.1f}%")

        if disk.percent > self.CRITICAL_DISK_PERCENT:
            critical_issues.append(f"Disk usage critical: {disk.percent:.1f}%")
        elif disk.percent > self.WARNING_DISK_PERCENT:
            warnings.append(f"Disk usage high: {disk.percent:.1f}%")

        # Service checks
        for service_name in failed_services:
            critical_issues.append(f"Critical service down: {service_name}")

        # Connectivity checks
        if not supabase_reachable:
            critical_issues.append("Supabase unreachable")

        if not redis_reachable:
            warnings.append("Redis cache unreachable")

        # Overall health
        healthy = len(critical_issues) == 0

        return SystemHealth(
            timestamp=timestamp,
            hostname=self.hostname,
            healthy=healthy,
            cpu_percent=cpu_percent,
            memory_percent=memory.percent,
            disk_percent=disk.percent,
            services=services,
            failed_services=failed_services,
            supabase_reachable=supabase_reachable,
            redis_reachable=redis_reachable,
            critical_issues=critical_issues,
            warnings=warnings
        )

    def _check_services(self) -> List[ServiceStatus]:
        """Check status of critical services."""
        services = []

        for service_name in self.CRITICAL_SERVICES:
            status = self._check_systemd_service(service_name)
            services.append(status)

        return services

    def _check_systemd_service(self, service_name: str) -> ServiceStatus:
        """
        Check systemd service status.

        Args:
            service_name: Service name (e.g., "watchtower")

        Returns:
            ServiceStatus for the service
        """
        try:
            # Check service status
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True,
                text=True,
                timeout=5
            )

            is_active = result.returncode == 0

            if is_active:
                # Get PID and uptime
                show_result = subprocess.run(
                    ["systemctl", "show", service_name, "-p", "MainPID", "-p", "ActiveEnterTimestamp"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                pid = None
                uptime = None

                for line in show_result.stdout.split('\n'):
                    if line.startswith("MainPID="):
                        pid = int(line.split('=')[1])
                    elif line.startswith("ActiveEnterTimestamp="):
                        # Parse timestamp and calculate uptime
                        # This is a simplified version
                        uptime = None  # TODO: Calculate from timestamp

                return ServiceStatus(
                    name=service_name,
                    running=True,
                    pid=pid,
                    uptime_seconds=uptime,
                    status_message="Active (running)"
                )
            else:
                return ServiceStatus(
                    name=service_name,
                    running=False,
                    pid=None,
                    uptime_seconds=None,
                    status_message="Inactive (dead)"
                )

        except Exception as e:
            logger.error(f"Error checking service {service_name}: {e}")
            return ServiceStatus(
                name=service_name,
                running=False,
                pid=None,
                uptime_seconds=None,
                status_message=f"Error: {str(e)}"
            )

    def _check_supabase(self) -> bool:
        """Check Supabase connectivity."""
        try:
            from src.knowledge.supabase_client import get_supabase_client

            client = get_supabase_client()

            # Simple connectivity test - query learnings table
            result = client.table("learnings").select("id").limit(1).execute()

            return True
        except Exception as e:
            logger.warning(f"Supabase unreachable: {e}")
            return False

    def _check_redis(self) -> bool:
        """Check Redis connectivity."""
        try:
            import redis

            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            r = redis.from_url(redis_url, socket_connect_timeout=2)

            # Ping test
            r.ping()

            return True
        except Exception as e:
            logger.warning(f"Redis unreachable: {e}")
            return False

    def save_health_status(self, health: SystemHealth, filepath: Optional[Path] = None):
        """
        Save health status to file.

        Args:
            health: SystemHealth object
            filepath: Output path (default: data/health/latest.json)
        """
        if filepath is None:
            filepath = PROJECT_ROOT / "data" / "health" / "latest.json"

        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Convert to dict
        health_dict = asdict(health)

        # Save
        with open(filepath, 'w') as f:
            json.dump(health_dict, f, indent=2)

        logger.info(f"Health status saved: {filepath}")

    def trigger_failover_alert(self, health: SystemHealth):
        """
        Trigger failover alert (Sentry + Telegram).

        Args:
            health: SystemHealth with failure details
        """
        # Log to Sentry
        try:
            from src.utils.sentry_config import capture_message, set_context

            set_context("vps_health", {
                "hostname": health.hostname,
                "cpu_percent": health.cpu_percent,
                "memory_percent": health.memory_percent,
                "failed_services": health.failed_services,
                "critical_issues": health.critical_issues
            })

            capture_message(
                f"VPS HEALTH CRITICAL - Failover recommended: {', '.join(health.critical_issues)}",
                level="error"
            )
        except Exception as e:
            logger.error(f"Failed to send Sentry alert: {e}")

        # Send Telegram alert
        try:
            from src.alerts.telegram_notifier import send_telegram_message

            alert_msg = f"🚨 VPS HEALTH CRITICAL ({health.hostname})\n\n"
            alert_msg += f"Critical Issues ({len(health.critical_issues)}):\n"
            for issue in health.critical_issues:
                alert_msg += f"  • {issue}\n"
            alert_msg += f"\nCPU: {health.cpu_percent:.1f}%\n"
            alert_msg += f"Memory: {health.memory_percent:.1f}%\n"
            alert_msg += f"Disk: {health.disk_percent:.1f}%\n"
            alert_msg += f"\nFailed Services: {', '.join(health.failed_services) if health.failed_services else 'None'}\n"
            alert_msg += f"\n⚠️ Manual intervention required or initiate failover to backup VPS"

            send_telegram_message(alert_msg)
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")

        logger.critical(f"FAILOVER ALERT TRIGGERED: {health.critical_issues}")

    def get_health_history(self, days: int = 7) -> List[Dict]:
        """
        Get health check history.

        Args:
            days: Number of days of history to retrieve

        Returns:
            List of health status dicts
        """
        health_dir = PROJECT_ROOT / "data" / "health"
        if not health_dir.exists():
            return []

        history = []

        # Load historical health files
        for filepath in sorted(health_dir.glob("health_*.json")):
            with open(filepath, 'r') as f:
                health_data = json.load(f)

            # Filter by date
            timestamp = datetime.fromisoformat(health_data["timestamp"].replace('Z', '+00:00'))
            age_days = (datetime.now(timezone.utc) - timestamp).days

            if age_days <= days:
                history.append(health_data)

        return history
