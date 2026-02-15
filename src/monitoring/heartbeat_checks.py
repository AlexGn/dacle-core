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
    - Disk space        → #focus         (Session 427)
    - Redis health      → #focus         (Session 427)
    - Process memory    → #focus         (Session 427)
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
