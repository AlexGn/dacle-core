"""
Brief Priority Scoring Engine — Phase 2.1 (Session 440)

Pure-function scorer that ranks trade setups by urgency for the daily brief.
No I/O, no side effects — all data passed in.

Scoring factors:
- Entry proximity (exponential: ≤3% = URGENT, ≤7% = READY, >7% = PATIENT)
- R:R ranking (higher = higher priority)
- TA freshness penalty (>24h = stale)
- Macro alignment bonus (direction matches market bias)
- Weekend penalty (L093: Friday 50%, Saturday ≥8.5 only, Sunday BLOCKED)
- Conviction decay (score dropped from previous = DECAYING)
"""

from datetime import datetime, timezone, timedelta
from typing import Optional


# === Weights ===
WEIGHT_PROXIMITY = 35
WEIGHT_RR = 25
WEIGHT_MACRO = 20
WEIGHT_TA_FRESHNESS = 10
WEIGHT_CONVICTION = 10

# === Thresholds ===
PROXIMITY_URGENT_PCT = 3.0
PROXIMITY_READY_PCT = 7.0
TA_STALE_HOURS = 24
SATURDAY_MIN_SCORE = 8.5


def _compute_entry_proximity(setup: dict) -> tuple[float, str]:
    """Compute distance from current price to entry zone as percentage.

    Returns (proximity_pct, urgency_tag).
    For SHORTs: entry zone is above current price (we want price to rise into it).
    For LONGs: entry zone is below current price (we want price to drop into it).
    """
    direction = setup.get("direction", "SHORT").upper()
    current_price = setup.get("current_price", 0)
    entry_low = setup.get("entry_low", 0)
    entry_high = setup.get("entry_high", 0)

    if not current_price or not entry_low or not entry_high:
        return 100.0, "PATIENT"

    if direction == "SHORT":
        # SHORT: we enter when price is in/near entry zone (high side)
        if current_price < entry_low:
            # Price dropped below entry zone — setup expired
            return -1.0, "EXPIRED"
        elif entry_low <= current_price <= entry_high:
            return 0.0, "URGENT"
        else:
            # Price above entry zone — calculate % distance from entry_high
            pct = ((current_price - entry_high) / entry_high) * 100
            if pct <= PROXIMITY_URGENT_PCT:
                return pct, "URGENT"
            elif pct <= PROXIMITY_READY_PCT:
                return pct, "READY"
            else:
                return pct, "PATIENT"
    else:
        # LONG: we enter when price drops into entry zone (low side)
        if current_price > entry_high:
            # Price above entry zone — setup expired for LONG
            return -1.0, "EXPIRED"
        elif entry_low <= current_price <= entry_high:
            return 0.0, "URGENT"
        else:
            # Price below entry zone — calculate % distance from entry_low
            pct = ((entry_low - current_price) / entry_low) * 100
            if pct <= PROXIMITY_URGENT_PCT:
                return pct, "URGENT"
            elif pct <= PROXIMITY_READY_PCT:
                return pct, "READY"
            else:
                return pct, "PATIENT"


def _proximity_score(pct: float) -> float:
    """Convert proximity percentage to a 0-100 score (closer = higher)."""
    if pct <= 0:
        return 100.0  # In zone
    if pct < 0:
        return 0.0  # Expired
    # Exponential decay: 100 at 0%, ~50 at 5%, ~20 at 10%
    return max(0, 100 * (1 - (pct / 15.0) ** 1.2))


def _weekend_day(now: datetime) -> int:
    """Return day of week (0=Monday). Handles timezone."""
    return now.weekday()


