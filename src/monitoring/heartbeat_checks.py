"""
HEARTBEAT Monitor — Pure Check Functions

T1.2: Each function takes data dicts and returns HeartbeatAlert | None.
Zero I/O, zero side effects — easy to test.

Channel routing:
    - Market direction  → #macro-updates (1470361576237306058)
    - Regime re-eval    → #trades        (P1.3: re-run PTC on direction shift)
    - New discoveries   → #discovery
    - Discovery recap   → #focus
    - Position health   → #focus         (1470789144736174326)
    - Staleness         → #focus         (1470789144736174326)
    - Infrastructure    → #focus         (1470789144736174326)
    - Disk space        → #focus         (Session 427)
    - Redis health      → #focus         (Session 427)
    - Process memory    → #focus         (Session 427)
    - Calibration drift → #focus
    - Regime prediction → #macro-updates
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any, List, Optional


@dataclass
class HeartbeatAlert:
    """A single actionable alert from a heartbeat check."""
    check_name: str      # e.g. "market_direction_shift"
    channel: str         # Discord channel name: "macro-updates", "trades", "focus"
    message: str         # Ready-to-post Discord message
    severity: str        # "info", "warning", "critical"
    meta: Optional[dict[str, Any]] = None


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

    confidence = current_data.get("confidence_pct", current_data.get("confidence", 0)) or 0
    score = float(current_data.get("score", 0) or 0)

    # Actionable recommendation based on new bias
    abs_score = abs(score)
    if current_bias == "BEARISH":
        action = "Look for SHORT setups" + (" (1.2x)" if abs_score >= 0.60 else " (1.0x)")
    elif current_bias == "BULLISH":
        action = "Look for LONG setups" + (" (1.2x)" if abs_score >= 0.60 else " (1.0x)")
    else:
        action = "Both directions viable (0.75x)"

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
            f"\u26a1 [MARKET SHIFT] {prior_bias} \u2192 {current_bias} "
            f"({confidence}% confidence, score {score:+.2f})"
            f"{impact_msg}"
            f"\n\u27a1\ufe0f {action}"
        ),
        severity="warning",
    )


def check_regime_shift_reeval(
    current_data: dict,
    last_state: dict,
    active_setups: List[dict],
) -> Optional[HeartbeatAlert]:
    """When market direction shifts, flag active setups that need PTC re-evaluation.

    Args:
        current_data: Market direction data (bias, confidence, score).
        last_state: Previous heartbeat state with last_market_bias.
        active_setups: List of dicts with {token, direction, entry, sl, target} for
                       tokens with recent execution_state.json.

    Returns:
        HeartbeatAlert with meta containing affected setups, or None.
    """
    current_bias = current_data.get("bias")
    prior_bias = last_state.get("last_market_bias")

    if not current_bias or not prior_bias:
        return None
    if current_bias == prior_bias:
        return None
    if not active_setups:
        return None

    # Find setups that conflict with the new regime
    affected = []
    for setup in active_setups:
        direction = setup.get("direction", "").upper()
        # BEARISH shift hurts LONG setups, BULLISH shift hurts SHORT setups
        if current_bias == "BEARISH" and direction == "LONG":
            affected.append(setup)
        elif current_bias == "BULLISH" and direction == "SHORT":
            affected.append(setup)
        elif current_bias == "NEUTRAL":
            affected.append(setup)  # Re-eval all on neutral shift

    if not affected:
        return None

    tokens_str = ", ".join(s.get("token", "?") for s in affected)
    return HeartbeatAlert(
        check_name="regime_shift_reeval",
        channel="trades",
        message=(
            f"**REGIME SHIFT RE-EVAL** \u2014 {prior_bias} -> {current_bias}\n"
            f"Re-running PTC for {len(affected)} active setup(s): {tokens_str}"
        ),
        severity="warning",
        meta={"affected_setups": affected, "new_bias": current_bias, "old_bias": prior_bias},
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


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def check_zero_usage_gap_sustained(
    gap_report: dict,
    last_state: dict,
    now: Optional[datetime] = None,
    cooldown_hours: int = 24,
) -> Optional[HeartbeatAlert]:
    """
    Alert when zero-usage endpoint gaps persist, routed to #logs.

    Suppresses duplicates for unchanged fingerprints during cooldown window.
    """
    payload = gap_report or {}
    groups = payload.get("gaps", {}) or {}
    underused = groups.get("underused_or_dead", []) or []
    zero_usage = [g for g in underused if "Zero-usage endpoint coverage" in str(g.get("title", ""))]
    if not zero_usage:
        return None

    categories = sorted(
        {
            str(item.get("title", "")).replace("Zero-usage endpoint coverage in ", "").strip()
            for item in zero_usage
        }
    )
    fingerprint = hashlib.sha256("|".join(categories).encode("utf-8")).hexdigest()[:16]

    ts_now = now or datetime.now(timezone.utc)
    last_fp = (last_state or {}).get("last_zero_usage_fingerprint")
    last_alert = _parse_iso((last_state or {}).get("last_zero_usage_alert_utc"))
    if last_fp == fingerprint and last_alert is not None:
        age_h = (ts_now - last_alert).total_seconds() / 3600.0
        if age_h < max(1, cooldown_hours):
            return None

    preview = ", ".join(categories[:3])
    if len(categories) > 3:
        preview = f"{preview}, +{len(categories) - 3} more"
    return HeartbeatAlert(
        check_name="zero_usage_gap_sustained",
        channel="logs",
        severity="warning",
        message=(
            "[GAP COVERAGE] Sustained zero-usage endpoint categories (7d): "
            f"{len(categories)} ({preview})."
        ),
        meta={"fingerprint": fingerprint, "categories": categories},
    )


# =============================================================================
# Session 427: Disk Space, Redis Health, Process Memory Checks
# =============================================================================

# Disk space thresholds
DISK_WARN_PERCENT = 80.0
DISK_CRITICAL_PERCENT = 90.0

# Redis health thresholds
REDIS_MEMORY_WARN_MB = 256
REDIS_KEY_COUNT_WARN = 100_000

# Process memory threshold (RSS in MB)
PROCESS_MEMORY_WARN_MB = 512


def check_disk_space(
    disk_usage_tuple: tuple,
) -> Optional[HeartbeatAlert]:
    """
    Check disk usage percentage from shutil.disk_usage() output.

    Args:
        disk_usage_tuple: (total, used, free) in bytes from shutil.disk_usage("/")

    Returns:
        HeartbeatAlert if usage >= 80%, None otherwise.
    """
    total, used, free = disk_usage_tuple
    if total <= 0:
        return None

    usage_pct = (used / total) * 100

    if usage_pct >= DISK_CRITICAL_PERCENT:
        free_gb = free / (1024 ** 3)
        return HeartbeatAlert(
            check_name="disk_space",
            channel="focus",
            message=(
                f"[DISK CRITICAL] Usage at {usage_pct:.1f}% "
                f"({free_gb:.1f} GB free) — immediate action required"
            ),
            severity="critical",
        )

    if usage_pct >= DISK_WARN_PERCENT:
        free_gb = free / (1024 ** 3)
        return HeartbeatAlert(
            check_name="disk_space",
            channel="focus",
            message=(
                f"[DISK WARNING] Usage at {usage_pct:.1f}% "
                f"({free_gb:.1f} GB free) — consider cleanup"
            ),
            severity="warning",
        )

    return None


def check_redis_health(
    redis_info: dict,
) -> Optional[HeartbeatAlert]:
    """
    Check Redis health from redis.info() output.

    Args:
        redis_info: Dict from redis.info() with keys like used_memory, db0, etc.

    Returns:
        HeartbeatAlert if memory or key count exceeds thresholds, None otherwise.
    """
    issues = []

    # Check memory usage
    used_memory_bytes = redis_info.get("used_memory", 0)
    used_memory_mb = used_memory_bytes / (1024 * 1024)
    if used_memory_mb > REDIS_MEMORY_WARN_MB:
        issues.append(f"memory {used_memory_mb:.0f}MB (>{REDIS_MEMORY_WARN_MB}MB)")

    # Check total key count across all DBs
    total_keys = 0
    for key, value in redis_info.items():
        if key.startswith("db") and isinstance(value, dict):
            total_keys += value.get("keys", 0)
    if total_keys > REDIS_KEY_COUNT_WARN:
        issues.append(f"{total_keys:,} keys (>{REDIS_KEY_COUNT_WARN:,})")

    if not issues:
        return None

    severity = "critical" if used_memory_mb > REDIS_MEMORY_WARN_MB * 2 else "warning"
    return HeartbeatAlert(
        check_name="redis_health",
        channel="focus",
        message=f"[REDIS] {'; '.join(issues)}",
        severity=severity,
    )


# =============================================================================
# Phase 5: Position Aging Check
# =============================================================================

POSITION_AGING_THRESHOLD_HOURS = 48


def update_position_first_seen(
    current_positions: list[dict],
    existing_first_seen: dict[str, str],
    now: datetime = None,
) -> dict[str, str]:
    """Update first-seen tracking: add new positions, remove closed ones."""
    ts_now = now or datetime.now(timezone.utc)
    current_symbols = {p.get("symbol") for p in current_positions if p.get("symbol")}

    updated = {}
    for symbol in current_symbols:
        if symbol in existing_first_seen:
            updated[symbol] = existing_first_seen[symbol]
        else:
            updated[symbol] = ts_now.isoformat()

    return updated


def check_position_aging(
    positions: list[dict],
    position_first_seen: dict[str, str],
    now: datetime = None,
) -> Optional[HeartbeatAlert]:
    """Alert if any position has been open >= 48h based on first-seen tracking."""
    if not positions:
        return None

    ts_now = now or datetime.now(timezone.utc)
    aging = []

    for p in positions:
        symbol = p.get("symbol")
        if not symbol or symbol not in position_first_seen:
            continue

        first_seen_dt = _parse_iso(position_first_seen[symbol])
        if first_seen_dt is None:
            continue

        age_hours = (ts_now - first_seen_dt).total_seconds() / 3600.0
        if age_hours >= POSITION_AGING_THRESHOLD_HOURS:
            aging.append((symbol, int(age_hours)))

    if not aging:
        return None

    parts = [f"{sym} open for {hrs}h" for sym, hrs in aging]
    msg = "[AGING] " + ", ".join(parts) + " — consider reviewing TP/SL"

    return HeartbeatAlert(
        check_name="position_aging",
        channel="trades",
        message=msg,
        severity="warning",
    )


def check_process_memory(
    process_info_list: List[dict],
    threshold_mb: float = PROCESS_MEMORY_WARN_MB,
) -> Optional[HeartbeatAlert]:
    """
    Check process RSS memory usage.

    Args:
        process_info_list: List of dicts with keys: name, pid, rss_mb
        threshold_mb: Alert threshold in MB (default 512)

    Returns:
        HeartbeatAlert if any process exceeds threshold, None otherwise.
    """
    high_mem = []
    for proc in process_info_list:
        rss_mb = proc.get("rss_mb", 0)
        if rss_mb > threshold_mb:
            name = proc.get("name", "unknown")
            pid = proc.get("pid", "?")
            high_mem.append(f"{name} (PID {pid}): {rss_mb:.0f}MB")

    if not high_mem:
        return None

    severity = "critical" if any(
        p.get("rss_mb", 0) > threshold_mb * 2 for p in process_info_list
    ) else "warning"

    return HeartbeatAlert(
        check_name="process_memory",
        channel="focus",
        message=f"[MEMORY] High RSS: {'; '.join(high_mem)}",
        severity=severity,
    )


# =============================================================================
# Session 433: Policy Engine Health Check
# =============================================================================

POLICY_ERROR_RATE_WARN_PCT = 10.0
POLICY_ERROR_RATE_CRITICAL_PCT = 50.0


def check_policy_engine_health(
    kpi_summary: dict,
    shadow_mode: bool = False,
) -> Optional[HeartbeatAlert]:
    """
    Check policy engine KPI health from summarize_policy_kpis() output.

    Args:
        kpi_summary: Dict with total_runs, success_count, error_count,
                     fallback_count, avg_latency_ms.
        shadow_mode: Whether the engine is in shadow mode.

    Returns:
        HeartbeatAlert if error rate > 10% or 100% fallback, None otherwise.
    """
    total = kpi_summary.get("total_runs", 0)
    if total == 0:
        return None

    error_count = kpi_summary.get("error_count", 0)
    fallback_count = kpi_summary.get("fallback_count", 0)
    error_rate = (error_count / total) * 100

    if error_rate <= POLICY_ERROR_RATE_WARN_PCT:
        return None

    mode_label = " (SHADOW)" if shadow_mode else ""
    severity = "critical" if error_rate >= POLICY_ERROR_RATE_CRITICAL_PCT else "warning"

    return HeartbeatAlert(
        check_name="policy_engine_health",
        channel="focus",
        message=(
            f"[POLICY ENGINE{mode_label}] Error rate {error_rate:.0f}% "
            f"({error_count}/{total} runs) — "
            f"fallbacks: {fallback_count}, "
            f"avg latency: {kpi_summary.get('avg_latency_ms', 0):.0f}ms"
        ),
        severity=severity,
        meta={"shadow_mode": shadow_mode, "error_rate_pct": round(error_rate, 1)},
    )


# =============================================================================
# Behavioral Checks (Session 434 — Sprint 4)
# =============================================================================

# Constants for behavioral checks
REVENGE_COOLING_HOURS = 2.0
FEEDBACK_GAP_HOURS = 12.0
ESCALATION_RECENT_TRADES = 5
ESCALATION_MULTIPLIER = 2.0


def _parse_trade_date(trade: dict) -> Optional[datetime]:
    """Parse datetime from a trade dict."""
    for field in ("submitted_at", "close_time", "open_time"):
        val = trade.get(field)
        if not val or not isinstance(val, str):
            continue
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            continue
    return None


def check_revenge_risk(
    trades: list,
    now: Optional[datetime] = None,
) -> Optional[HeartbeatAlert]:
    """Alert if a trade was opened within 2h of a loss (revenge trade).

    Only alerts if the revenge trade is recent (within 4h of now).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if not trades or len(trades) < 2:
        return None

    # Sort by date ascending
    dated = [(t, _parse_trade_date(t)) for t in trades]
    dated = [(t, d) for t, d in dated if d is not None]
    dated.sort(key=lambda x: x[1])

    # Walk through pairs looking for revenge trades
    for i in range(len(dated) - 1, 0, -1):
        curr_trade, curr_date = dated[i]
        prev_trade, prev_date = dated[i - 1]

        # Only care about recent trades
        if (now - curr_date).total_seconds() / 3600 > 4.0:
            break

        prev_result = prev_trade.get("result")
        if not isinstance(prev_result, str) or prev_result.upper() != "LOSS":
            continue

        gap_hours = (curr_date - prev_date).total_seconds() / 3600
        if gap_hours <= REVENGE_COOLING_HOURS:
            token = curr_trade.get("token", "?")
            return HeartbeatAlert(
                check_name="revenge_risk",
                channel="focus",
                message=(
                    f"[REVENGE RISK] {token} trade opened {gap_hours:.1f}h after a loss. "
                    f"Revenge trades have 0% historical win rate. "
                    f"Consider closing if still in drawdown."
                ),
                severity="warning",
                meta={"token": token, "gap_hours": round(gap_hours, 2)},
            )

    return None


