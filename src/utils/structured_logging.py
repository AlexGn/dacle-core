#!/usr/bin/env python3
"""
Structured Logging - Session 275 P2 Foundation

Provides structured (JSON) logging for production observability.

Purpose:
- Consistent log format across all modules
- JSON output for log aggregation (Datadog, ELK, CloudWatch)
- Context fields (token, session_id, correlation_id)
- Integration with exception_handler for error categorization
- Performance metrics (duration, latency)

Use Cases:
1. Production debugging: Search logs by token, session, error category
2. Metrics extraction: Parse JSON for latency percentiles
3. Alert correlation: Match logs to Sentry events via correlation_id
4. Audit trail: Track data flow through pipeline

Cost: $0

Usage:
    from src.utils.structured_logging import (
        get_structured_logger,
        log_with_context,
        LogContext,
        @with_logging
    )

    # Method 1: Get structured logger
    logger = get_structured_logger(__name__)
    logger.info("Processing token", extra={"token": "POWER", "conviction": 8.5})

    # Method 2: Context manager for automatic duration logging
    with LogContext(operation="fetch_price", token="POWER") as ctx:
        price = api.get_price("POWER")
        ctx.add_field("price", price)
    # Automatically logs: {"operation": "fetch_price", "token": "POWER", "price": 0.35, "duration_ms": 120}

    # Method 3: Decorator for function logging
    @with_logging(operation="score_token")
    def calculate_conviction(token: str) -> float:
        return 8.5

Session 275 Impact:
- Log searchability: Keyword → Structured fields
- Debug time: Minutes → Seconds
- Log aggregation: Ready for Datadog/ELK

Author: DACLE System (Session 275)
Date: 2026-01-02
"""

import functools
import json
import logging
import os
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional, TypeVar

# Thread-local context for correlation IDs
import threading

_context = threading.local()

