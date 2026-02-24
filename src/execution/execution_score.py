"""Canonical execution scoring and threshold policy for trade entry gating."""

from dataclasses import dataclass
from typing import Dict, Optional


ENTRY_MIN_BQS = 60.0
ENTRY_MIN_RR = 2.0
COUNTER_REGIME_CONFIDENCE = 55.0
LONG_EXECUTION_THRESHOLD = 7.0
SHORT_EXECUTION_THRESHOLD = 8.0
COUNTER_REGIME_THRESHOLD = 7.5
TA_HARD_STALE_HOURS = 4.0


@dataclass
class ExecutionScoreResult:
    execution_score: float
    execution_score_source: str
    execution_threshold: float
    execution_threshold_reason: str
    blocked: bool
    block_reason: Optional[str]
    qualified_sources: Dict[str, float]
    score_components: Dict[str, float]
    entry_score_qualified: bool
    entry_score_qualification_reason: Optional[str]


def _is_counter_regime(direction: str, market_bias: str) -> bool:
    direction_u = str(direction or "").upper()
    bias_u = str(market_bias or "").upper()
    return (
        (direction_u == "LONG" and bias_u == "BEARISH")
        or (direction_u == "SHORT" and bias_u == "BULLISH")
    )


def _compute_threshold(direction: str, market_direction: Optional[dict]) -> tuple[float, str]:
    direction_u = str(direction or "").upper()
    base_threshold = (
        LONG_EXECUTION_THRESHOLD if direction_u == "LONG" else SHORT_EXECUTION_THRESHOLD
    )

    md = market_direction if isinstance(market_direction, dict) else {}
    bias = str(md.get("bias") or "NEUTRAL").upper()
    confidence = float(md.get("confidence_pct") or 0)
    stale = bool(md.get("stale"))

    if (
        not stale
        and _is_counter_regime(direction_u, bias)
        and confidence >= COUNTER_REGIME_CONFIDENCE
    ):
        return (
            COUNTER_REGIME_THRESHOLD,
            f"Counter-regime uplift ({direction_u} vs {bias} @ {confidence:.0f}% confidence)",
        )
    return (base_threshold, f"Base {direction_u} threshold")


def _sanitize_score(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _qualify_entry_score(
    entry_score: Optional[float],
    bqs_score: Optional[float],
    rr_ratio: Optional[float],
    ta_fresh: bool,
) -> tuple[bool, Optional[str]]:
    if entry_score is None:
        return False, None
    if not ta_fresh:
        return False, "TA freshness required for entry score"
    if rr_ratio is None or rr_ratio < ENTRY_MIN_RR:
        return False, f"R:R < {ENTRY_MIN_RR:.1f}"
    if bqs_score is None or bqs_score < ENTRY_MIN_BQS:
        return False, f"BQS < {ENTRY_MIN_BQS:.0f}"
    return True, "Qualified by BQS/R:R/freshness"


def compute_execution_score(
    *,
    direction: str,
    rr_ratio: Optional[float],
    market_direction: Optional[dict],
    entry_score: Optional[float] = None,
    bqs_score: Optional[float] = None,
    quick_ta_score: Optional[float] = None,
    dacle_auto_score: Optional[float] = None,
    user_conviction: Optional[float] = None,
    ta_fresh: bool = True,
    volume_ratio: Optional[float] = None,
    ema_200_distance_pct: Optional[float] = None,
) -> ExecutionScoreResult:
    """Compute canonical score/threshold and blocking decision for execution gating."""
    # Session 442: Decoupling Check
    # If in Rocket Mode (High Vol + Price > EMA200), we allow decoupling from macro
    is_decoupling = (
        direction.upper() == "LONG" 
        and (volume_ratio or 1.0) > 3.0 
        and (ema_200_distance_pct or 0) > 0
    )
    
    if is_decoupling:
        threshold = LONG_EXECUTION_THRESHOLD
        threshold_reason = "Macro Decoupling (Rocket Momentum confirmed)"
    else:
        threshold, threshold_reason = _compute_threshold(direction, market_direction)

    entry_score = _sanitize_score(entry_score)
    quick_ta_score = _sanitize_score(quick_ta_score)
    dacle_auto_score = _sanitize_score(dacle_auto_score)
    user_conviction = _sanitize_score(user_conviction)
    bqs_score = _sanitize_score(bqs_score)

    qualified_sources: Dict[str, float] = {}
    entry_ok, entry_reason = _qualify_entry_score(entry_score, bqs_score, rr_ratio, ta_fresh)
    if entry_ok and entry_score is not None:
        qualified_sources["ENTRY_SCORE"] = entry_score
    if quick_ta_score is not None:
        qualified_sources["QUICK_TA"] = quick_ta_score
    if dacle_auto_score is not None:
        qualified_sources["DACLE_AUTO"] = dacle_auto_score
    if user_conviction is not None:
        qualified_sources["USER_PROVIDED"] = user_conviction

    priority = ["ENTRY_SCORE", "QUICK_TA", "DACLE_AUTO", "USER_PROVIDED"]
    execution_score = 0.0
    source = "NONE"
    if qualified_sources:
        max_score = max(qualified_sources.values())
        execution_score = float(max_score)
        for key in priority:
            if qualified_sources.get(key) == max_score:
                source = key
                break

    blocked = source != "NONE" and execution_score < threshold
    block_reason = None
    if blocked:
        block_reason = f"Conviction {execution_score:.1f}/10 below {threshold:.1f} threshold"

    return ExecutionScoreResult(
        execution_score=round(execution_score, 2),
        execution_score_source=source,
        execution_threshold=threshold,
        execution_threshold_reason=threshold_reason,
        blocked=blocked,
        block_reason=block_reason,
        qualified_sources=qualified_sources,
        score_components={
            "entry_score": entry_score or 0.0,
            "bqs_score": bqs_score or 0.0,
            "quick_ta_score": quick_ta_score or 0.0,
            "dacle_auto_score": dacle_auto_score or 0.0,
            "user_conviction": user_conviction or 0.0,
        },
        entry_score_qualified=entry_ok,
        entry_score_qualification_reason=entry_reason,
    )
