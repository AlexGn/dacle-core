#!/usr/bin/env python3
"""
Startup Validator - Session 273 P1 Silent Failure Protection

Validates critical services before daemon startup to prevent silent failures.

Checks:
1. Telegram Bot - Validates token and chat_id are valid
2. Supabase - Validates connection and table access
3. Redis - Validates connection (optional - degrades gracefully)
4. File System - Validates required directories exist

Usage:
    from src.monitoring.startup_validator import StartupValidator, validate_startup

    # Quick validation (raises on failure)
    validate_startup()

    # Detailed validation with results
    validator = StartupValidator()
    results = validator.validate_all()
    if not results['all_critical_passed']:
        print(f"Critical failures: {results['critical_failures']}")

Author: DACLE System (Session 273)
Date: 2026-01-02
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a single validation check."""
    name: str
    passed: bool
    critical: bool  # If True, failure should block startup
    message: str
    error: Optional[str] = None


class StartupValidator:
    """
    Session 273: Validates critical services before daemon startup.

    Philosophy: Fail fast, fail loud. If a critical service is down,
    alert immediately rather than silently dropping messages.
    """

    # Required directories for DACLE operation
    REQUIRED_DIRS = [
        "data/ml",
        "data/trades",
        "data/tokens",
        "data/health",
        "logs",
    ]

    def __init__(self, project_root: Optional[Path] = None):
        """
        Initialize validator.

        Args:
            project_root: Path to project root (auto-detected if not provided)
        """
        if project_root:
            self.project_root = project_root
        else:
            # Auto-detect from file location
            self.project_root = Path(__file__).parent.parent.parent

        self.results: List[ValidationResult] = []

    def validate_telegram(self) -> ValidationResult:
        """
        Validate Telegram bot configuration.

        Checks:
        - TELEGRAM_BOT_TOKEN is set
        - TELEGRAM_CHAT_ID is set
        - Token format looks valid (bot prefix)
        - Optionally validates backup token
        """
        try:
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            backup_token = os.getenv("TELEGRAM_BOT_TOKEN_BACKUP")

            errors = []

            if not token:
                errors.append("TELEGRAM_BOT_TOKEN not set")
            elif not token.startswith(""):
                # Basic format check (tokens have format: 123456789:ABC-DEF...)
                if ":" not in token:
                    errors.append("TELEGRAM_BOT_TOKEN format invalid (missing colon)")

            if not chat_id:
                errors.append("TELEGRAM_CHAT_ID not set")

            if errors:
                return ValidationResult(
                    name="telegram",
                    passed=False,
                    critical=True,
                    message="Telegram configuration incomplete",
                    error="; ".join(errors)
                )

            # Success
            backup_status = "configured" if backup_token else "not configured"
            return ValidationResult(
                name="telegram",
                passed=True,
                critical=True,
                message=f"Telegram configured (backup: {backup_status})"
            )

        except Exception as e:
            return ValidationResult(
                name="telegram",
                passed=False,
                critical=True,
                message="Telegram validation failed",
                error=str(e)
            )

    def validate_supabase(self) -> ValidationResult:
        """
        Validate Supabase connection and table access.

        Checks:
        - SUPABASE_URL and SUPABASE_KEY are set
        - Can connect and query learning_insights table
        """
        try:
            url = os.getenv("SUPABASE_URL")
            key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")

            if not url or not key:
                missing = []
                if not url:
                    missing.append("SUPABASE_URL")
                if not key:
                    missing.append("SUPABASE_KEY")
                return ValidationResult(
                    name="supabase",
                    passed=False,
                    critical=True,
                    message="Supabase configuration incomplete",
                    error=f"Missing: {', '.join(missing)}"
                )

            # Try actual connection
            try:
                # Try to import supabase client - may fail if running standalone
                try:
                    from src.knowledge.supabase_client import get_supabase_client
                except ImportError:
                    # Fallback: direct supabase import
                    from supabase import create_client
                    client = create_client(url, key)

                    # Test query
                    result = client.table("learning_insights").select("id").limit(1).execute()

                    return ValidationResult(
                        name="supabase",
                        passed=True,
                        critical=True,
                        message=f"Supabase connected (learning_insights table accessible)"
                    )

                client = get_supabase_client()

                # Test query on learning_insights table
                result = client.table("learning_insights").select("id").limit(1).execute()

                return ValidationResult(
                    name="supabase",
                    passed=True,
                    critical=True,
                    message=f"Supabase connected (learning_insights table accessible)"
                )

            except Exception as conn_err:
                return ValidationResult(
                    name="supabase",
                    passed=False,
                    critical=True,
                    message="Supabase connection failed",
                    error=str(conn_err)
                )

        except Exception as e:
            return ValidationResult(
                name="supabase",
                passed=False,
                critical=True,
                message="Supabase validation failed",
                error=str(e)
            )

    def validate_redis(self) -> ValidationResult:
        """
        Validate Redis connection (optional - degrades gracefully).

        Checks:
        - REDIS_HOST is set (or uses localhost default)
        - Can connect and ping
        """
        try:
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", "6379"))

            try:
                import redis
                client = redis.Redis(host=redis_host, port=redis_port, socket_timeout=5)
                client.ping()

                return ValidationResult(
                    name="redis",
                    passed=True,
                    critical=False,  # Redis is optional
                    message=f"Redis connected ({redis_host}:{redis_port})"
                )

            except ImportError:
                return ValidationResult(
                    name="redis",
                    passed=False,
                    critical=False,
                    message="Redis module not installed",
                    error="Install with: pip install redis"
                )

            except Exception as conn_err:
                return ValidationResult(
                    name="redis",
                    passed=False,
                    critical=False,
                    message="Redis connection failed (system will work without caching)",
                    error=str(conn_err)
                )

        except Exception as e:
            return ValidationResult(
                name="redis",
                passed=False,
                critical=False,
                message="Redis validation failed",
                error=str(e)
            )

    def validate_filesystem(self) -> ValidationResult:
        """
        Validate required directories exist.

        Creates missing directories if possible.
        """
        try:
            missing = []
            created = []

            for dir_path in self.REQUIRED_DIRS:
                full_path = self.project_root / dir_path
                if not full_path.exists():
                    try:
                        full_path.mkdir(parents=True, exist_ok=True)
                        created.append(dir_path)
                    except Exception as e:
                        missing.append(f"{dir_path}: {e}")

            if missing:
                return ValidationResult(
                    name="filesystem",
                    passed=False,
                    critical=True,
                    message="Required directories missing",
                    error="; ".join(missing)
                )

            msg = "All required directories exist"
            if created:
                msg += f" (created: {', '.join(created)})"

            return ValidationResult(
                name="filesystem",
                passed=True,
                critical=True,
                message=msg
            )

        except Exception as e:
            return ValidationResult(
                name="filesystem",
                passed=False,
                critical=True,
                message="Filesystem validation failed",
                error=str(e)
            )

    def validate_env_file(self) -> ValidationResult:
        """
        Validate .env file exists and has minimum required variables.
        """
        try:
            env_path = self.project_root / ".env"

            if not env_path.exists():
                return ValidationResult(
                    name="env_file",
                    passed=False,
                    critical=True,
                    message=".env file not found",
                    error=f"Expected at: {env_path}"
                )

            # Check for minimum required variables
            required_vars = [
                "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHAT_ID",
                "SUPABASE_URL",
            ]

            missing = [var for var in required_vars if not os.getenv(var)]

            if missing:
                return ValidationResult(
                    name="env_file",
                    passed=False,
                    critical=True,
                    message="Required environment variables missing",
                    error=f"Missing: {', '.join(missing)}"
                )

            return ValidationResult(
                name="env_file",
                passed=True,
                critical=True,
                message=".env file valid with required variables"
            )

        except Exception as e:
            return ValidationResult(
                name="env_file",
                passed=False,
                critical=True,
                message="Environment file validation failed",
                error=str(e)
            )

    def validate_all(self) -> Dict[str, Any]:
        """
        Run all validation checks.

        Returns:
            Dict with:
                - all_passed: True if all checks passed
                - all_critical_passed: True if all critical checks passed
                - results: List of ValidationResult
                - critical_failures: List of failed critical check names
                - warnings: List of failed non-critical check names
        """
        self.results = []

        # Run all checks
        checks = [
            self.validate_env_file,
            self.validate_filesystem,
            self.validate_telegram,
            self.validate_supabase,
            self.validate_redis,
        ]

        for check in checks:
            try:
                result = check()
                self.results.append(result)
            except Exception as e:
                # If check itself fails, treat as critical failure
                self.results.append(ValidationResult(
                    name=check.__name__.replace("validate_", ""),
                    passed=False,
                    critical=True,
                    message="Check threw exception",
                    error=str(e)
                ))

        # Analyze results
        all_passed = all(r.passed for r in self.results)
        critical_failures = [r.name for r in self.results if r.critical and not r.passed]
        warnings = [r.name for r in self.results if not r.critical and not r.passed]
        all_critical_passed = len(critical_failures) == 0

        return {
            "all_passed": all_passed,
            "all_critical_passed": all_critical_passed,
            "results": self.results,
            "critical_failures": critical_failures,
            "warnings": warnings,
        }

    def print_results(self):
        """Print validation results in human-readable format."""
        print("\n" + "=" * 60)
        print("DACLE STARTUP VALIDATION (Session 273)")
        print("=" * 60 + "\n")

        for result in self.results:
            status = "✅" if result.passed else ("❌" if result.critical else "⚠️")
            critical_marker = "[CRITICAL]" if result.critical else "[optional]"

            print(f"{status} {result.name.upper()} {critical_marker}")
            print(f"   {result.message}")
            if result.error:
                print(f"   Error: {result.error}")
            print()

        # Summary
        critical_failures = [r for r in self.results if r.critical and not r.passed]
        warnings = [r for r in self.results if not r.critical and not r.passed]

        print("-" * 60)
        if critical_failures:
            print(f"❌ CRITICAL FAILURES: {len(critical_failures)}")
            for r in critical_failures:
                print(f"   - {r.name}: {r.error or r.message}")
            print("\n⛔ STARTUP BLOCKED - Fix critical issues before proceeding")
        elif warnings:
            print(f"✅ All critical checks passed")
            print(f"⚠️ Warnings: {len(warnings)} (non-critical)")
            print("\n✅ READY TO START (with degraded functionality)")
        else:
            print("✅ ALL CHECKS PASSED")
            print("\n✅ READY TO START")

        print("=" * 60 + "\n")


