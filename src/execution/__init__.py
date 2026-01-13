"""
Execution module for DACLE permission ladders and trade execution.

This module provides tiered autonomy based on conviction scores:
- Permission ladder for alert classification
- Future: Trade execution via MEXC API
"""

from src.execution.permission_ladder import (
    PermissionTier,
    PermissionDecision,
    get_permission_tier,
    load_permission_config,
)

__all__ = [
    "PermissionTier",
    "PermissionDecision",
    "get_permission_tier",
    "load_permission_config",
]