def score_setup_priority(
    setup: dict,
    macro_bias: str = "NEUTRAL",
    now: Optional[datetime] = None,
) -> dict:
    """Score a single trade setup and return enriched dict with priority metadata.

    Args:
        setup: Token setup dict with readiness, rr_ratio, entry levels, etc.
        macro_bias: Market direction ("BULLISH", "BEARISH", "NEUTRAL")
        now: Current time (for testability). Defaults to UTC now.

    Returns:
        Copy of setup with added keys:
        - priority_score: float (0-100, higher = more urgent)
        - urgency: str (URGENT/READY/PATIENT/EXPIRED/BLOCKED)
        - entry_proximity_pct: float
        - ta_stale: bool
        - macro_aligned: bool
        - decaying: bool
        - weekend_penalty: bool
    """
    if now is None:
        now = datetime.now(timezone.utc)

    result = dict(setup)
    direction = setup.get("direction", "SHORT").upper()

    # --- Weekend check (L093) ---
    weekday = _weekend_day(now)
    weekend_penalty = False

    if weekday == 6:  # Sunday
        result.update({
            "priority_score": 0.0,
            "urgency": "BLOCKED",
            "entry_proximity_pct": 0.0,
            "ta_stale": False,
            "macro_aligned": False,
            "decaying": False,
            "weekend_penalty": True,
        })
        return result

    if weekday == 5:  # Saturday
        weekend_penalty = True
    elif weekday == 4:  # Friday
        weekend_penalty = True

    # --- Entry proximity ---
    proximity_pct, urgency = _compute_entry_proximity(setup)
    prox_score = _proximity_score(proximity_pct) if proximity_pct >= 0 else 0.0

    # --- R:R ---
    rr = setup.get("rr_ratio") or 0
    rr_score = min(100, rr * 20)  # 5.0 R:R = 100

    # --- TA freshness ---
    ta_stale = False
    ta_freshness_score = 100.0
    ta_at = setup.get("ta_computed_at")
    if ta_at:
        try:
            ta_time = datetime.fromisoformat(ta_at.replace("Z", "+00:00"))
            ta_age_h = (now - ta_time).total_seconds() / 3600
            if ta_age_h > TA_STALE_HOURS:
                ta_stale = True
                ta_freshness_score = max(0, 100 - (ta_age_h - TA_STALE_HOURS) * 5)
        except (ValueError, TypeError):
            pass

    # --- Macro alignment ---
    macro_aligned = False
    macro_score = 50.0  # Neutral baseline
    if macro_bias == "BEARISH" and direction == "SHORT":
        macro_aligned = True
        macro_score = 100.0
    elif macro_bias == "BULLISH" and direction == "LONG":
        macro_aligned = True
        macro_score = 100.0
    elif macro_bias == "BEARISH" and direction == "LONG":
        macro_score = 15.0
    elif macro_bias == "BULLISH" and direction == "SHORT":
        macro_score = 15.0

    # --- Conviction decay ---
    score_val = setup.get("score", 0) or 0
    prev = setup.get("previous_score")
    decaying = prev is not None and score_val < prev

    conviction_score = min(100, score_val * 10)

    # --- Weighted total ---
    total = (
        prox_score * WEIGHT_PROXIMITY
        + rr_score * WEIGHT_RR
        + macro_score * WEIGHT_MACRO
        + ta_freshness_score * WEIGHT_TA_FRESHNESS
        + conviction_score * WEIGHT_CONVICTION
    ) / 100.0

    # Weekend penalty: reduce score
    if weekend_penalty:
        if weekday == 5:  # Saturday
            total *= 0.5
        else:  # Friday
            total *= 0.75

    # Expired override
    if urgency == "EXPIRED":
        total = 0.0

    result.update({
        "priority_score": round(total, 2),
        "urgency": urgency,
        "entry_proximity_pct": round(proximity_pct, 2) if proximity_pct >= 0 else 0.0,
        "ta_stale": ta_stale,
        "macro_aligned": macro_aligned,
        "decaying": decaying,
        "weekend_penalty": weekend_penalty,
    })
    return result


def rank_setups(
    setups: list[dict],
    macro_bias: str = "NEUTRAL",
    now: Optional[datetime] = None,
) -> list[dict]:
    """Score and rank all setups by priority.

    READY_TO_TRADE setups are scored and sorted by priority_score descending.
    Non-ready setups are appended after, in original order, with rank=0.

    Returns list of enriched setup dicts with 'rank' key.
    """
    if not setups:
        return []

    ready = []
    non_ready = []

    for s in setups:
        if s.get("readiness") == "READY_TO_TRADE":
            scored = score_setup_priority(s, macro_bias=macro_bias, now=now)
            ready.append(scored)
        else:
            enriched = dict(s)
            enriched["rank"] = 0
            enriched["priority_score"] = 0.0
            non_ready.append(enriched)

    # Sort ready setups by priority descending
    ready.sort(key=lambda x: x["priority_score"], reverse=True)

    # Assign ranks
    for i, s in enumerate(ready, 1):
        s["rank"] = i

    return ready + non_ready


