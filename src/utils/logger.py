"""
Logging configuration for DACLE
Provides structured logging with appropriate levels
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from .config import get_config


def setup_logger(
    name: str, log_file: Optional[Path] = None, level: Optional[str] = None
) -> logging.Logger:
    """
    Set up a logger with console and optionally file output

    Args:
        name: Logger name (usually __name__)
        log_file: Optional path to log file
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
               If not provided, uses LOG_LEVEL from config

    Returns:
        Configured logger instance
    """
    # Get log level from config if not provided
    if level is None:
        config = get_config()
        level = config.log_level

    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Console handler with formatting
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)

    # Detailed format for development, simpler for production
    config = get_config()
    if config.is_development:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler if log file provided
    if log_file:
        # Create logs directory if it doesn't exist
        log_file.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# Create a default logger for the application
def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance

    Args:
        name: Logger name (usually __name__)

    Returns:
        Logger instance
    """
    return setup_logger(name)