def check_position_escalation(
    trades: list,
    now: Optional[datetime] = None,
) -> Optional[HeartbeatAlert]:
    """Alert if recent average position > 2x the overall median.

    Requires at least 10 trades to have enough data for meaningful comparison.
    """
    if not trades or len(trades) < 10:
        return None

    # Get all position sizes
    sizes = []
    for trade in trades:
        pos = trade.get("position", {})
        if isinstance(pos, dict):
            size = pos.get("size_usd")
            if isinstance(size, (int, float)) and size > 0:
                sizes.append(float(size))

    if len(sizes) < 10:
        return None

    # Overall median
    sorted_sizes = sorted(sizes)
    median = sorted_sizes[len(sorted_sizes) // 2]
    if median <= 0:
        return None

    # Recent N trades average
    recent_count = min(ESCALATION_RECENT_TRADES, len(sizes))
    recent_sizes = sizes[-recent_count:]  # Last N entries
    recent_avg = sum(recent_sizes) / len(recent_sizes)

    if recent_avg > median * ESCALATION_MULTIPLIER:
        return HeartbeatAlert(
            check_name="position_escalation",
            channel="focus",
            message=(
                f"[POSITION ESCALATION] Recent avg ${recent_avg:.0f} "
                f"is {recent_avg/median:.1f}x the overall median ${median:.0f}. "
                f"Large positions historically underperform. Consider sizing down."
            ),
            severity="warning",
            meta={"recent_avg": round(recent_avg, 2), "median": round(median, 2)},
        )

    return None


def check_feedback_gap(
    trades: list,
    now: Optional[datetime] = None,
) -> Optional[HeartbeatAlert]:
    """Remind about un-reviewed losses older than 12h.

    Scans all LOSS trades in the last 24h without feedback.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if not trades:
        return None

    from datetime import timedelta
    cutoff = now - timedelta(hours=24)
    gap_cutoff = now - timedelta(hours=FEEDBACK_GAP_HOURS)
    unreviewed = []

    for trade in trades:
        result = trade.get("result")
        if not isinstance(result, str) or result.upper() != "LOSS":
            continue

        dt = _parse_trade_date(trade)
        if dt is None or dt < cutoff:
            continue

        # Check if feedback exists
        fb = trade.get("feedback")
        has_fb = False
        if isinstance(fb, dict) and fb:
            text = fb.get("text", "") or fb.get("notes", "") or fb.get("review", "")
            has_fb = bool(text and str(text).strip())
        elif isinstance(fb, str):
            has_fb = bool(fb.strip())

        if not has_fb and dt < gap_cutoff:
            token = trade.get("token", "?")
            hours_ago = (now - dt).total_seconds() / 3600
            unreviewed.append((token, hours_ago))

    if not unreviewed:
        return None

    tokens_str = ", ".join(f"{t} ({h:.0f}h ago)" for t, h in unreviewed[:3])
    return HeartbeatAlert(
        check_name="feedback_gap",
        channel="focus",
        message=(
            f"[FEEDBACK GAP] {len(unreviewed)} unreviewed loss(es): {tokens_str}. "
            f"2 minutes of reflection prevents repeating mistakes."
        ),
        severity="warning",
        meta={"unreviewed_count": len(unreviewed)},
    )


# =============================================================================
# Calibration Drift + Regime Prediction Checks
# =============================================================================

# Minimum completed trades before calibration checks are meaningful
CALIBRATION_MIN_TRADES = 10

# Score concentration: alert if 9-10 scores exceed this fraction of total
CALIBRATION_CONCENTRATION_THRESHOLD = 0.25

# Top-tier win rate below this is critical miscalibration
CALIBRATION_TOP_TIER_MIN_WIN_RATE = 60.0


def check_calibration_drift(
    calibration_data: dict,
) -> Optional[HeartbeatAlert]:
    """
    Check for scorer calibration issues using pre-computed stats.

    Detects three categories of miscalibration:
    1. Win rate inversion (higher scores have lower win rates than mid scores)
    2. Score concentration (9-10 scores are too common, >25% of total)
    3. High-failure top tier (9-10 bucket has <60% win rate)

    Args:
        calibration_data: Output from get_score_accuracy_stats() with keys:
            total (int), buckets (dict of bucket_name -> {win_rate, total_trades, ...})

    Returns:
        HeartbeatAlert if calibration issues found, None if healthy.
    """
    total = calibration_data.get("total", 0)
    if total < CALIBRATION_MIN_TRADES:
        return None

    buckets = calibration_data.get("buckets")
    if not buckets:
        return None

    issues = []
    severity = "warning"

    # 1. Win Rate Inversion: 8-9 bucket should beat 6-7 bucket
    high_wr = (buckets.get("8-9") or {}).get("win_rate")
    mid_wr = (buckets.get("6-7") or {}).get("win_rate")
    if high_wr is not None and mid_wr is not None and high_wr < mid_wr:
        issues.append(
            f"Win Rate Inversion: 8-9 score WR ({high_wr:.0f}%) < 6-7 WR ({mid_wr:.0f}%)"
        )

    # 2. Score Concentration: 9-10 scores should be rare
    nine_ten = buckets.get("9-10") or {}
    nine_ten_trades = nine_ten.get("total_trades", 0)
    if nine_ten_trades > total * CALIBRATION_CONCENTRATION_THRESHOLD:
        pct = (nine_ten_trades / total) * 100
        issues.append(
            f"Score Concentration: 9-10 scores are {pct:.0f}% of trades (expect <25%)"
        )

    # 3. High-Failure Top Tier: 9-10 win rate below 60% is critical
    nine_ten_wr = nine_ten.get("win_rate")
    if nine_ten_wr is not None and nine_ten_wr < CALIBRATION_TOP_TIER_MIN_WIN_RATE:
        issues.append(
            f"Critical: 9-10 setups have {nine_ten_wr:.0f}% win rate (below {CALIBRATION_TOP_TIER_MIN_WIN_RATE:.0f}%)"
        )
        severity = "critical"

    if not issues:
        return None

    return HeartbeatAlert(
        check_name="calibration_drift",
        channel="focus",
        message="[CALIBRATION] " + "; ".join(issues),
        severity=severity,
        meta={"issues": issues, "total_trades": total},
    )


def check_regime_prediction(
    predictive_alerts: Optional[List[str]],
) -> List[HeartbeatAlert]:
    """
    Convert MarketRegimePredictor alert strings into HeartbeatAlert objects.

    This is the pure-function equivalent of the inline HeartbeatAlert creation
    currently done in heartbeat_monitor.py (check #6). It wraps each alert
    string from generate_preemptive_alerts() into a proper HeartbeatAlert with
    consistent check_name and channel routing.

    Args:
        predictive_alerts: List of alert strings from
            MarketRegimePredictor.generate_preemptive_alerts().
            Can be None or empty.

    Returns:
        List of HeartbeatAlert objects, one per input string.
    """
    if not predictive_alerts:
        return []

    return [
        HeartbeatAlert(
            check_name="predictive_regime_shift",
            channel="macro-updates",
            message=msg,
            severity="warning",
        )
        for msg in predictive_alerts
    ]


# =============================================================================
# Phase 4: TA Freshness Check
# =============================================================================

TA_FRESHNESS_MAX_AGE_HOURS = 6.0


def check_ta_freshness(
    playbook_tokens: list[dict],
    ta_data: dict[str, dict],
    max_age_hours: float = TA_FRESHNESS_MAX_AGE_HOURS,
) -> Optional[HeartbeatAlert]:
    """
    Check if any playbook-ready tokens have stale TA (>6h old).

    Pure function: zero I/O, zero side effects.

    Args:
        playbook_tokens: List of dicts with 'symbol' key -- tokens that have
            active playbooks.
        ta_data: Dict mapping symbol -> ta/latest.json content (with _computed_at).
        max_age_hours: Maximum TA age before alerting (default 6h).

    Returns:
        HeartbeatAlert if any tokens have stale TA, None if all fresh.
    """
    if not playbook_tokens:
        return None

    now = datetime.now(timezone.utc)
    stale_tokens: list[tuple[str, int]] = []

    for token_info in playbook_tokens:
        symbol = token_info.get("symbol")
        if not symbol:
            continue

        ta = ta_data.get(symbol)
        if ta is None:
            # No TA data at all -- treat as stale with unknown age
            stale_tokens.append((symbol, -1))
            continue

        computed_at = ta.get("_computed_at")
        if not computed_at:
            stale_tokens.append((symbol, -1))
            continue

        try:
            ts = datetime.fromisoformat(computed_at)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_hours = (now - ts).total_seconds() / 3600
            if age_hours > max_age_hours:
                stale_tokens.append((symbol, int(age_hours)))
        except (ValueError, TypeError):
            stale_tokens.append((symbol, -1))

    if not stale_tokens:
        return None

    # Format age display
    parts = []
    for sym, age_h in stale_tokens:
        if age_h < 0:
            parts.append(f"{sym} (no TA)")
        else:
            parts.append(f"{sym} ({age_h}h)")

    return HeartbeatAlert(
        check_name="ta_freshness",
        channel="focus",
        message=(
            f"\U0001f504 **TA Freshness Alert**: {len(stale_tokens)} token(s) have "
            f"stale TA (>{max_age_hours:.0f}h): {', '.join(parts)}. "
            f"Consider refreshing via `/ta TOKEN`."
        ),
        severity="warning",
        meta={"stale_count": len(stale_tokens), "symbols": [s for s, _ in stale_tokens]},
    )


def prioritize_stale_ta_tokens(
    stale_symbols: list[str],
    tokens_dir: "Path",
    max_tokens: int = 8,
) -> list[dict]:
    """Prioritize stale TA tokens for auto-refresh. Returns [{symbol, direction, age_hours}].

    Priority: highest conviction score first (from consolidated.json),
    then oldest TA first. Only tokens with a known direction get refreshed.

    Pure function: reads filesystem but has no side effects.
    """
    import json
    from pathlib import Path

    if not stale_symbols:
        return []

    tokens_dir = Path(tokens_dir)
    now = datetime.now(timezone.utc)
    candidates = []

    for symbol in stale_symbols:
        token_dir = tokens_dir / symbol
        consolidated_path = token_dir / "consolidated.json"
        if not consolidated_path.exists():
            continue

        try:
            data = json.loads(consolidated_path.read_text())
        except Exception:
            continue

        # Extract direction and score
        direction_det = data.get("direction_detection", {})
        recommended = direction_det.get("recommended", "")
        if recommended == "SHORT":
            score = direction_det.get("short_score", 0) or 0
            direction = "SHORT"
        elif recommended == "LONG":
            score = direction_det.get("long_score", 0) or 0
            direction = "LONG"
        else:
            # No clear direction — skip
            continue

        # Get TA age
        ta_latest = token_dir / "ta" / "latest.json"
        age_hours = -1  # -1 means no TA
        if ta_latest.exists():
            try:
                ta_data = json.loads(ta_latest.read_text())
                computed_at = ta_data.get("_computed_at", "")
                if computed_at:
                    ts = datetime.fromisoformat(computed_at)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    age_hours = (now - ts).total_seconds() / 3600
            except Exception:
                age_hours = -1

        candidates.append({
            "symbol": symbol,
            "direction": direction,
            "age_hours": round(age_hours, 1) if age_hours >= 0 else -1,
            "score": score,
        })

    # Sort: highest score first, then oldest TA first (age_hours descending)
    candidates.sort(key=lambda c: (-c["score"], -c["age_hours"]))
    return [
        {"symbol": c["symbol"], "direction": c["direction"], "age_hours": c["age_hours"]}
        for c in candidates[:max_tokens]
    ]


# =============================================================================
# D2: Direction Accuracy + KPI Health Checks
# =============================================================================

DIRECTION_ACCURACY_MIN_PERIODS = 10
DIRECTION_ACCURACY_OVERALL_THRESHOLD = 50.0
DIRECTION_ACCURACY_BIAS_THRESHOLD = 40.0

KPI_DRAWDOWN_WARN_PCT = 15.0
KPI_HIGH_CONVICTION_FLOOR = 8.5
KPI_LOW_CONVICTION_CEIL = 7.0


def check_direction_accuracy(
    accuracy_stats: dict,
) -> Optional[HeartbeatAlert]:
    """Check if market direction predictions are worse than a coin flip.

    Args:
        accuracy_stats: Output from DirectionAccuracyTracker.get_accuracy_stats().
            Keys: total_periods, correct_calls, hit_rate, by_bias.

    Returns:
        HeartbeatAlert if accuracy is poor, None if healthy.
    """
    if not accuracy_stats:
        return None

    total = accuracy_stats.get("total_periods", 0)
    if total < DIRECTION_ACCURACY_MIN_PERIODS:
        return None

    issues = []
    hit_rate = accuracy_stats.get("hit_rate", 0)

    if hit_rate < DIRECTION_ACCURACY_OVERALL_THRESHOLD:
        issues.append(f"Overall hit rate {hit_rate}% (below {DIRECTION_ACCURACY_OVERALL_THRESHOLD}%)")

    by_bias = accuracy_stats.get("by_bias", {})
    for bias_name in ("BULLISH", "BEARISH"):
        bias_stats = by_bias.get(bias_name, {})
        if bias_stats.get("excluded"):
            continue
        bias_periods = bias_stats.get("periods", 0)
        bias_hr = bias_stats.get("hit_rate", 0)
        if bias_periods >= 5 and bias_hr < DIRECTION_ACCURACY_BIAS_THRESHOLD:
            issues.append(f"{bias_name} hit rate {bias_hr}% ({bias_stats.get('correct', 0)}/{bias_periods})")

    if not issues:
        return None

    return HeartbeatAlert(
        check_name="direction_accuracy",
        channel="focus",
        message="[DIRECTION ACCURACY] " + "; ".join(issues)
            + f" — direction calls are unreliable over last {total} periods",
        severity="warning",
        meta={"hit_rate": hit_rate, "total_periods": total},
    )


def check_kpi_health(
    kpi_data: dict,
) -> Optional[HeartbeatAlert]:
    """Check KPI health for drawdown and conviction bucket inversion.

    Args:
        kpi_data: Dict with keys:
            max_drawdown: {max_drawdown_pct, status}
            conviction_buckets: [{bucket, win_rate_pct, total_trades}, ...]

    Returns:
        HeartbeatAlert if KPI issues found, None if healthy.
    """
    if not kpi_data:
        return None

    issues = []
    severity = "warning"

    # 1. Max drawdown check
    drawdown = kpi_data.get("max_drawdown", {})
    dd_pct = drawdown.get("max_drawdown_pct", 0)
    if dd_pct > KPI_DRAWDOWN_WARN_PCT:
        issues.append(f"Max drawdown {dd_pct}% (>{KPI_DRAWDOWN_WARN_PCT}%)")
        if dd_pct > 25:
            severity = "critical"

    # 2. Conviction bucket inversion
    buckets = kpi_data.get("conviction_buckets", [])
    if buckets:
        high_conviction = [
            b for b in buckets
            if _bucket_floor(b.get("bucket", "")) >= KPI_HIGH_CONVICTION_FLOOR
            and b.get("total_trades", 0) >= 3
        ]
        low_conviction = [
            b for b in buckets
            if _bucket_floor(b.get("bucket", "")) < KPI_LOW_CONVICTION_CEIL
            and b.get("total_trades", 0) >= 3
        ]

        if high_conviction and low_conviction:
            high_wr = sum(b.get("win_rate_pct", 0) for b in high_conviction) / len(high_conviction)
            low_wr = sum(b.get("win_rate_pct", 0) for b in low_conviction) / len(low_conviction)
            if high_wr < low_wr:
                issues.append(
                    f"Conviction inversion: high-conviction WR {high_wr:.1f}% "
                    f"underperforms low-conviction {low_wr:.1f}%"
                )

    if not issues:
        return None

    return HeartbeatAlert(
        check_name="kpi_health",
        channel="focus",
        message="[KPI] " + "; ".join(issues),
        severity=severity,
        meta={"issues": issues},
    )


def _bucket_floor(bucket_name: str) -> float:
    """Extract the lower bound from a bucket name like '8.5-9.0' or '< 6.0'."""
    if not bucket_name:
        return 0.0
    if bucket_name.startswith("<"):
        return 0.0
    try:
        return float(bucket_name.split("-")[0].strip())
    except (ValueError, IndexError):
        return 0.0