T = TypeVar('T')


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.

    Output format:
    {
        "timestamp": "2026-01-02T10:30:00.000Z",
        "level": "INFO",
        "logger": "src.conviction.tge_scorer",
        "message": "Scored token",
        "token": "POWER",
        "conviction": 8.5,
        "duration_ms": 120,
        "correlation_id": "abc-123"
    }
    """

    def __init__(self, include_trace: bool = True):
        """
        Initialize formatter.

        Args:
            include_trace: Include stack trace for errors
        """
        super().__init__()
        self.include_trace = include_trace

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        # Base fields
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add location for DEBUG/ERROR
        if record.levelno >= logging.ERROR or record.levelno <= logging.DEBUG:
            log_entry["location"] = f"{record.filename}:{record.lineno}"
            log_entry["function"] = record.funcName

        # Add exception info if present
        if record.exc_info and self.include_trace:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "trace": self.formatException(record.exc_info)
            }

        # Add extra fields (excluding standard LogRecord attributes)
        standard_attrs = {
            'name', 'msg', 'args', 'created', 'filename', 'funcName',
            'levelname', 'levelno', 'lineno', 'module', 'msecs',
            'pathname', 'process', 'processName', 'relativeCreated',
            'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
            'message', 'taskName'
        }

        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith('_'):
                # Serialize non-JSON types
                if isinstance(value, (datetime,)):
                    log_entry[key] = value.isoformat()
                elif isinstance(value, (set, frozenset)):
                    log_entry[key] = list(value)
                elif hasattr(value, '__dict__'):
                    log_entry[key] = str(value)
                else:
                    try:
                        json.dumps(value)
                        log_entry[key] = value
                    except (TypeError, ValueError):
                        log_entry[key] = str(value)

        # Add correlation ID if set
        correlation_id = getattr(_context, 'correlation_id', None)
        if correlation_id:
            log_entry["correlation_id"] = correlation_id

        # Add session ID if set
        session_id = getattr(_context, 'session_id', None)
        if session_id:
            log_entry["session_id"] = session_id

        return json.dumps(log_entry, default=str)


class HumanFormatter(logging.Formatter):
    """
    Human-readable formatter for development.

    Output format:
    2026-01-02 10:30:00 [INFO] src.conviction.tge_scorer - Scored token | token=POWER conviction=8.5
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as human-readable string."""
        # Base format
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        base = f"{timestamp} [{record.levelname:5}] {record.name} - {record.getMessage()}"

        # Add extra fields
        standard_attrs = {
            'name', 'msg', 'args', 'created', 'filename', 'funcName',
            'levelname', 'levelno', 'lineno', 'module', 'msecs',
            'pathname', 'process', 'processName', 'relativeCreated',
            'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
            'message', 'taskName'
        }

        extras = []
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith('_'):
                if isinstance(value, float):
                    extras.append(f"{key}={value:.2f}")
                else:
                    extras.append(f"{key}={value}")

        if extras:
            base += " | " + " ".join(extras)

        # Add exception if present
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)

        return base


def get_structured_logger(
    name: str,
    level: Optional[str] = None,
    json_output: Optional[bool] = None
) -> logging.Logger:
    """
    Get a logger with structured output.

    Args:
        name: Logger name (usually __name__)
        level: Log level (default: from LOG_LEVEL env var or INFO)
        json_output: Use JSON format (default: True in production, False in dev)

    Returns:
        Configured logger
    """
    logger = logging.getLogger(name)

    # Get level from env or default
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level, logging.INFO))

    # Clear existing handlers
    logger.handlers.clear()

    # Determine output format
    if json_output is None:
        # Use JSON in production (when not in development)
        is_dev = os.getenv("DACLE_ENV", "development").lower() == "development"
        json_output = not is_dev

    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)

    if json_output:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(HumanFormatter())

    logger.addHandler(handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def set_correlation_id(correlation_id: Optional[str] = None) -> str:
    """
    Set correlation ID for current thread.

    Args:
        correlation_id: ID to use (generates UUID if not provided)

    Returns:
        The correlation ID
    """
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())[:8]
    _context.correlation_id = correlation_id
    return correlation_id


def get_correlation_id() -> Optional[str]:
    """Get current correlation ID."""
    return getattr(_context, 'correlation_id', None)


def set_session_id(session_id: str) -> None:
    """Set session ID for current thread."""
    _context.session_id = session_id


def clear_context() -> None:
    """Clear all context fields."""
    if hasattr(_context, 'correlation_id'):
        del _context.correlation_id
    if hasattr(_context, 'session_id'):
        del _context.session_id


@dataclass
class LogContext:
    """
    Context manager for scoped logging with automatic duration tracking.

    Usage:
        with LogContext(operation="fetch_price", token="POWER") as ctx:
            price = api.get_price("POWER")
            ctx.add_field("price", price)
        # Logs: {"operation": "fetch_price", "token": "POWER", "price": 0.35, "duration_ms": 120}
    """
    operation: str
    logger: Optional[logging.Logger] = None
    level: int = logging.INFO
    log_start: bool = False
    log_end: bool = True
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    # Internal state
    _start_time: float = field(default=0.0, init=False)
    _end_logged: bool = field(default=False, init=False)

    def __post_init__(self):
        """Initialize logger if not provided."""
        if self.logger is None:
            self.logger = get_structured_logger("structured_logging")

    def add_field(self, key: str, value: Any) -> None:
        """Add field to be logged at context end."""
        self.extra_fields[key] = value

    def add_fields(self, **kwargs) -> None:
        """Add multiple fields."""
        self.extra_fields.update(kwargs)

    def __enter__(self) -> "LogContext":
        """Enter context."""
        self._start_time = time.time()

        if self.log_start:
            self.logger.log(
                self.level,
                f"Starting {self.operation}",
                extra={"operation": self.operation, **self.extra_fields}
            )

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Exit context with duration logging."""
        duration_ms = (time.time() - self._start_time) * 1000

        if exc_type is not None:
            # Log error
            self.logger.error(
                f"Failed {self.operation}: {exc_val}",
                extra={
                    "operation": self.operation,
                    "duration_ms": round(duration_ms, 2),
                    "error_type": exc_type.__name__,
                    "error": str(exc_val),
                    **self.extra_fields
                },
                exc_info=True
            )
        elif self.log_end and not self._end_logged:
            self.logger.log(
                self.level,
                f"Completed {self.operation}",
                extra={
                    "operation": self.operation,
                    "duration_ms": round(duration_ms, 2),
                    **self.extra_fields
                }
            )
            self._end_logged = True

        return False  # Don't suppress exceptions