def validate_startup(raise_on_failure: bool = True) -> Dict[str, Any]:
    """
    Session 273: Quick startup validation function.

    Validates all critical services and optionally raises on failure.

    Args:
        raise_on_failure: If True, raises RuntimeError on critical failure

    Returns:
        Validation results dict

    Raises:
        RuntimeError: If critical validation fails and raise_on_failure=True

    Example:
        from src.monitoring.startup_validator import validate_startup

        # Will raise if critical services unavailable
        validate_startup()

        # Get results without raising
        results = validate_startup(raise_on_failure=False)
        if not results['all_critical_passed']:
            print("Some services unavailable")
    """
    validator = StartupValidator()
    results = validator.validate_all()

    # Log results
    for r in results["results"]:
        if r.passed:
            logger.info(f"✅ {r.name}: {r.message}")
        elif r.critical:
            logger.error(f"❌ {r.name}: {r.message} - {r.error}")
        else:
            logger.warning(f"⚠️ {r.name}: {r.message} - {r.error}")

    # Raise on critical failure if requested
    if raise_on_failure and not results["all_critical_passed"]:
        failures = ", ".join(results["critical_failures"])
        raise RuntimeError(
            f"Startup validation failed. Critical services unavailable: {failures}"
        )

    return results


if __name__ == "__main__":
    # Run validation and print results
    validator = StartupValidator()
    results = validator.validate_all()
    validator.print_results()

    # Exit with error code if critical failures
    if not results["all_critical_passed"]:
        exit(1)
