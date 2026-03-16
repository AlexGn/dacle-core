from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

# --- Enums ---

class ExecutionState(str, Enum):
    """Execution state machine states."""
    DISCOVERED = "DISCOVERED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED_RECEIVED = "APPROVED_RECEIVED"
    REVALIDATING = "REVALIDATING"
    VETOED = "VETOED"
    READY = "READY"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    PROTECTION_SUBMITTING = "PROTECTION_SUBMITTING"
    PROTECTION_ARMED = "PROTECTION_ARMED"
    PROTECTION_FAILED = "PROTECTION_FAILED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"

class ProtectionStatus(str, Enum):
    """Status of protective orders (SL/TP)."""
    PENDING = "PENDING"
    ARMED = "ARMED"
    FAILED = "FAILED"
    N_A = "N/A"

class VetoReasonCode(str, Enum):
    """Explicit reasons for execution veto."""
    VETO_PRICE_DRIFT_EXCEEDED = "VETO_PRICE_DRIFT_EXCEEDED"
    VETO_ATR_REGIME_BREAK = "VETO_ATR_REGIME_BREAK"
    VETO_MACRO_SHOCK = "VETO_MACRO_SHOCK"
    VETO_LIQUIDITY_INSUFFICIENT = "VETO_LIQUIDITY_INSUFFICIENT"
    VETO_SLIPPAGE_EXCEEDED = "VETO_SLIPPAGE_EXCEEDED"
    VETO_RR_BELOW_MIN = "VETO_RR_BELOW_MIN"
    VETO_MIN_SIZE_NOT_MET = "VETO_MIN_SIZE_NOT_MET"
    VETO_STALE_SNAPSHOT = "VETO_STALE_SNAPSHOT"
    VETO_ORDERBOOK_UNAVAILABLE = "VETO_ORDERBOOK_UNAVAILABLE"
    VETO_ORDERBOOK_STALE = "VETO_ORDERBOOK_STALE"
    VETO_DIVERGENCE_EXCEEDED = "VETO_DIVERGENCE_EXCEEDED"
    VETO_ATR_UNAVAILABLE = "VETO_ATR_UNAVAILABLE"
    VETO_IDEMPOTENCY_CONFLICT = "VETO_IDEMPOTENCY_CONFLICT"
    VETO_WEEKEND_RESTRICTION = "VETO_WEEKEND_RESTRICTION"
    VETO_CONVICTION_BELOW_THRESHOLD = "VETO_CONVICTION_BELOW_THRESHOLD"

class WarningCode(str, Enum):
    """Non-blocking warnings for execution."""
    WARN_LIQUIDITY_THIN = "WARN_LIQUIDITY_THIN"
    WARN_AUTO_SCALED_SIZE = "WARN_AUTO_SCALED_SIZE"
    WARN_ATR_ELEVATED = "WARN_ATR_ELEVATED"
    WARN_NEAR_DRIFT_LIMIT = "WARN_NEAR_DRIFT_LIMIT"
    WARN_CONVICTION_DRIFT = "WARN_CONVICTION_DRIFT"

class ExecutionErrorCode(str, Enum):
    """Exchange and submission errors."""
    ERR_EXCHANGE_TIMEOUT = "ERR_EXCHANGE_TIMEOUT"
    ERR_EXCHANGE_REJECTED = "ERR_EXCHANGE_REJECTED"
    ERR_ORDER_SUBMIT_FAILED = "ERR_ORDER_SUBMIT_FAILED"
    ERR_CANCEL_FAILED = "ERR_CANCEL_FAILED"
    ERR_CONTEXT_GUARD_FAILED = "ERR_CONTEXT_GUARD_FAILED"
    ERR_INTERNAL_RETRY_EXHAUSTED = "ERR_INTERNAL_RETRY_EXHAUSTED"

# --- Models ---

class ExecutionLeg(BaseModel):
    """Canonical execution leg contract (Stage 1 multi-account ready)."""
    leg_id: str
    symbol: str
    side: str = Field(..., pattern="^(long|short|buy|sell)$")
    qty: Optional[float] = None
    price: Optional[float] = None
    venue: Optional[str] = None
    idempotency_key: str
    account_id: str = Field(default="primary", min_length=1, max_length=64)

