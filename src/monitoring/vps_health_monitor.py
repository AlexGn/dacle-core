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
class ProcessMemory:
    """Memory profile for a process - Session 286."""
    name: str
    pid: int
    rss_mb: float  # Resident Set Size (actual memory used)
    vms_mb: float  # Virtual Memory Size
    percent: float  # Percentage of total system memory
    num_threads: int


@dataclass
class ServiceStatus:
    """Status of a monitored service."""
    name: str
    running: bool
    pid: Optional[int]
    uptime_seconds: Optional[int]
    status_message: str
    memory_mb: Optional[float] = None  # Session 286: Add memory tracking


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

    # Session 286: Per-process memory profiling
    process_memory: Optional[List[ProcessMemory]] = None
    total_dacle_memory_mb: Optional[float] = None


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

    # Critical services that must be running (actual VPS service names)
    # Format: (service_name, is_timer_triggered)
    CRITICAL_SERVICES = [
        ("dacle-watchtower", False),  # Main TGE discovery daemon (continuous)
        ("dacle-sniper", True),  # Execution monitoring (timer-triggered)
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

        # Session 286: Collect per-process memory profiling
        process_memory, total_dacle_memory = self._profile_dacle_memory()

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
            warnings=warnings,
            process_memory=process_memory,
            total_dacle_memory_mb=total_dacle_memory
        )

    def _profile_dacle_memory(self) -> tuple[List[ProcessMemory], float]:
        """
        Profile memory usage of all DACLE-related processes.

        Session 286: Added per-process memory profiling for observability.
        Tracks Python processes related to DACLE (watchtower, sniper, etc.)

        Returns:
            Tuple of (list of ProcessMemory, total DACLE memory in MB)
        """
        dacle_processes = []
        total_mb = 0.0

        # Keywords to identify DACLE processes
        dacle_keywords = [
            'dacle', 'watchtower', 'sniper', 'health_check',
            'entry_zone', 'recovery_signal', 'telegram_tge'
        ]

        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info', 'num_threads']):
                try:
                    cmdline = ' '.join(proc.info.get('cmdline') or []).lower()
                    name = proc.info.get('name', '').lower()

                    # Check if this is a DACLE process
                    is_dacle = any(kw in cmdline or kw in name for kw in dacle_keywords)

                    if is_dacle and proc.info.get('memory_info'):
                        mem = proc.info['memory_info']
                        rss_mb = mem.rss / (1024 * 1024)
                        vms_mb = mem.vms / (1024 * 1024)
                        mem_percent = proc.memory_percent()

                        # Get a friendly process name
                        process_name = self._get_process_name(cmdline, name)

                        dacle_processes.append(ProcessMemory(
                            name=process_name,
                            pid=proc.info['pid'],
                            rss_mb=round(rss_mb, 2),
                            vms_mb=round(vms_mb, 2),
                            percent=round(mem_percent, 2),
                            num_threads=proc.info.get('num_threads', 0)
                        ))

                        total_mb += rss_mb

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

        except Exception as e:
            logger.warning(f"Memory profiling failed: {e}")

        # Sort by memory usage (highest first)
        dacle_processes.sort(key=lambda p: p.rss_mb, reverse=True)

        return dacle_processes, round(total_mb, 2)

    def _get_process_name(self, cmdline: str, name: str) -> str:
        """Extract a friendly process name from cmdline."""
        # Try to identify specific DACLE processes
        if 'dacle_watchtower' in cmdline or 'watchtower' in cmdline:
            return 'watchtower'
        elif 'sniper_monitor' in cmdline or 'sniper' in cmdline:
            return 'sniper-monitor'
        elif 'health_check_daemon' in cmdline:
            return 'health-daemon'
        elif 'entry_zone_monitor' in cmdline:
            return 'entry-zone-monitor'
        elif 'recovery_signal' in cmdline:
            return 'recovery-signal-monitor'
        elif 'telegram_tge' in cmdline:
            return 'telegram-monitor'
        elif 'python' in name:
            # Try to get script name from cmdline
            parts = cmdline.split()
            for part in parts:
                if '.py' in part:
                    return Path(part).stem
            return 'python-worker'
        return name or 'unknown'

    def _check_services(self) -> List[ServiceStatus]:
        """Check status of critical services."""
        services = []

        for service_name, is_timer in self.CRITICAL_SERVICES:
            if is_timer:
                status = self._check_timer_service(service_name)
            else:
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

    def _check_timer_service(self, service_name: str) -> ServiceStatus:
        """
        Check timer-triggered systemd service status.

        Timer-triggered services (like dacle-sniper) are not continuously running.
        They run periodically and exit. We check:
        1. If the timer is active and enabled
        2. If the last service run was successful (exit code 0)

        Args:
            service_name: Service name (e.g., "dacle-sniper")

        Returns:
            ServiceStatus for the timer-triggered service
        """
        timer_name = f"{service_name}.timer"

        try:
            # Check if timer is active
            timer_result = subprocess.run(
                ["systemctl", "is-active", timer_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            timer_active = timer_result.returncode == 0

            if not timer_active:
                return ServiceStatus(
                    name=service_name,
                    running=False,
                    pid=None,
                    uptime_seconds=None,
                    status_message=f"Timer {timer_name} not active"
                )

            # Check last run result using ExecMainStatus
            show_result = subprocess.run(
                ["systemctl", "show", service_name, "-p", "ExecMainStatus", "-p", "Result"],
                capture_output=True,
                text=True,
                timeout=5
            )

            exit_status = None
            result_status = None
            for line in show_result.stdout.split('\n'):
                if line.startswith("ExecMainStatus="):
                    exit_status = line.split('=')[1]
                elif line.startswith("Result="):
                    result_status = line.split('=')[1]

            # Timer service is healthy if timer is active and last run succeeded
            is_healthy = timer_active and result_status == "success"

            if is_healthy:
                return ServiceStatus(
                    name=service_name,
                    running=True,  # Conceptually "running" = healthy timer
                    pid=None,
                    uptime_seconds=None,
                    status_message=f"Timer active, last run: success"
                )
            else:
                return ServiceStatus(
                    name=service_name,
                    running=False,
                    pid=None,
                    uptime_seconds=None,
                    status_message=f"Timer active but last run failed: {result_status}"
                )

        except Exception as e:
            logger.error(f"Error checking timer service {service_name}: {e}")
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

            # Simple connectivity test - Session 298: Use unified 'learnings' table
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

        # Save atomically
        from src.utils.atomic_write import atomic_json_write
        atomic_json_write(filepath, health_dict)

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

        # Send Telegram alert (respects HEALTH_TELEGRAM_ALERTS env var)
        telegram_alerts_enabled = os.environ.get("HEALTH_TELEGRAM_ALERTS", "true").lower() == "true"
        if telegram_alerts_enabled:
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
        else:
            logger.info("Telegram alerts disabled (HEALTH_TELEGRAM_ALERTS=false)")

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
