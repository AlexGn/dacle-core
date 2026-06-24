"""
Sentry configuration for production error tracking

Session 255 - Task 9: Sentry Integration
Provides centralized error monitoring and alerting for DACLE production environment
"""

import logging
import os
from typing import Optional

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from src.utils.redaction import redact_string, redact_value

logger = logging.getLogger(__name__)


def init_sentry(
    dsn: Optional[str] = None,
    environment: Optional[str] = None,
    release: Optional[str] = None,
    traces_sample_rate: float = 0.1,
    profiles_sample_rate: float = 0.1,
) -> bool:
    """
    Initialize Sentry for error tracking and performance monitoring.

    Args:
        dsn: Sentry DSN (Data Source Name). If not provided, reads from SENTRY_DSN env var
        environment: Environment name (production, staging, development)
        release: Release version for tracking deployments
        traces_sample_rate: Percentage of transactions to trace (0.0-1.0)
        profiles_sample_rate: Percentage of transactions to profile (0.0-1.0)

    Returns:
        True if Sentry was initialized, False if skipped (no DSN)

    Example:
        >>> init_sentry(environment="production", release="v1.0")
        True
    """
    # Get DSN from parameter or environment
    dsn = dsn or os.getenv("SENTRY_DSN")

    if not dsn:
        logger.info("Sentry DSN not configured. Error tracking disabled.")
        return False

    # Auto-detect environment if not provided
    if environment is None:
        if os.getenv("ENVIRONMENT") == "production":
            environment = "production"
        elif os.getenv("VPS_PRODUCTION") == "true":
            environment = "production"
        elif os.getenv("CI") == "true":
            environment = "ci"
        else:
            environment = "development"

    # Auto-detect release from git or version file
    if release is None:
        release = _get_release_version()

    # Configure logging integration
    # Capture ERROR and above logs as breadcrumbs
    # Send ERROR level and above as Sentry events
    sentry_logging = LoggingIntegration(
        level=logging.INFO,  # Breadcrumb level (context)
        event_level=logging.ERROR,  # Event level (creates Sentry issues)
    )

    # Initialize Sentry
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        profiles_sample_rate=profiles_sample_rate,
        integrations=[sentry_logging],
        # Add custom tags
        before_send=_before_send,
        # Scrub sensitive data
        send_default_pii=False,  # Don't send personally identifiable info
    )

    logger.info(
        f"Sentry initialized: environment={environment}, release={release}, "
        f"traces_sample_rate={traces_sample_rate}"
    )
    return True


def _get_release_version() -> str:
    """
    Auto-detect release version from git or version file.

    Returns:
        Release version string (e.g., "v1.0.0" or "git-abc123")
    """
    # Try VERSION file first
    version_file = os.path.join(os.path.dirname(__file__), "..", "..", "VERSION")
    if os.path.exists(version_file):
        with open(version_file, "r") as f:
            return f.read().strip()

    # Try git commit SHA
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return f"git-{result.stdout.strip()}"
    except Exception:
        pass

    # Fallback to pyproject.toml version
    return "v0.1.0"


def _before_send(event, hint):
    """
    Filter/modify events before sending to Sentry.

    Args:
        event: Sentry event dict
        hint: Additional context

    Returns:
        Modified event or None to drop event
    """
    # Redact payload fields before any routing/tagging logic.
    event = redact_value(event)
    hint = redact_value(hint)

    # Add custom tags for DACLE-specific context
    if "tags" not in event:
        event["tags"] = {}

    # Tag critical production components
    if "extra" in event:
        # Tag conviction scoring errors
        if "conviction" in str(event.get("message", "")).lower():
            event["tags"]["component"] = "conviction_scoring"
        # Tag ML validator errors
        elif "ml" in str(event.get("message", "")).lower():
            event["tags"]["component"] = "ml_validator"
        # Tag Telegram errors
        elif "telegram" in str(event.get("message", "")).lower():
            event["tags"]["component"] = "telegram_alerts"
        # Tag data validation errors
        elif "validation" in str(event.get("message", "")).lower():
            event["tags"]["component"] = "data_validation"

    # Drop noisy errors (optional - customize as needed)
    if hint and "exception" in hint:
        exc_type = hint["exception"]
        # Example: Drop certain expected errors
        if isinstance(exc_type, KeyboardInterrupt):
            return None  # Don't track manual interrupts

    return event


def capture_exception(error: Exception, context: Optional[dict] = None):
    """
    Manually capture an exception with optional context.

    Args:
        error: Exception to capture
        context: Additional context dict to attach

    Example:
        >>> try:
        ...     risky_operation()
        ... except Exception as e:
        ...     capture_exception(e, {"token": "MONAD", "conviction": 8.2})
    """
    if context:
        safe_context = redact_value(context)
        with sentry_sdk.push_scope() as scope:
            for key, value in safe_context.items():
                scope.set_extra(key, value)
            sentry_sdk.capture_exception(error)
    else:
        sentry_sdk.capture_exception(error)


def capture_message(message: str, level: str = "info", context: Optional[dict] = None):
    """
    Capture a custom message (non-exception event).

    Args:
        message: Message to log
        level: Severity level (debug, info, warning, error, fatal)
        context: Additional context dict

    Example:
        >>> capture_message("ML model drift detected", level="warning", {"accuracy": 0.52})
    """
    safe_message = redact_string(message, max_length=2000)
    if context:
        safe_context = redact_value(context)
        with sentry_sdk.push_scope() as scope:
            for key, value in safe_context.items():
                scope.set_extra(key, value)
            sentry_sdk.capture_message(safe_message, level=level)
    else:
        sentry_sdk.capture_message(safe_message, level=level)


def set_user_context(user_id: Optional[str] = None, email: Optional[str] = None):
    """
    Set user context for error tracking (useful for multi-user systems).

    Args:
        user_id: User ID
        email: User email (will be scrubbed if send_default_pii=False)

    Example:
        >>> set_user_context(user_id="david", email="david@example.com")
    """
    sentry_sdk.set_user({"id": user_id, "email": email})


def set_tag(key: str, value: str):
    """
    Set a custom tag for all subsequent events in this context.

    Args:
        key: Tag name
        value: Tag value

    Example:
        >>> set_tag("tge_symbol", "MONAD")
    """
    sentry_sdk.set_tag(key, value)


def set_context(name: str, context: dict):
    """
    Set custom context data for error events.

    Args:
        name: Context category name
        context: Context data dict

    Example:
        >>> set_context("conviction_scoring", {
        ...     "symbol": "MONAD",
        ...     "conviction": 8.2,
        ...     "fdv_mc_ratio": 6.7
        ... })
    """
    sentry_sdk.set_context(name, redact_value(context))
