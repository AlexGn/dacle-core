#!/usr/bin/env python3
"""
Network Resilience & Data Normalization Utilities
Session 148: Phase 2 & 3 Pipeline Enhancements

Session 267: Migrated from scripts/helpers/phase2_phase3_enhancements.py to src/utils/network_resilience.py

Standalone module for retry logic and data normalization.
This module can be imported without modifying existing files extensively.
Designed to survive auto-formatters and git operations.

Phase 2: Retry Logic with Exponential Backoff
Phase 3: Data Normalization and Sanity Validation

Created: December 17, 2025 (Session 148)
"""

import time
import logging
import re
from typing import Any, Dict, List, Optional, Union, Tuple

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

logger = logging.getLogger(__name__)


# ============================================================================
# PHASE 2: RETRY LOGIC & NETWORK RESILIENCE
# ============================================================================

class RetryableError(Exception):
    """Errors that should trigger retry (network issues, rate limits, server errors)."""
    pass


def is_retryable_status(status_code: int) -> bool:
    """
    Determine if HTTP status code indicates a retryable error.

    Retryable statuses:
    - 429: Rate limit (wait and retry)
    - 500-504: Server errors (temporary)
    - 408: Request timeout
    - 520-524: Cloudflare errors (temporary)

    Non-retryable (4xx client errors are permanent):
    - 400, 401, 403, 404, etc.
    """
    return status_code in [429, 500, 502, 503, 504, 408, 520, 521, 522, 523, 524]


