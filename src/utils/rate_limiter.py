"""
Rate limiting utilities for external API calls

Provides decorators and utilities to prevent API quota exhaustion
and implement retry logic with exponential backoff.

Security: Addresses HIGH-SEC-001 from security audit
"""

import functools
import logging
import time
from typing import Callable, TypeVar, cast
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Type variable for generic function decoration
F = TypeVar('F', bound=Callable)


def rate_limit(calls: int, period: int):
    """
    Decorator to rate limit function calls

    Args:
        calls: Number of calls allowed
        period: Time period in seconds

    Usage:
        @rate_limit(calls=10, period=1)  # 10 calls per second
        def my_api_call():
            ...

    Note: This is a simple in-memory rate limiter. For distributed
    systems, use Redis-based rate limiting instead.
    """
    min_interval = period / calls
    last_called = [0.0]  # Use list to allow modification in closure

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Calculate time since last call
            elapsed = time.time() - last_called[0]
            left_to_wait = min_interval - elapsed

            if left_to_wait > 0:
                logger.debug(
                    f"Rate limit: waiting {left_to_wait:.2f}s before calling {func.__name__}"
                )
                time.sleep(left_to_wait)

            # Update last called time
            last_called[0] = time.time()

            # Call the function
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


def get_retry_session(
    total_retries: int = 3,
    backoff_factor: float = 2.0,
    status_forcelist: tuple = (429, 500, 502, 503, 504),
    allowed_methods: tuple = ("GET", "POST"),
) -> 'requests.Session':
    """
    Create a requests.Session with retry logic

    Args:
        total_retries: Maximum number of retry attempts
        backoff_factor: Multiplier for exponential backoff (1s, 2s, 4s, ...)
        status_forcelist: HTTP status codes that trigger retry
        allowed_methods: HTTP methods that can be retried

    Returns:
        Configured requests.Session with retry adapter

    Usage:
        session = get_retry_session()
        response = session.get("https://api.example.com")
        # Automatically retries on 429, 500, 502, 503, 504
        # With delays: 1s, 2s, 4s

    Example retry timeline:
        Attempt 1: Immediate
        Attempt 2: Wait 1s (backoff_factor * 0.5)
        Attempt 3: Wait 2s (backoff_factor * 1)
        Attempt 4: Wait 4s (backoff_factor * 2)
    """
    import requests

    session = requests.Session()

    # Configure retry strategy
    retry_strategy = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=allowed_methods,
        raise_on_status=False,  # Don't raise on retry exhaustion
    )

    # Create HTTP adapter with retry strategy
    adapter = HTTPAdapter(max_retries=retry_strategy)

    # Mount adapter for both http and https
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple = (Exception,)
):
    """
    Decorator to retry function calls with exponential backoff

    Args:
        max_attempts: Maximum number of attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap in seconds
        exceptions: Tuple of exception types to catch and retry

    Usage:
        @retry_with_backoff(max_attempts=3, base_delay=2)
        def flaky_api_call():
            response = requests.get("https://api.example.com")
            response.raise_for_status()
            return response.json()

    Backoff formula: min(base_delay * (2 ** attempt), max_delay)
    Example with base_delay=1:
        Attempt 1: Immediate
        Attempt 2: Wait 1s
        Attempt 3: Wait 2s
        Attempt 4: Wait 4s (if max_attempts > 3)
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts - 1:
                        # Last attempt failed, re-raise
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    # Calculate exponential backoff delay
                    delay = min(base_delay * (2 ** attempt), max_delay)

                    logger.warning(
                        f"{func.__name__} failed (attempt {attempt + 1}/{max_attempts}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )

                    time.sleep(delay)

            # Should never reach here, but satisfy type checker
            raise RuntimeError(f"{func.__name__} exhausted retries")

        return cast(F, wrapper)

    return decorator


# Example usage:
# from src.utils.rate_limiter import rate_limit, get_retry_session, retry_with_backoff
#
# class APIClient:
#     def __init__(self):
#         self.session = get_retry_session(total_retries=3, backoff_factor=2)
#
#     @rate_limit(calls=10, period=1)  # 10 calls per second
#     @retry_with_backoff(max_attempts=3)
#     def fetch_data(self, endpoint: str):
#         response = self.session.get(f"https://api.example.com/{endpoint}")
#         response.raise_for_status()
#         return response.json()
