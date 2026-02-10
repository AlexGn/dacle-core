"""
HEARTBEAT Monitor — Pure Check Functions

T1.2: Each function takes data dicts and returns HeartbeatAlert | None.
Zero I/O, zero side effects — easy to test.

Channel routing:
    - Market direction  → #macro-updates (1470361576237306058)
    - High conviction   → #trades        (1468948950412431598)
    - Position health   → #trades        (1468948950412431598)
    - Staleness         → #focus         (1470789144736174326)
    - Infrastructure    → #focus         (1470789144736174326)
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class HeartbeatAlert:
    """A single actionable alert from a heartbeat check."""
    check_name: str      # e.g. "market_direction_shift"
    channel: str         # Discord channel name: "macro-updates", "trades", "focus"
    message: str         # Ready-to-post Discord message
    severity: str        # "info", "warning", "critical"


# Staleness threshold: alert when more than this many tokens are stale
STALENESS_THRESHOLD = 5

# Position health threshold: alert when PnL% drops below this
POSITION_HEALTH_THRESHOLD = -10.0

# Position critical threshold: escalate severity at this level
POSITION_CRITICAL_THRESHOLD = -25.0

# Minimum conviction score for discovery alerts
HIGH_CONVICTION_THRESHOLD = 8.0


def check_market_direction_shift(
    current_data: dict,
    last_state: dict,
) -> Optional[HeartbeatAlert]:
    """
    Check if market direction bias changed since last heartbeat.

    Args:
        current_data: Response from GET /api/macro/market-direction
                      Expected keys: bias, confidence, score
        last_state: Previous heartbeat state
                    Expected key: last_market_bias

    Returns:
        HeartbeatAlert if bias changed, None otherwise.
    """
    current_bias = current_data.get("bias")
    if not current_bias:
        return None

    prior_bias = last_state.get("last_market_bias")
    if not prior_bias:
        return None

    if current_bias == prior_bias:
        return None

    confidence = current_data.get("confidence", 0)
    return HeartbeatAlert(
        check_name="market_direction_shift",
        channel="macro-updates",
        message=(
            f"[MARKET SHIFT] Direction changed from {prior_bias} to {current_bias} "
            f"({confidence}% confidence)"
        ),
        severity="warning",
    )


def check_high_conviction_discoveries(
    unified_tokens: List[dict],
) -> Optional[HeartbeatAlert]:
    """
    Check for tokens with conviction score >= 8.0.

    Args:
        unified_tokens: List of token dicts from GET /api/tokens/unified
                        Expected keys: symbol, conviction_score, direction

    Returns:
        HeartbeatAlert if any token >= 8.0, None otherwise.
    """
    high = []
    for t in unified_tokens:
        score = t.get("conviction_score")
        if score is not None and score >= HIGH_CONVICTION_THRESHOLD:
            symbol = t.get("symbol", "???")
            direction = t.get("direction", "N/A")
            high.append(f"{symbol} {score}/10 {direction}")

    if not high:
        return None

    if len(high) == 1:
        msg = f"[DISCOVERY] {high[0]}"
    else:
        items = ", ".join(high)
        msg = f"[DISCOVERY] {len(high)} high-conviction tokens: {items}"

    return HeartbeatAlert(
        check_name="high_conviction_discovery",
        channel="trades",
        message=msg,
        severity="info",
    )


def check_position_health(
    positions: List[dict],
) -> Optional[HeartbeatAlert]:
    """
    Check for positions with unrealized PnL below -10%.

    Args:
        positions: List of position dicts from GET /api/blofin/positions
                   Expected keys: symbol, unrealized_pnl_pct

    Returns:
        HeartbeatAlert if any position < -10%, None otherwise.
    """
    unhealthy = []
    worst_pnl = 0.0

    for p in positions:
        pnl = p.get("unrealized_pnl_pct")
        if pnl is None:
            continue
        if pnl < POSITION_HEALTH_THRESHOLD:
            symbol = p.get("symbol", "???")
            unhealthy.append(f"{symbol} at {pnl}%")
            if pnl < worst_pnl:
                worst_pnl = pnl

    if not unhealthy:
        return None

    if len(unhealthy) == 1:
        msg = f"[DRAWDOWN] {unhealthy[0]} — consider reviewing"
    else:
        items = ", ".join(unhealthy)
        msg = f"[DRAWDOWN] {len(unhealthy)} positions in drawdown: {items}"

    severity = "critical" if worst_pnl < POSITION_CRITICAL_THRESHOLD else "warning"

    return HeartbeatAlert(
        check_name="position_health",
        channel="trades",
        message=msg,
        severity=severity,
    )


def check_data_staleness(
    unified_tokens: List[dict],
) -> Optional[HeartbeatAlert]:
    """
    Check for excessive stale token data.

    Args:
        unified_tokens: List of token dicts from GET /api/tokens/unified
                        Expected key: data_quality.is_stale

    Returns:
        HeartbeatAlert if stale count > 5, None otherwise.
    """
    stale_count = 0
    for t in unified_tokens:
        dq = t.get("data_quality")
        if isinstance(dq, dict) and dq.get("is_stale") is True:
            stale_count += 1

    if stale_count <= STALENESS_THRESHOLD:
        return None

    return HeartbeatAlert(
        check_name="data_staleness",
        channel="focus",
        message=f"[STALE DATA] {stale_count} tokens have stale data (>48h) — may need refresh",
        severity="warning",
    )


def check_infrastructure_health(
    health_data: dict,
) -> Optional[HeartbeatAlert]:
    """
    Check system health status.

    Args:
        health_data: Response from GET /api/system/health
                     Expected keys: overall_status, alerts

    Returns:
        HeartbeatAlert if DEGRADED or CRITICAL, None otherwise.
    """
    status = health_data.get("overall_status")
    if not status or status == "HEALTHY":
        return None

    alerts = health_data.get("alerts", [])
    alert_summary = "; ".join(alerts) if alerts else "no details"

    severity = "critical" if status == "CRITICAL" else "warning"

    return HeartbeatAlert(
        check_name="infrastructure_health",
        channel="focus",
        message=f"[SYSTEM] Status: {status} — {alert_summary}",
        severity=severity,
    )