class ExecutionIntent(BaseModel):
    """Canonical execution intent contract (Stage 1 multi-account ready)."""
    intent_id: str
    setup_id: str
    symbol: str
    side: str = Field(..., pattern="^(long|short)$")
    idempotency_key: str
    account_id: str = Field(default="primary", min_length=1, max_length=64)
    legs: List[ExecutionLeg] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class PreTradeCheckRequestV2(BaseModel):
    """Request for v2 pre-trade check."""
    setup_id: str
    account_id: str = Field(default="primary", min_length=1, max_length=64)
    symbol: str
    side: str = Field(..., pattern="^(long|short)$")
    entry: float
    stop_loss: float
    take_profit: float
    size_usd: Optional[float] = None
    qty: Optional[float] = None
    conviction: float = 8.0
    max_slippage_bps: int = 120
    allow_auto_scale_down: bool = True
    min_trade_size_usd: float = 100.0
    discovery_ts: Optional[datetime] = None
    discovery_atr: Optional[float] = None
    discovery_price: Optional[float] = None
    atr_current: Optional[float] = None
    idempotency_key: str

class MarketSnapshotV2(BaseModel):
    """Market snapshot at check time."""
    book_ts: datetime
    mark_price: float
    atr_current: float

class PreTradeCheckResponseV2(BaseModel):
    """Response from v2 pre-trade check."""
    state: ExecutionState
    liquidity_gate_pass: bool
    estimated_slippage_bps: int
    max_safe_size_usd: float
    recommended_size_usd: float
    recommended_adaptive_size_usd: Optional[float] = None
    rr_ratio: float
    veto_reasons: List[VetoReasonCode] = []
    warnings: List[WarningCode] = []
    snapshot: MarketSnapshotV2
    reason_codes: List[str] = []
    score_card: Optional[Dict[str, Any]] = None
    score_lineage: Optional[Dict[str, Any]] = None
    formatted_response: Optional[str] = None

class ApproveAndExecuteRequestV2(BaseModel):
    """Request to approve and execute setup."""
    setup_id: str
    account_id: str = Field(default="primary", min_length=1, max_length=64)
    approval_id: str  # discord_msg_or_button_id
    idempotency_key: str
    symbol: str
    side: str = Field(..., pattern="^(long|short)$")
    entry: float
    stop_loss: float
    take_profit: float
    size_usd: Optional[float] = None
    qty: Optional[float] = None
    conviction: float = 8.0
    use_adaptive_sizing: bool = False
    max_slippage_bps: int = 120
    allow_auto_scale_down: bool = True
    min_trade_size_usd: float = 100.0
    discovery_atr: Optional[float] = None
    discovery_price: Optional[float] = None
    atr_current: Optional[float] = None
    dry_run: bool = True
    time_in_force: str = "GTC"
    execution_policy: str = "LIMIT_ONLY"

class RevalidationSnapshotV2(BaseModel):
    """Revalidation details during execution flow."""
    price_drift_pct: float
    atr_regime_mult: float
    estimated_slippage_bps: int
    rr_ratio: float

class ApproveAndExecuteResponseV2(BaseModel):
    """Response from approve-and-execute."""
    execution_id: str
    state: ExecutionState
    effective_size_usd: float
    execution_policy: str = "LIMIT_ONLY"
    entry_order_id: Optional[str] = None
    protective_order_ids: Dict[str, str] = Field(default_factory=dict)
    protection_status: ProtectionStatus = ProtectionStatus.N_A
    order_ids: List[str] = []
    veto_reasons: List[VetoReasonCode] = []
    warnings: List[WarningCode] = []
    error_code: Optional[str] = None
    error_reason: Optional[str] = None
    error_domain: Optional[str] = None
    revalidation_snapshot: RevalidationSnapshotV2

class ExecutionStatusResponseV2(BaseModel):
    """Status details for an execution."""
    execution_id: str
    state: ExecutionState
    filled_qty: float
    remaining_qty: float
    avg_fill_price: float
    last_error_code: Optional[ExecutionErrorCode] = None
    updated_at: datetime
