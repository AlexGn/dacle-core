"""
DEPRECATED: This module has been migrated to src/utils/network_resilience.py

Please update your imports:
    OLD: from dacle_core.utils.phase2_phase3_enhancements import fetch_with_retry, RetryableError
    NEW: from dacle_core.utils.network_resilience import fetch_with_retry, RetryableError

This file will be removed in a future release.

---
Network Resilience & Data Normalization Utilities.
Migrated to src/utils/network_resilience.py in Session 267.
"""

import warnings

# Emit deprecation warning on import
warnings.warn(
    "scripts.helpers.phase2_phase3_enhancements is deprecated. "
    "Use src.utils.network_resilience instead.",
    DeprecationWarning,
    stacklevel=2
)

# Import everything from the new location for backward compatibility
from dacle_core.utils.network_resilience import (
    RetryableError,
    is_retryable_status,
    fetch_with_retry,
    post_with_retry,
    DataNormalizer,
    validate_field_value,
    apply_retry_to_api_call,
)

__all__ = [
    "RetryableError",
    "is_retryable_status",
    "fetch_with_retry",
    "post_with_retry",
    "DataNormalizer",
    "validate_field_value",
    "apply_retry_to_api_call",
]