def compute_opportunity_cost(
    ranked_setups: list[dict],
    positions: list[dict],
) -> Optional[dict]:
    """Compute opportunity cost: best available R:R vs current position average.

    Returns None if no setups available. Otherwise:
    {
        best_available_rr: float,
        best_symbol: str,
        current_avg_rr: float,
        upgrade_delta: float,
        next_best_symbol: str | None,
        next_best_rr: float | None,
    }
    """
    if not ranked_setups:
        return None

    best = ranked_setups[0]
    best_rr = best.get("rr_ratio", 0) or 0

    # Current position average R:R
    pos_rrs = [p.get("rr_ratio", 0) or 0 for p in positions if p.get("rr_ratio")]
    current_avg = sum(pos_rrs) / len(pos_rrs) if pos_rrs else 0

    # Next best
    next_best_symbol = None
    next_best_rr = None
    if len(ranked_setups) > 1:
        nb = ranked_setups[1]
        next_best_symbol = nb.get("symbol")
        next_best_rr = nb.get("rr_ratio", 0) or 0

    return {
        "best_available_rr": best_rr,
        "best_symbol": best.get("symbol"),
        "current_avg_rr": round(current_avg, 2),
        "upgrade_delta": round(best_rr - current_avg, 2),
        "next_best_symbol": next_best_symbol,
        "next_best_rr": next_best_rr,
    }


def annotate_changes(
    current_setups: list[dict],
    previous_setups: list[dict],
) -> list[dict]:
    """Add change annotations to each setup by comparing current vs previous state.

    Returns list of enriched setup dicts with 'change_annotation' key.
    Expired setups from previous (not in current) are appended with EXPIRED annotation.
    """
    prev_map = {s["symbol"]: s for s in previous_setups}
    curr_symbols = {s["symbol"] for s in current_setups}

    annotated = []
    for setup in current_setups:
        result = dict(setup)
        symbol = setup["symbol"]
        annotations = []

        if symbol not in prev_map:
            # New discovery
            annotations.append("NEW discovery")
        else:
            prev = prev_map[symbol]

            # Price movement
            curr_price = setup.get("current_price", 0)
            prev_price = prev.get("current_price", 0)
            entry_high = setup.get("entry_high", 0)
            entry_low = setup.get("entry_low", 0)
            direction = setup.get("direction", "SHORT").upper()

            if curr_price and prev_price and entry_high and entry_low:
                # Determine reference point for proximity
                if direction == "SHORT":
                    ref = entry_high
                    curr_dist = abs(curr_price - ref)
                    prev_dist = abs(prev_price - ref)
                else:
                    ref = entry_low
                    curr_dist = abs(curr_price - ref)
                    prev_dist = abs(prev_price - ref)

                if curr_dist < prev_dist - 0.001 * ref:
                    pct_move = round(abs(curr_price - prev_price) / prev_price * 100, 1)
                    annotations.append(f"Price moved toward entry ({pct_move}%)")
                elif curr_dist > prev_dist + 0.001 * ref:
                    pct_move = round(abs(curr_price - prev_price) / prev_price * 100, 1)
                    annotations.append(f"Price moved away from entry ({pct_move}%)")

            # Conviction change
            curr_score = setup.get("score", 0)
            prev_score = prev.get("score", 0)
            if curr_score and prev_score and abs(curr_score - prev_score) >= 0.1:
                annotations.append(f"Conviction {prev_score} → {curr_score}")

        result["change_annotation"] = " | ".join(annotations) if annotations else ""
        annotated.append(result)

    # Append expired setups (in previous but not in current)
    for symbol, prev in prev_map.items():
        if symbol not in curr_symbols:
            expired = dict(prev)
            expired["change_annotation"] = "EXPIRED — no longer in pipeline"
            annotated.append(expired)

    return annotated
