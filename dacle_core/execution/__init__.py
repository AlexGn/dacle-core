"""
Execution module for DACLE permission ladders and trade execution.

This module provides tiered autonomy based on conviction scores:
- Permission ladder for alert classification
- Future: Trade execution via MEXC API
"""

from dacle_core.execution.permission_ladder import (
    PermissionTier,
    PermissionDecision,
    get_permission_tier,
    load_permission_config,
)
from dacle_core.execution.execution_score import (
    ExecutionScoreResult,
    compute_execution_score,
)

__all__ = [
    "PermissionTier",
    "PermissionDecision",
    "get_permission_tier",
    "load_permission_config",
    "ExecutionScoreResult",
    "compute_execution_score",
]
