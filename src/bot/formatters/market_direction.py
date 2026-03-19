"""Canonical formatter for market-direction Discord payloads."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _fmt_signed(value: Any, decimals: int = 1) -> str:
    num = float(value or 0)
    return f"{num:+.{decimals}f}"


def _to_num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_levels_staleness_badge(btc_levels_path: Path | None = None) -> str:
    """Return staleness badge text for the Key Levels header.

    Returns empty string if fresh, "(STALE - Nd old)" if >7d, "(OUTDATED)" if >30d.
    """
    if btc_levels_path is None:
        btc_levels_path = Path("data/macro/btc_structure_levels.json")

    try:
        if not btc_levels_path.exists():
            return " (OUTDATED)"
        with open(btc_levels_path) as f:
            data = json.load(f)
        updated = data.get("updated") or data.get("updated_at", "")
        if not updated:
            return " (OUTDATED)"
        # Parse date (supports both "2026-02-14" and ISO formats)
        updated_str = str(updated).split("T")[0]
        updated_dt = datetime.strptime(updated_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - updated_dt).days
        if age_days > 30:
            return " (OUTDATED)"
        if age_days > 7:
            return f" (STALE - {age_days}d old)"
    except Exception:
        return " (OUTDATED)"
    return ""


def build_market_direction_embed(
    data: dict[str, Any],
    *,
    next_update_hint: str = "~4h",
    include_next_update: bool = True,
    btc_levels_path: Path | None = None,
) -> dict[str, Any]:
    """Build a normalized Discord embed from /api/macro/market-direction payload."""
    bias = str(data.get("bias", "UNKNOWN"))
    score = float(data.get("score", 0) or 0)
    confidence = int(data.get("confidence_pct", 0) or 0)
    timestamp = data.get("timestamp")

    bias_cfg = {
        "BULLISH": {"color": 0x34C759, "emoji": "🔵"},
        "NEUTRAL": {"color": 0xFF9500, "emoji": "🟡"},
        "BEARISH": {"color": 0xFF3B30, "emoji": "🔴"},
    }
    bc = bias_cfg.get(bias, {"color": 0x9B9B9B, "emoji": "⚪"})

    if data.get("shift_detected") and data.get("previous_bias"):
        shift_line = f"\n\u26a1 *SHIFTED from {data['previous_bias']}*"
    else:
        streak = int(data.get("bias_streak", 0) or 0)
        if streak > 0:
            shift_line = f"\n\U0001f4cc *Holding {bias} \u2014 {streak} consecutive update{'s' if streak != 1 else ''}*"
        else:
            shift_line = ""

    signals = data.get("signals") if isinstance(data.get("signals"), list) else []
    context_signals = data.get("context_signals") if isinstance(data.get("context_signals"), list) else []
    key_levels = data.get("key_levels") if isinstance(data.get("key_levels"), dict) else {}
    impl = data.get("position_implications") if isinstance(data.get("position_implications"), dict) else {}

    signal_lines: list[str] = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        signal_lines.append(
            f"{s.get('emoji', '⚪')} **{s.get('name', 'Signal')}**: "
            f"{s.get('label', 'N/A')} ({int(s.get('weight_pct', 0) or 0)}%)"
        )
    signals_text = "\n".join(signal_lines) if signal_lines else "No signals available"

    context_lines: list[str] = []
    for s in context_signals:
        if not isinstance(s, dict):
            continue
        context_lines.append(f"{s.get('emoji', '⚪')} **{s.get('name', 'Context')}**: {s.get('label', 'N/A')}")
    context_text = "\n".join(context_lines) if context_lines else None

    btc_signal = next((s for s in signals if isinstance(s, dict) and s.get("name") == "BTC Trend"), None)
    btcdom_signal = next((s for s in signals if isinstance(s, dict) and s.get("name") == "BTCDOM"), None)
    total3_signal = next((s for s in signals if isinstance(s, dict) and s.get("name") == "TOTAL3"), None)
    btc_price = _to_num((btc_signal or {}).get("value"))
    btcdom_val = _to_num((btcdom_signal or {}).get("value"))
    total3_val = _to_num((total3_signal or {}).get("value"))

    level_lines: list[str] = []
    btc_levels = key_levels.get("btc") if isinstance(key_levels.get("btc"), list) else []
    if btc_levels:
        if btc_price is not None:
            level_lines.append(f"**BTC** ${btc_price:,.0f}:")
        for lv in btc_levels:
            if not isinstance(lv, dict):
                continue
            icon = "⚠️" if lv.get("alert") else ("↗" if lv.get("status") == "BELOW" else "↘")
            lvl = _to_num(lv.get("level"))
            dist = _to_num(lv.get("distance_pct")) or 0
            level_name = lv.get("name", "Level")
            lvl_text = f"${lvl:,.0f}" if lvl is not None else "N/A"
            level_lines.append(f"  {icon} {level_name} {lvl_text} ({_fmt_signed(dist, 1)}%)")

    btcdom_levels = key_levels.get("btcdom") if isinstance(key_levels.get("btcdom"), list) else []
    if btcdom_levels:
        if btcdom_val is not None:
            level_lines.append(f"\n**BTCDOM** {btcdom_val:.1f}%:")
        for lv in btcdom_levels:
            if not isinstance(lv, dict):
                continue
            icon = "⚠️" if lv.get("alert") else ("✅" if lv.get("status") == "BELOW" else "🔺")
            lvl = _to_num(lv.get("level"))
            dist = _to_num(lv.get("distance_pct")) or 0
            level_name = lv.get("name", "Level")
            lvl_text = f"{lvl:.1f}%" if lvl is not None else "N/A"
            level_lines.append(f"  {icon} {level_name} {lvl_text} ({_fmt_signed(dist, 1)}%)")

    total3_levels = key_levels.get("total3") if isinstance(key_levels.get("total3"), list) else []
    if total3_levels:
        if total3_val is not None:
            level_lines.append(f"\n**TOTAL3** ${total3_val:,.0f}B:")
        for lv in total3_levels:
            if not isinstance(lv, dict):
                continue
            icon = "⚠️" if lv.get("alert") else ("✅" if lv.get("status") == "ABOVE" else "🔻")
            lvl = _to_num(lv.get("level"))
            dist = _to_num(lv.get("distance_pct")) or 0
            level_name = lv.get("name", "Level")
            lvl_text = f"${lvl:,.0f}B" if lvl is not None else "N/A"
            level_lines.append(f"  {icon} {level_name} {lvl_text} ({_fmt_signed(dist, 1)}%)")

    levels_text = "\n".join(level_lines) if level_lines else "No key levels loaded"

    recommendation = impl.get("recommendation", "N/A")
    short_sizing = impl.get("short_sizing", "N/A")
    long_sizing = impl.get("long_sizing", "N/A")
    implications = [
        f"Recommendation: **{recommendation}**",
        f"• SHORTs: {short_sizing}",
        f"• LONGs: {long_sizing}",
    ]
    implications_text = "\n".join(implications)

    footer_parts = []
    if include_next_update:
        footer_parts.append(f"Next update: {next_update_hint}")
    footer_parts.append(f"Score: {score:+.2f}")
    if timestamp:
        footer_parts.append(f"Data: {timestamp}")

    # Narrative summary (rule-based, from generate_narrative_summary)
    narrative = data.get("narrative") or ""

    fields = []

    # --- Economic Calendar Warning (Phase 1.6) ---
    econ_cal = data.get("economic_calendar")
    if econ_cal and isinstance(econ_cal, dict) and econ_cal.get("risk_level") in ("CRITICAL", "WARNING"):
        event_name = econ_cal.get("event_name", "Unknown Event")
        hours_until = econ_cal.get("hours_until", "?")
        risk_lvl = econ_cal.get("risk_level", "WARNING")
        risk_emoji = "\u26a0\ufe0f" if risk_lvl == "CRITICAL" else "\u26a1"
        econ_text = f"{risk_emoji} **{event_name}** in {hours_until}h — {risk_lvl}"
        fields.append({"name": "\u2501\u2501\u2501\u2501 Event Warning \u2501\u2501\u2501\u2501", "value": econ_text[:1024], "inline": False})

    if narrative:
        fields.append({"name": "\u2501\u2501\u2501\u2501 Summary \u2501\u2501\u2501\u2501", "value": narrative[:1024], "inline": False})

    # --- Delta / Changes from previous (Phase 1.4) ---
    score_delta = data.get("score_delta")
    signal_changes = data.get("signal_changes")
    if score_delta is not None or signal_changes:
        delta_lines: list[str] = []
        if score_delta is not None:
            delta_lines.append(f"Score: {score_delta:+.2f}")
        if signal_changes and isinstance(signal_changes, list):
            for ch in signal_changes[:5]:
                if isinstance(ch, dict):
                    delta_lines.append(
                        f"\u2022 **{ch.get('name', '?')}** {ch.get('from_label', '?')} \u2192 {ch.get('to_label', '?')}"
                    )
        if delta_lines:
            fields.append({"name": "\u2501\u2501\u2501\u2501 Changes \u2501\u2501\u2501\u2501", "value": "\n".join(delta_lines)[:1024], "inline": False})

    fields.append({"name": "\u2501\u2501\u2501\u2501 Signal Breakdown \u2501\u2501\u2501\u2501", "value": signals_text[:1024], "inline": False})

    # --- Signal Proximity / Near Flip (Phase 1.3) ---
    signal_proximity = data.get("signal_proximity")
    if signal_proximity and isinstance(signal_proximity, list):
        prox_lines: list[str] = []
        for p in signal_proximity[:3]:
            if isinstance(p, dict) and p.get("description"):
                prox_lines.append(f"\u2022 **{p.get('name', '?')}**: {p['description']}")
        if prox_lines:
            fields.append({"name": "\u2501\u2501\u2501\u2501 Signals Near Flip \u2501\u2501\u2501\u2501", "value": "\n".join(prox_lines)[:1024], "inline": False})

    if context_text:
        fields.append({"name": "\u2501\u2501\u2501\u2501 Market Context \u2501\u2501\u2501\u2501", "value": context_text[:1024], "inline": False})
    staleness_badge = _get_levels_staleness_badge(btc_levels_path)
    key_levels_header = f"\u2501\u2501\u2501\u2501 Key Levels{staleness_badge} \u2501\u2501\u2501\u2501"
    fields.extend([
        {"name": key_levels_header, "value": levels_text[:1024], "inline": False},
        {"name": "\u2501\u2501\u2501\u2501 Implications \u2501\u2501\u2501\u2501", "value": implications_text[:1024], "inline": False},
    ])

    # --- Accuracy stats in footer (Phase 1.5) ---
    accuracy = data.get("accuracy_stats")
    if accuracy and isinstance(accuracy, dict):
        hit_rate = accuracy.get("hit_rate", 0)
        by_bias = accuracy.get("by_bias", {})
        bull_stats = by_bias.get("BULLISH", {})
        bear_stats = by_bias.get("BEARISH", {})
        acc_parts = [f"Accuracy: {hit_rate:.0f}%"]
        if bull_stats.get("periods"):
            acc_parts.append(f"BULLISH {bull_stats['hit_rate']:.0f}% ({bull_stats['correct']}/{bull_stats['periods']})")
        if bear_stats.get("periods"):
            acc_parts.append(f"BEARISH {bear_stats['hit_rate']:.0f}% ({bear_stats['correct']}/{bear_stats['periods']})")
        footer_parts.append(" | ".join(acc_parts))

    # Regime label (Phase 3.4)
    regime = data.get("regime")
    regime_line = f"\nRegime: **{regime}**" if regime else ""

    return {
        "title": "\U0001f4ca MARKET DIRECTION UPDATE",
        "description": (
            f"{bc['emoji']} **{bias}** ({confidence}% confidence, "
            f"{int(data.get('signals_active', 0) or 0)}/{int(data.get('signals_total', 8) or 8)} signals)"
            f"{shift_line}"
            f"{regime_line}"
        ),
        "color": bc["color"],
        "fields": fields,
        "footer": {"text": " | ".join(footer_parts)},
        "timestamp": timestamp,
    }

