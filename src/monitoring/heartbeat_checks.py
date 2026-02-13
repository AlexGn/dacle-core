"""
HEARTBEAT Monitor — Pure Check Functions

T1.2: Each function takes data dicts and returns HeartbeatAlert | None.
Zero I/O, zero side effects — easy to test.

Channel routing:
    - Market direction  → #macro-updates (1470361576237306058)
    - New discoveries   → #discovery
    - Discovery recap   → #focus
    - Position health   → #focus         (1470789144736174326)
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
    positions: List[dict] = None,
) -> Optional[HeartbeatAlert]:
    """
    Check if market direction bias changed since last heartbeat.
    Includes position impact analysis (T3.4).

    Args:
        current_data: Response from GET /api/macro/market-direction
                      Expected keys: bias, confidence, score
        last_state: Previous heartbeat state
                    Expected key: last_market_bias
        positions: Current open positions to analyze impact

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
    
    # T3.4: Position Impact Analysis
    impact_msg = ""
    if positions:
        longs = [p.get("symbol") for p in positions if p.get("side") == "LONG"]
        shorts = [p.get("symbol") for p in positions if p.get("side") == "SHORT"]
        
        if current_bias == "BEARISH":
            if shorts:
                impact_msg = f"\n\u2705 Tailwind for {len(shorts)} shorts: {', '.join(shorts[:3])}"
            if longs:
                impact_msg += f"\n\u26a0\ufe0f Headwind for {len(longs)} longs: {', '.join(longs[:3])}"
        elif current_bias == "BULLISH":
            if longs:
                impact_msg = f"\n\u2705 Tailwind for {len(longs)} longs: {', '.join(longs[:3])}"
            if shorts:
                impact_msg += f"\n\u26a0\ufe0f Headwind for {len(shorts)} shorts: {', '.join(shorts[:3])}"
        elif current_bias == "NEUTRAL":
            impact_msg = "\n\u23f3 Regime is now neutral - volatility may increase."

    return HeartbeatAlert(
        check_name="market_direction_shift",
        channel="macro-updates",
        message=(
            f"[MARKET SHIFT] Direction changed from {prior_bias} to {current_bias} "
            f"({confidence}% confidence){impact_msg}"
        ),
        severity="warning",
    )


def check_high_conviction_discoveries(
    unified_tokens: List[dict],
    last_state: dict,
) -> List[HeartbeatAlert]:
    """
    Check for tokens with conviction score >= 8.0.
    T3.2: Detects NEW discoveries to trigger detailed setup cards.

    Args:
        unified_tokens: List of token dicts from GET /api/tokens/unified
        last_state: Previous state to detect NEW discoveries

    Returns:
        List of HeartbeatAlert objects.
    """
    prior_tokens = last_state.get("last_alerted_tokens", [])
    
    new_high = []
    all_high = []
    
    for t in unified_tokens:
        score = t.get("conviction_score")
        if score is not None and score >= HIGH_CONVICTION_THRESHOLD:
            symbol = t.get("symbol", "???")
            direction = t.get("direction", "N/A")
            token_str = f"{symbol} {score}/10 {direction}"
            all_high.append(token_str)
            
            if symbol not in prior_tokens:
                new_high.append(symbol)

    if not all_high:
        return []

    alerts = []
    
    # 1. NEW Discovery Alerts (T3.2: High priority focus cards)
    for symbol in new_high:
        alerts.append(HeartbeatAlert(
            check_name=f"new_discovery_{symbol}",
            channel="discovery",
            message=f"\U0001f3af **NEW HIGH CONVICTION: {symbol}**\nPreparing trade setup card...",
            severity="critical"
        ))

    # 2. General Discovery Summary (Regular update)
    if len(all_high) == 1:
        msg = f"[DISCOVERY] {all_high[0]}"
    else:
        items = ", ".join(all_high)
        msg = f"[DISCOVERY] {len(all_high)} high-conviction tokens: {items}"

    alerts.append(HeartbeatAlert(
        check_name="high_conviction_discovery",
        channel="focus",
        message=msg,
        severity="info",
    ))

    return alerts


