from enum import Enum
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator

class TerminalReason(str, Enum):
    """Canonical terminal states for any trade intent."""
    SUCCESS = "SUCCESS"
    RISK_REJECT = "RISK_REJECT"
    CAPITAL_REJECT = "CAPITAL_REJECT"
    RATE_LIMIT_REJECT = "RATE_LIMIT_REJECT"
    STALE_INPUT = "STALE_INPUT"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    INTENT_TIMEOUT = "INTENT_TIMEOUT"
    HALT_INTERRUPT = "HALT_INTERRUPT"
    CONTROL_BLOCK = "CONTROL_BLOCK"

class DecisionSnapshot(BaseModel):
    """Snapshot of inputs used to make the trade decision."""
    price: float
    signal_score: float
    input_sequence: int
    input_latency_ms: int
    metadata: Dict[str, Any] = Field(default_factory=dict)

class GateResult(BaseModel):
    """Result of an individual control-plane gate check."""
    gate: str
    status: str  # e.g., "PASS", "FAIL", "SKIP"
    value: Any
    reason: Optional[str] = None

class TradeIntent(BaseModel):
    """
    The canonical record of a trade intent, from signal to terminal state.
    This serves as the 'Black Box' for multi-strategy auditing.
    """
    intent_id: UUID
    strategy_id: str
    venue: str = "UNKNOWN" # e.g., "Lighter", "Polymarket", "Blofin"
    symbol: str
    side: str  # "LONG", "SHORT", "BUY", "SELL"
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Decision Context
    decision_snapshot: DecisionSnapshot
    
    # Gate Pipeline History
    gate_results: List[GateResult] = Field(default_factory=list)
    
    # Terminal Outcome
    terminal_status: TerminalReason
    terminal_reason_code: Optional[str] = None
    
    # Capital Lifecycle
    lease_id: Optional[str] = None
    lease_status: str = "NONE" # "NONE", "ACTIVE", "RELEASED"

    # Execution Tracking
    venue_order_id: Optional[str] = None
    execution_latency_ms: Optional[int] = None

    @field_validator('requested_at')
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    class Config:
        use_enum_values = True
        populate_by_name = True