@retry(
    retry=retry_if_exception_type((RetryableError, requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(4),  # 1 initial + 3 retries = 4 total attempts
    wait=wait_exponential(multiplier=1, min=2, max=10),  # 2s, 4s, 8s
    before_sleep=before_sleep_log(logger, logging.WARNING)
)
def fetch_with_retry(
    url: str,
    headers: dict,
    timeout: int = 15,
    params: Optional[dict] = None
) -> requests.Response:
    """
    Fetch URL with automatic retry on transient failures.

    Retry Strategy:
    - Initial attempt + 3 retries = 4 total attempts
    - Exponential backoff: 2s, 4s, 8s between retries
    - Special handling for rate limits (429)

    Retries on:
    - Network errors (ConnectionError, Timeout)
    - 5xx server errors (temporary)
    - 429 rate limits (with Retry-After header respect)

    Does NOT retry on:
    - 4xx client errors (permanent failures)
    - 200-399 success/redirect codes

    Args:
        url: API endpoint URL
        headers: HTTP headers dict
        timeout: Request timeout in seconds
        params: Optional query parameters dict

    Returns:
        requests.Response object

    Raises:
        RetryableError: After all retry attempts exhausted

    Usage:
        try:
            response = fetch_with_retry(url, headers, timeout=15, params=params)
        except RetryableError as e:
            logger.error(f"API failed after retries: {e}")
            return None
    """
    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)

        # Handle retryable status codes
        if is_retryable_status(response.status_code):
            # Special handling for rate limit
            if response.status_code == 429:
                # Session 340 Part 3: Reduced default from 60s to 5s, max 10s cap
                # Long waits cause pipeline timeouts - better to fail fast and move on
                retry_after = response.headers.get('Retry-After', 5)
                retry_after = min(int(retry_after), 10)  # Cap at 10 seconds max
                logger.warning(f"Rate limit (429), waiting {retry_after}s (capped at 10s)")
                time.sleep(retry_after)

            raise RetryableError(f"HTTP {response.status_code}")

        # Raise exception for other 4xx/5xx errors
        response.raise_for_status()
        return response

    except (requests.Timeout, requests.ConnectionError) as e:
        logger.warning(f"Network error: {e} - will retry")
        raise RetryableError(str(e))


@retry(
    retry=retry_if_exception_type((RetryableError, requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(4),  # 1 initial + 3 retries = 4 total attempts
    wait=wait_exponential(multiplier=1, min=2, max=10),  # 2s, 4s, 8s
    before_sleep=before_sleep_log(logger, logging.WARNING)
)
def post_with_retry(
    url: str,
    headers: Optional[dict] = None,
    json_data: Optional[dict] = None,
    timeout: int = 15
) -> requests.Response:
    """
    POST request with automatic retry on transient failures.

    Same retry strategy as fetch_with_retry but for POST requests.

    Args:
        url: API endpoint URL
        headers: Optional HTTP headers dict
        json_data: Optional JSON payload dict
        timeout: Request timeout in seconds

    Returns:
        requests.Response object

    Raises:
        RetryableError: After all retry attempts exhausted

    Usage:
        try:
            response = post_with_retry(url, json_data=payload, timeout=60)
        except RetryableError as e:
            logger.error(f"POST failed after retries: {e}")
            return None
    """
    try:
        response = requests.post(url, headers=headers, json=json_data, timeout=timeout)

        # Handle retryable status codes
        if is_retryable_status(response.status_code):
            # Special handling for rate limit
            if response.status_code == 429:
                # Session 340 Part 3: Reduced default from 60s to 5s, max 10s cap
                # Long waits cause pipeline timeouts - better to fail fast and move on
                retry_after = response.headers.get('Retry-After', 5)
                retry_after = min(int(retry_after), 10)  # Cap at 10 seconds max
                logger.warning(f"Rate limit (429), waiting {retry_after}s (capped at 10s)")
                time.sleep(retry_after)

            raise RetryableError(f"HTTP {response.status_code}")

        # Raise exception for other 4xx/5xx errors
        response.raise_for_status()
        return response

    except (requests.Timeout, requests.ConnectionError) as e:
        logger.warning(f"Network error: {e} - will retry")
        raise RetryableError(str(e))


# ============================================================================
# PHASE 3: DATA NORMALIZATION
# ============================================================================

class DataNormalizer:
    """Normalizes data from various sources into consistent formats."""

    @staticmethod
    def normalize_float_percent(value: Any, source: str = "unknown") -> Optional[float]:
        """
        Normalize percentage values to 0-100 range.

        Fixes Bug: 0.5 vs 0.50 ambiguity
        - OLD: 0.5 → treated as 50%, 0.50 → treated as 0.5% (INVERTED!)
        - NEW: 0.5 → 50.0%, 0.50 → 50.0% (CORRECT)

        Logic:
        - If value < 1.0 → decimal (0.05 = 5%)
        - If value >= 1.0 → percentage (5.0 = 5%)

        Examples:
            0.05 → 5.0%
            0.50 → 50.0%
            5.0 → 5.0%
            "50%" → 50.0%
            None → None
        """
        if value is None or value == "":
            return None

        # Handle string values (e.g., "50%", "0.5")
        if isinstance(value, str):
            value = value.strip().replace("%", "")
            try:
                value = float(value)
            except ValueError:
                logger.warning(f"Cannot parse percentage from '{value}' ({source})")
                return None

        # Convert to float
        try:
            value = float(value)
        except (ValueError, TypeError):
            logger.warning(f"Cannot convert {value} to float ({source})")
            return None

        # Normalize to 0-100 range
        if value < 0:
            logger.warning(f"Negative percentage {value} ({source})")
            return None

        if value < 1.0:
            # Decimal format: 0.05 = 5%
            normalized = value * 100
        else:
            # Percentage format: 5.0 = 5%
            normalized = value

        # Sanity check: percentage should be 0-100
        if normalized > 100:
            logger.warning(f"Percentage {normalized}% exceeds 100% ({source}), capping")
            normalized = 100.0

        return round(normalized, 2)

    @staticmethod
    def normalize_investor_list(value: Any, source: str = "unknown") -> List[str]:
        """
        Normalize investor data to list of strings.

        Fixes Bug: String investors causing character iteration
        - OLD: Code expects list, sometimes gets "a16z, Paradigm"
        - NEW: Converts strings to lists properly

        Examples:
            ["a16z", "Paradigm"] → ["a16z", "Paradigm"]
            "a16z, Paradigm" → ["a16z", "Paradigm"]
            "a16z; Paradigm" → ["a16z", "Paradigm"]
            None → []
        """
        if value is None or value == "" or value == {}:
            return []

        # Already a list
        if isinstance(value, list):
            cleaned = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    cleaned.append(item.strip())
                elif isinstance(item, dict) and item.get("name"):
                    cleaned.append(item["name"].strip())
            return cleaned

        # String format - split by comma or semicolon
        if isinstance(value, str):
            separators = [",", ";"]
            investors = [value]

            for sep in separators:
                if sep in value:
                    investors = value.split(sep)
                    break

            cleaned = [inv.strip() for inv in investors if inv.strip()]
            return cleaned

        logger.warning(f"Cannot parse investors from {type(value)} ({source})")
        return []

    @staticmethod
    def normalize_numeric(value: Any, field_name: str = "unknown") -> Optional[Union[int, float]]:
        """
        Convert string numbers to numeric types.

        Supports:
        - Plain: "100000000" → 100000000
        - Decimals: "1.5" → 1.5
        - Millions: "$100M" → 100000000
        - Billions: "1.5B" → 1500000000
        - N/A: "N/A" → None
        """
        if value is None or value == "":
            return None

        # Already numeric
        if isinstance(value, (int, float)):
            return value

        # String parsing
        if isinstance(value, str):
            # Handle N/A markers
            na_markers = ["n/a", "tbd", "tba", "unknown", "-", "—"]
            if value.strip().lower() in na_markers:
                return None

            # Remove currency symbols, commas, whitespace
            cleaned = value.strip().replace("$", "").replace(",", "").replace(" ", "")

            # Handle M/B/K suffixes
            multiplier = 1
            if cleaned.endswith("M"):
                multiplier = 1_000_000
                cleaned = cleaned[:-1]
            elif cleaned.endswith("B"):
                multiplier = 1_000_000_000
                cleaned = cleaned[:-1]
            elif cleaned.endswith("K"):
                multiplier = 1_000
                cleaned = cleaned[:-1]

            try:
                num = float(cleaned)
                result = num * multiplier
                return int(result) if result == int(result) else result
            except ValueError:
                logger.warning(f"Cannot parse numeric '{value}' for {field_name}")
                return None

        logger.warning(f"Cannot convert {type(value)} to numeric for {field_name}")
        return None

    @staticmethod
    def is_empty_value(value: Any) -> bool:
        """
        Check if value is effectively empty.

        Empty values:
        - None, {}, [], "", "  "

        NOT empty:
        - 0 (zero is valid)
        - False (boolean value)
        """
        if value is None:
            return True
        if isinstance(value, dict) and len(value) == 0:
            return True
        if isinstance(value, list) and len(value) == 0:
            return True
        if isinstance(value, str) and value.strip() == "":
            return True
        return False

    @staticmethod
    def normalize_all_fields(data: Dict, source: str = "unknown") -> Dict:
        """
        Apply normalization to all fields in a data dict.

        Call this BEFORE consolidation logic runs.

        Normalization Rules:
        1. Float percentages: float_percent, tge_unlock_pct, etc.
        2. Investor lists: investors, lead_investors
        3. Numeric values: fdv, market_cap, funding_raised_usd, etc.
        """
        # Percentage fields (0-100 range)
        percent_fields = [
            "float_percent", "tge_unlock_pct", "initial_unlock_pct",
            "float_at_tge", "circulating_percent"
        ]

        # Investor list fields
        investor_fields = ["investors", "lead_investors", "vc_backers"]

        # Numeric value fields
        numeric_fields = [
            "fdv", "fdv_low", "fdv_high", "market_cap",
            "initial_market_cap_low", "initial_market_cap_high",
            "funding_raised_usd", "total_supply", "circulating_supply",
            "circulating_supply_at_tge", "current_price",
            "listing_price_low", "listing_price_high"
        ]

        # Normalize percentages
        for field in percent_fields:
            if field in data and not DataNormalizer.is_empty_value(data[field]):
                normalized = DataNormalizer.normalize_float_percent(data[field], source)
                if normalized is not None:
                    data[field] = normalized

        # Normalize investor lists
        for field in investor_fields:
            if field in data and not DataNormalizer.is_empty_value(data[field]):
                normalized = DataNormalizer.normalize_investor_list(data[field], source)
                if normalized:
                    data[field] = normalized

        # Normalize numeric fields (only strings)
        for field in numeric_fields:
            if field in data and isinstance(data[field], str):
                normalized = DataNormalizer.normalize_numeric(data[field], field)
                if normalized is not None:
                    data[field] = normalized

        return data


# ============================================================================
# PHASE 3: SANITY VALIDATION
# ============================================================================

def validate_field_value(
    field_name: str,
    candidate_value: Any,
    all_values: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    """
    Sanity check candidate value against other sources.

    Validation Rules:
    1. Numeric outliers: Flag if >3x or <0.3x median
    2. Float percent: Must be 0-100 range
    3. FDV >= Market Cap (warning only)

    Args:
        field_name: Name of field being validated
        candidate_value: Value from priority source
        all_values: All source values (source_key → value)

    Returns:
        (is_valid, warning_message)
        is_valid=False means skip this source, try next priority

    Usage:
        is_valid, warning = validate_field_value("fdv", candidate, all_sources)
        if not is_valid:
            logger.warning(f"Sanity check failed: {warning}")
            continue  # Try next source
    """
    # Float percentage sanity check (0-100 range)
    percent_fields = ["float_percent", "tge_unlock_pct", "initial_unlock_pct"]
    if field_name in percent_fields:
        try:
            value = float(candidate_value)
            if value < 0 or value > 100:
                return False, f"{field_name} ({value}%) outside valid range (0-100%)"
        except (ValueError, TypeError):
            pass  # Non-numeric is OK

    # Numeric outlier detection (3x threshold)
    numeric_fields = ["fdv", "fdv_low", "fdv_high", "market_cap", "funding_raised_usd"]
    if field_name in numeric_fields and isinstance(candidate_value, (int, float)):
        # Collect other numeric values
        other_values = []
        for source_key, value in all_values.items():
            if isinstance(value, (int, float)) and value != candidate_value:
                other_values.append(float(value))

        if len(other_values) >= 2:
            # Calculate median
            sorted_values = sorted(other_values)
            median = sorted_values[len(sorted_values) // 2]

            if median > 0:
                ratio = candidate_value / median
                if ratio > 3.0:
                    return False, f"{field_name} (${candidate_value:,.0f}) is {ratio:.1f}x higher than median (${median:,.0f})"
                elif ratio < 0.3:
                    return False, f"{field_name} (${candidate_value:,.0f}) is {ratio:.1f}x lower than median (${median:,.0f})"

    # Relational check (FDV >= Market Cap) - warning only
    if field_name == "fdv" and "market_cap" in all_values:
        try:
            mc = float(all_values["market_cap"])
            fdv = float(candidate_value)
            if fdv < mc:
                logger.warning(f"FDV (${fdv:,.0f}) < Market Cap (${mc:,.0f}) - unusual but possible")
        except (ValueError, TypeError):
            pass

    return True, None


# ============================================================================
# INTEGRATION HELPERS
# ============================================================================

def apply_retry_to_api_call(
    api_function,
    *args,
    use_retry: bool = True,
    **kwargs
) -> Optional[Any]:
    """
    Wrapper to apply retry logic to any API call function.

    Usage:
        # Instead of: response = requests.get(url, headers=headers)
        # Use: response = apply_retry_to_api_call(requests.get, url, headers=headers)
    """
    if not use_retry:
        return api_function(*args, **kwargs)

    try:
        if api_function == requests.get:
            url = args[0] if args else kwargs.get('url')
            headers = kwargs.get('headers', {})
            timeout = kwargs.get('timeout', 15)
            params = kwargs.get('params')
            return fetch_with_retry(url, headers, timeout, params)
        else:
            # For other functions, call directly (no retry wrapper available)
            return api_function(*args, **kwargs)
    except RetryableError as e:
        logger.error(f"API call failed after retries: {e}")
        return None


# Export main classes and functions
__all__ = [
    'RetryableError',
    'is_retryable_status',
    'fetch_with_retry',
    'DataNormalizer',
    'validate_field_value',
    'apply_retry_to_api_call'
]