def check_position_health(
    positions: List[dict],
    unified_tokens: List[dict] = None,
) -> Optional[HeartbeatAlert]:
    """
    Check for positions with unrealized PnL below -10% or approaching SL.
    T3.3: Enhanced with SL proximity check and actionable suggestions.

    Args:
        positions: List of position dicts from GET /api/blofin/positions
                   Expected keys: symbol, unrealized_pnl_pct, price, side
        unified_tokens: List of token dicts to find Stop Loss levels

    Returns:
        HeartbeatAlert if any position < -10% or near SL, None otherwise.
    """
    if not positions:
        return None

    unhealthy = []
    approaching_sl = []
    worst_pnl = 0.0
    
    # Map tokens for quick lookup
    token_map = {t.get("symbol"): t for t in (unified_tokens or [])}

    for p in positions:
        symbol = p.get("symbol", "???")
        pnl = p.get("unrealized_pnl_pct")
        current_price = p.get("price")
        side = p.get("side")
        
        # 1. Check Drawdown
        if pnl is not None and pnl < POSITION_HEALTH_THRESHOLD:
            unhealthy.append(f"{symbol} at {pnl}%")
            if pnl < worst_pnl:
                worst_pnl = pnl
        
        # 2. Check SL Proximity (T3.3)
        token_data = token_map.get(symbol)
        if token_data and current_price:
            # Find SL in execution_state or david_ta_overlay
            exec_state = token_data.get("execution_state") or {}
            levels = exec_state.get("execution_levels") or {}
            sl_price = levels.get("stop_loss") or levels.get("invalidation")
            
            # Fallback to david_ta_overlay
            if not sl_price:
                ta_overlay = token_data.get("consolidated", {}).get("david_ta_overlay", {})
                sl_price = ta_overlay.get("stop_loss")
            
            if sl_price:
                distance_pct = abs(current_price - sl_price) / current_price * 100
                if distance_pct < 5.0:  # Within 5% of SL
                    status = "DANGER" if distance_pct < 2.0 else "NEAR"
                    approaching_sl.append(f"{symbol} {status} SL (dist: {distance_pct:.1f}%)")

    if not unhealthy and not approaching_sl:
        return None

    msgs = []
    if unhealthy:
        if len(unhealthy) == 1:
            msgs.append(f"[DRAWDOWN] {unhealthy[0]}")
        else:
            items = ", ".join(unhealthy)
            msgs.append(f"[DRAWDOWN] {len(unhealthy)} positions in drawdown: {items}")
            
    if approaching_sl:
        items = ", ".join(approaching_sl)
        msgs.append(f"[SL PROXIMITY] {items}")
        
    # Suggestions (T3.3)
    if unhealthy or approaching_sl:
        msgs.append("\n\U0001f4a1 **Suggestions:**")
        if worst_pnl < POSITION_CRITICAL_THRESHOLD:
            msgs.append("\u2022 CRITICAL: Consider reducing position size or exiting.")
        else:
            msgs.append("\u2022 Monitor closely. Ensure 4H close rule (L030) is followed.")

    severity = "warning"
    if worst_pnl < POSITION_CRITICAL_THRESHOLD or any("DANGER" in s for s in approaching_sl):
        severity = "critical"

    return HeartbeatAlert(
        check_name="position_health",
        channel="focus",
        message="\n".join(msgs),
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
    arch_alerts = [a for a in alerts if "Architectural integrity violated" in a]
    non_arch_alerts = [a for a in alerts if "Architectural integrity violated" not in a]
    alert_summary = "; ".join(non_arch_alerts) if non_arch_alerts else "no details"
    severity = "critical" if status == "CRITICAL" else "warning"

    # Architectural noise is routed separately to logs.
    # If only architectural guardian is degraded, do not post infra alert to focus.
    if status == "DEGRADED" and arch_alerts and not non_arch_alerts:
        return None

    return HeartbeatAlert(
        check_name="infrastructure_health",
        channel="focus",
        message=f"[SYSTEM] Status: {status} — {alert_summary}",
        severity=severity,
    )


def check_architectural_guardian_health(
    health_data: dict,
) -> Optional[HeartbeatAlert]:
    """
    Route architectural guardian degradation to logs channel.

    This keeps structural/hygiene noise out of focus and preserves focus for
    operationally actionable alerts.
    """
    status = health_data.get("overall_status")
    if not status or status == "HEALTHY":
        return None

    subsystems = health_data.get("subsystems", {}) or {}
    arch = subsystems.get("architectural_guardian", {}) or {}
    arch_status = arch.get("status")
    if arch_status not in ("DEGRADED", "CRITICAL"):
        return None

    violations = arch.get("violations", 0)
    details = arch.get("details", [])
    detail_summary = "; ".join(details[:3]) if details else "no details"
    severity = "warning" if arch_status == "CRITICAL" else "info"

    return HeartbeatAlert(
        check_name="architectural_guardian_health",
        channel="logs",
        message=f"[ARCHITECTURE] Status: {arch_status} — {violations} issues ({detail_summary})",
        severity=severity,
    )
