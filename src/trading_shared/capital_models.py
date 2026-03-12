import re
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

class LeaseStatus(str, Enum):
    """Canonical statuses for a capital lease in the unified pool."""
    ACTIVE = "ACTIVE"
    REVOKE_PENDING = "REVOKE_PENDING"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"

class UnifiedCapitalConfig(BaseModel):
    """Global configuration for the unified capital pool."""
    global_cap_cents: int = Field(..., description="Global firm-wide capital limit in integer cents")
    schema_version: int = 1
    updated_at_ts: float

class StrategyRegistryEntry(BaseModel):
    """Registration for a specific strategy within the unified pool."""
    priority_tier: int = Field(..., ge=1, le=10, description="1=Highest, 10=Lowest")
    strategy_cap_cents: int
    enabled: bool = True
    min_age_seconds: int = 30

class UnifiedLease(BaseModel):
    """Record of a capital lease admission."""
    lease_id: str
    strategy_id: str
    priority_tier: int
    granted_amount_cents: int
    requested_amount_cents: int
    created_at_ts: float
    expires_at_ts: float
    status: LeaseStatus = LeaseStatus.ACTIVE
    revoke_reason: Optional[str] = None
    preempted_by: Optional[str] = None
    session_id: Optional[str] = None

# Prefix for all Redis keys in this namespace
UNIFIED_CAPITAL_PREFIX = "dacle:capital:unified"

_NAMESPACE_SANITIZER = re.compile(r"[^a-zA-Z0-9_-]+")

def normalize_capital_namespace(namespace: Optional[str]) -> Optional[str]:
    """
    Normalize optional namespace for isolated account pools.
    Returns None for empty/unset input to preserve legacy keyspace.
    """
    raw = str(namespace or "").strip()
    if not raw:
        return None
    cleaned = _NAMESPACE_SANITIZER.sub("-", raw).strip("-_")
    return cleaned or None

def get_unified_capital_prefix(namespace: Optional[str] = None) -> str:
    ns = normalize_capital_namespace(namespace)
    if not ns:
        return UNIFIED_CAPITAL_PREFIX
    return f"{UNIFIED_CAPITAL_PREFIX}:{ns}"

# Key templates
def get_config_key(namespace: Optional[str] = None) -> str:
    return f"{get_unified_capital_prefix(namespace)}:config"

def get_allocated_key(namespace: Optional[str] = None) -> str:
    return f"{get_unified_capital_prefix(namespace)}:allocated_cents"

def get_strategy_allocated_key(namespace: Optional[str] = None) -> str:
    return f"{get_unified_capital_prefix(namespace)}:strategy_allocated_cents"

def get_strategy_registry_key(namespace: Optional[str] = None) -> str:
    return f"{get_unified_capital_prefix(namespace)}:strategy_registry"

def get_active_leases_key(namespace: Optional[str] = None) -> str:
    return f"{get_unified_capital_prefix(namespace)}:active_leases"

def get_preemption_channel(namespace: Optional[str] = None) -> str:
    return f"{get_unified_capital_prefix(namespace)}:preempted"

def get_preemption_stream(namespace: Optional[str] = None) -> str:
    return f"{get_unified_capital_prefix(namespace)}:preempted_stream"

def get_pending_preemptions_key(namespace: Optional[str] = None) -> str:
    return f"{get_unified_capital_prefix(namespace)}:pending_preemptions"

def get_risk_tier_key(namespace: Optional[str] = None) -> str:
    return f"{get_unified_capital_prefix(namespace)}:risk_tier"

# Constants
CENTS_PER_USD = 100
DEFAULT_GLOBAL_CAP_USD = 1000000 # $1M default
DEFAULT_GLOBAL_CAP_CENTS = DEFAULT_GLOBAL_CAP_USD * CENTS_PER_USD
