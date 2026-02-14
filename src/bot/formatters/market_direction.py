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
        "BULLISH": {"color": 0x34C759, "emoji": "🟢"},
        "NEUTRAL": {"color": 0xFF9500, "emoji": "🟡"},
        "BEARISH": {"color": 0xFF3B30, "emoji": "🔴"},
    }
    bc = bias_cfg.get(bias, {"color": 0x9B9B9B, "emoji": "⚪"})

    shift_line = ""
    if data.get("shift_detected") and data.get("previous_bias"):
        shift_line = f"\n*Shifted from {data['previous_bias']} → {bias}*"

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

    fields = [
        {"name": "━━━━ Signal Breakdown ━━━━", "value": signals_text[:1024], "inline": False},
    ]
    if context_text:
        fields.append({"name": "━━━━ Market Context ━━━━", "value": context_text[:1024], "inline": False})
    staleness_badge = _get_levels_staleness_badge(btc_levels_path)
    key_levels_header = f"━━━━ Key Levels{staleness_badge} ━━━━"
    fields.extend([
        {"name": key_levels_header, "value": levels_text[:1024], "inline": False},
        {"name": "━━━━ Implications ━━━━", "value": implications_text[:1024], "inline": False},
    ])

    return {
        "title": "📊 MARKET DIRECTION UPDATE",
        "description": f"{bc['emoji']} **{bias}** ({confidence}% confidence){shift_line}",
        "color": bc["color"],
        "fields": fields,
        "footer": {"text": " | ".join(footer_parts)},
        "timestamp": timestamp,
    }

