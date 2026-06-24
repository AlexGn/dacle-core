#!/usr/bin/env python3
"""
Standardized Logging Configuration for DACLE Scripts

DEPRECATED: Use src.utils.logger instead.
Session 256: src.utils.logger provides the standard logging setup.

Consolidates logging setup patterns across all scripts.

Found patterns in codebase (before standardization):
- scan_tge_calendar.py: '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
- run_tge_alert_check.py: '%(asctime)s [%(levelname)s] %(name)s: %(message)s' + file handler
- consolidate_perplexity_data.py: '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
- send_notification.py: '%(levelname)s: %(message)s'
- send_tge_notification.py: '%(asctime)s - %(levelname)s - %(message)s'

Standardized to:
- Default: Simple format for CLI scripts
- Verbose: Detailed format with timestamps and module names
- File logging: Optional log file with rotation support

Usage:
    from dacle_core.utils.logger import setup_logging, get_logger

    # Simple setup (CLI scripts)
    setup_logging()
    logger = get_logger(__name__)

    # Verbose setup (background services)
    setup_logging(verbose=True)
    logger = get_logger(__name__)

    # With file logging (cron jobs, long-running services)
    setup_logging(log_file="logs/my_script.log", verbose=True)
    logger = get_logger(__name__)

    # Custom log level
    setup_logging(level="DEBUG")
    logger = get_logger(__name__)

Created: 2025-11-19 (Phase 5: Standardize Logging Patterns)
"""

import logging
import sys
from pathlib import Path
from typing import Optional


# Standardized format patterns
SIMPLE_FORMAT = "%(levelname)s: %(message)s"
VERBOSE_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Module-level flag to prevent duplicate setup
_logging_configured = False


def setup_logging(
    level: str = "INFO",
    verbose: bool = False,
    log_file: Optional[str] = None,
    force_reconfigure: bool = False,
) -> None:
    """
    Configure standardized logging for DACLE scripts.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        verbose: If True, use detailed format with timestamps and module names
        log_file: Optional path to log file. Creates parent directories if needed.
        force_reconfigure: Force reconfiguration even if already set up

    Examples:
        # Simple CLI script
        setup_logging()

        # Verbose output for debugging
        setup_logging(level="DEBUG", verbose=True)

        # Background service with file logging
        setup_logging(
            verbose=True,
            log_file="logs/tge_alerts.log"
        )
    """
    global _logging_configured

    # Prevent duplicate configuration unless forced
    if _logging_configured and not force_reconfigure:
        return

    # Clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    # Convert string level to logging constant
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Choose format based on verbosity
    console_format = VERBOSE_FORMAT if verbose else SIMPLE_FORMAT

    # Configure handlers
    handlers = []

    # Console handler (always present)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(console_format))
    handlers.append(console_handler)

    # File handler (optional)
    if log_file:
        log_path = Path(log_file)

        # Create parent directories if needed
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
        handlers.append(file_handler)

    # Apply configuration
    logging.basicConfig(
        level=numeric_level,
        handlers=handlers,
        force=True,  # Force reconfiguration
    )

    _logging_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the given name.

    This is a thin wrapper around logging.getLogger() for consistency.

    Args:
        name: Logger name (typically __name__ of the calling module)

    Returns:
        Logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("Script started")
    """
    return logging.getLogger(name)


def reset_logging():
    """
    Reset logging configuration flag.

    Useful for testing or when you need to reconfigure logging.
    """
    global _logging_configured
    _logging_configured = False


# Convenience functions for common patterns
def setup_cli_logging(level: str = "INFO"):
    """Setup logging for CLI scripts (simple format, console only)."""
    setup_logging(level=level, verbose=False)


def setup_service_logging(log_file: str, level: str = "INFO"):
    """Setup logging for background services (verbose format with file logging)."""
    setup_logging(level=level, verbose=True, log_file=log_file)


def setup_debug_logging(log_file: Optional[str] = None):
    """Setup logging for debugging (DEBUG level, verbose format)."""
    setup_logging(level="DEBUG", verbose=True, log_file=log_file)