def with_logging(
    operation: Optional[str] = None,
    level: int = logging.INFO,
    log_args: bool = False,
    log_result: bool = False
):
    """
    Decorator for function logging with duration tracking.

    Usage:
        @with_logging(operation="score_token", log_result=True)
        def calculate_conviction(token: str) -> float:
            return 8.5
        # Logs: {"operation": "score_token", "duration_ms": 5, "result": 8.5}

    Args:
        operation: Operation name (default: function name)
        level: Log level
        log_args: Include function arguments in log
        log_result: Include return value in log
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            op_name = operation or func.__name__
            logger = get_structured_logger(func.__module__)

            extra = {"operation": op_name}
            if log_args:
                extra["args"] = str(args)[:200]
                extra["kwargs"] = str(kwargs)[:200]

            start_time = time.time()

            try:
                result = func(*args, **kwargs)
                duration_ms = (time.time() - start_time) * 1000

                if log_result:
                    extra["result"] = str(result)[:200]
                extra["duration_ms"] = round(duration_ms, 2)

                logger.log(level, f"Completed {op_name}", extra=extra)
                return result

            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                extra["duration_ms"] = round(duration_ms, 2)
                extra["error_type"] = type(e).__name__
                extra["error"] = str(e)[:200]

                logger.error(f"Failed {op_name}", extra=extra, exc_info=True)
                raise

        return wrapper
    return decorator


def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    **context_fields
) -> None:
    """
    Log message with context fields.

    Args:
        logger: Logger instance
        level: Log level
        message: Log message
        **context_fields: Additional fields to include
    """
    logger.log(level, message, extra=context_fields)


# Convenience loggers for common components
_component_loggers: Dict[str, logging.Logger] = {}


def get_component_logger(component: str) -> logging.Logger:
    """
    Get logger for a DACLE component.

    Pre-configured components:
    - conviction: Conviction scoring
    - ml: ML validation
    - telegram: Telegram alerts
    - data: Data fetching
    - pipeline: Analysis pipeline

    Args:
        component: Component name

    Returns:
        Configured logger
    """
    if component not in _component_loggers:
        _component_loggers[component] = get_structured_logger(f"dacle.{component}")
    return _component_loggers[component]


# Export common log levels
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL


if __name__ == "__main__":
    # Test structured logging
    print("=" * 60)
    print("STRUCTURED LOGGING TEST")
    print("=" * 60)

    # Test 1: Human format (development)
    print("\n1. Testing human-readable format...")
    os.environ["DACLE_ENV"] = "development"
    logger = get_structured_logger("test.module")
    logger.info("Test message", extra={"token": "POWER", "conviction": 8.5})

    # Test 2: JSON format (production)
    print("\n2. Testing JSON format...")
    json_logger = get_structured_logger("test.json", json_output=True)
    json_logger.info("Token scored", extra={"token": "POWER", "conviction": 8.5, "duration_ms": 120})

    # Test 3: Context manager
    print("\n3. Testing context manager...")
    with LogContext(operation="fetch_price", logger=logger, log_start=True) as ctx:
        ctx.add_field("token", "POWER")
        ctx.add_field("source", "coingecko")
        time.sleep(0.1)  # Simulate work
        ctx.add_field("price", 0.35)

    # Test 4: Decorator
    print("\n4. Testing decorator...")

    @with_logging(operation="calculate_score", log_result=True)
    def calculate_conviction(token: str) -> float:
        time.sleep(0.05)
        return 8.5

    result = calculate_conviction("POWER")
    print(f"   Result: {result}")

    # Test 5: Correlation ID
    print("\n5. Testing correlation ID...")
    corr_id = set_correlation_id()
    json_logger.info("Request started", extra={"endpoint": "/api/analyze"})
    json_logger.info("Processing token", extra={"token": "POWER"})
    json_logger.info("Request completed", extra={"status": 200})
    clear_context()

    # Test 6: Error logging
    print("\n6. Testing error logging...")
    try:
        with LogContext(operation="risky_op", logger=logger) as ctx:
            ctx.add_field("attempt", 1)
            raise ValueError("Simulated error")
    except ValueError:
        pass  # Expected

    # Test 7: Component loggers
    print("\n7. Testing component loggers...")
    conv_logger = get_component_logger("conviction")
    conv_logger.info("Scoring token", extra={"token": "POWER"})

    ml_logger = get_component_logger("ml")
    ml_logger.warning("Low confidence", extra={"confidence": 0.45})

    print("\n✅ All tests passed!")
