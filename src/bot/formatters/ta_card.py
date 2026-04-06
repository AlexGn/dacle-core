"""TA Card Discord Embed Builder — Session 440.

Builds a rich Discord embed from the /api/ta/card/{symbol} response.
Follows the section divider pattern from market_direction.py.
"""

from __future__ import annotations

from typing import Any, Optional

from src.bot.formatters.cipher_context import normalize_cipher_context


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert a value to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str = "N/A") -> str:
    """Return str representation or default."""
    if value is None:
        return default
    return str(value)


def _format_price(value: Any) -> str:
    """Format a price value for display."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
        if v == 0.0:
            return "N/A"
        if v >= 1000:
            return f"${v:,.2f}"
        if v >= 1:
            return f"${v:.4f}"
        return f"${v:.6f}"
    except (TypeError, ValueError):
        return "N/A"


def _get_embed_color(direction: str, bqs_score: float) -> int:
    """Determine embed color based on direction and BQS score."""
    if bqs_score >= 70:
        if direction == "LONG":
            return 0x34C759  # Green
        return 0xFF3B30  # Red
    if bqs_score >= 50:
        return 0xFF9500  # Orange
    return 0x9B9B9B  # Grey


def _signal_emoji(signal_name: str, signal_data: Any) -> str:
    """Return a directional emoji for a signal."""
    if signal_data is None:
        return "---"
    if isinstance(signal_data, dict):
        bias = signal_data.get("bias", signal_data.get("signal", ""))
        if isinstance(bias, str):
            bias_lower = bias.lower()
            if "bullish" in bias_lower or "long" in bias_lower or "buy" in bias_lower:
                return "BULLISH"
            if "bearish" in bias_lower or "short" in bias_lower or "sell" in bias_lower:
                return "BEARISH"
            if "squeeze" in bias_lower:
                return "SQUEEZE"
            if "expansion" in bias_lower:
                return "EXPANSION"
        return "NEUTRAL"
    return str(signal_data)


def _format_signal_line(name: str, data: Any) -> str:
    """Format a single indicator signal line."""
    if data is None:
        return f"{name}: N/A"
    if isinstance(data, dict):
        bias = _signal_emoji(name, data)
        # Try to include a key value
        value = data.get("value") or data.get("k") or data.get("histogram")
        if value is not None:
            try:
                return f"{name}: {bias} ({float(value):.1f})"
            except (TypeError, ValueError):
                pass
        return f"{name}: {bias}"
    return f"{name}: {data}"


def build_ta_card_embed(data: dict[str, Any]) -> dict[str, Any]:
    """Build a normalized Discord embed dict from TA card API response.

    Returns a dict with keys: title, description, color, fields, footer
    that can be converted to a discord.Embed.
    """
    symbol = _safe_str(data.get("symbol"), "???")
    direction = _safe_str(data.get("direction"), "SHORT")
    bqs_score = _safe_float(data.get("bqs_score"))
    bqs_grade = _safe_str(data.get("bqs_grade"), "F")
    enhanced_conviction = _safe_float(data.get("enhanced_conviction"))
    ta_score = _safe_float(data.get("ta_score"))

    color = _get_embed_color(direction, bqs_score)

    # Direction emoji
    dir_emoji = {"LONG": "LONG", "SHORT": "SHORT"}.get(direction, direction)

    title = (
        f"{symbol} {dir_emoji} | BQS {bqs_score:.0f}/100 ({bqs_grade}) "
        f"| Conv {enhanced_conviction:.1f}/10"
    )

    # Description: current price + TA score
    current_price = data.get("current_price")
    price_str = _format_price(current_price)
    description = f"Price: {price_str} | TA Score: {ta_score:.1f}/10"

    # --- Field 1: Signals ---
    indicators = data.get("indicators") or {}
    rsi_val = data.get("rsi_value")
    signal_lines = []

    if rsi_val is not None:
        signal_lines.append(f"RSI: {_safe_float(rsi_val):.1f}")
    else:
        signal_lines.append("RSI: N/A")

    signal_lines.append(_format_signal_line("MACD", indicators.get("macd")))
    signal_lines.append(_format_signal_line("Stochastic", indicators.get("stochastic")))
    signal_lines.append(
        _format_signal_line("Market Structure", data.get("market_structure"))
    )
    signal_lines.append(_format_signal_line("EMA Cross", indicators.get("ema_cross")))
    signal_lines.append(_format_signal_line("BB Squeeze", indicators.get("bb_squeeze")))
    signal_lines.append(
        _format_signal_line("CVD", indicators.get("cvd_divergence"))
    )

    # Session 440: Dacle Cipher Indicators
    wt = indicators.get("wavetrend")
    mfi = indicators.get("dacle_mfi")
    ha = indicators.get("heikin_ashi")
    if wt or mfi or ha:
        dacle_parts = []
        if wt:
            zone = wt.get("zone", "neutral")
            dacle_parts.append(f"WT: {zone}")
        if mfi:
            bias = "BULL" if mfi.get("is_bullish") else "BEAR"
            dacle_parts.append(f"MFI: {bias}")
        if ha:
            ha_streak = ha.get("bullish_streak", 0)
            ha_bias = "BULL" if ha.get("latest_is_bullish") else "BEAR"
            dacle_parts.append(f"HA: {ha_bias}({ha_streak})")
        if dacle_parts:
            signal_lines.append(f"Dacle: {' | '.join(dacle_parts)}")

    signals_text = "\n".join(signal_lines)

    # --- Field 2: Confluences ---
    confluence_factors = data.get("confluence_factors") or []
    tier_breakdown = data.get("tier_breakdown") or {}
    confluence_score = _safe_float(data.get("confluence_score"))

    conf_lines = [f"Score: {confluence_score:.1f} ({len(confluence_factors)} factors)"]

    # Group by tier if available
    for tier_name, tier_val in tier_breakdown.items():
        if tier_val:
            conf_lines.append(f"  {tier_name}: {tier_val}")

    # List individual factors (max 8 to stay under Discord limit)
    if confluence_factors:
        for factor in confluence_factors[:8]:
            conf_lines.append(f"  - {factor}")
        if len(confluence_factors) > 8:
            conf_lines.append(f"  ... +{len(confluence_factors) - 8} more")
    else:
        conf_lines.append("  No confluences detected")

    confluence_text = "\n".join(conf_lines)

    # --- Field 3: Levels ---
    entry_levels = data.get("entry_levels") or []
    stop_loss = data.get("stop_loss")
    tp1 = data.get("take_profit_1")
    rr_ratio = data.get("rr_ratio")

    level_lines = []
    if entry_levels:
        entries_str = ", ".join(_format_price(e) for e in entry_levels[:3])
        level_lines.append(f"Entry: {entries_str}")
    else:
        level_lines.append("Entry: N/A")
    level_lines.append(f"Stop Loss: {_format_price(stop_loss)}")
    level_lines.append(f"Target: {_format_price(tp1)}")
    if rr_ratio is not None and rr_ratio > 0:
        level_lines.append(f"R:R: {rr_ratio:.1f}:1")
    else:
        level_lines.append("R:R: N/A")

    levels_text = "\n".join(level_lines)

    # --- Field 4: Macro ---
    macro = data.get("macro") or {}
    macro_bias = _safe_str(macro.get("bias"), "N/A")
    macro_confidence = macro.get("confidence_pct")
    btc_price = macro.get("btc_price")
    fear_greed = macro.get("fear_greed")

    macro_lines = []
    if macro_confidence is not None:
        macro_lines.append(f"Bias: {macro_bias} ({macro_confidence}%)")
    else:
        macro_lines.append(f"Bias: {macro_bias}")
    if btc_price:
        macro_lines.append(f"BTC: ${_safe_float(btc_price):,.0f}")
    else:
        macro_lines.append("BTC: N/A")
    if fear_greed is not None:
        macro_lines.append(f"Fear & Greed: {fear_greed}")
    else:
        macro_lines.append("Fear & Greed: N/A")

    macro_text = "\n".join(macro_lines)

    cipher = normalize_cipher_context(data)
    cipher_lines = []
    if cipher.get("available"):
        cipher_lines.append(f"Signal: {cipher['label']}")
        if cipher.get("confidence_pct") is not None:
            cipher_lines.append(f"Confidence: {cipher['confidence_pct']:.0f}%")
        elif cipher.get("score") is not None:
            cipher_lines.append(f"Score: {cipher['score']:+.2f}")
        else:
            cipher_lines.append("Confidence: N/A")
        cipher_lines.append(f"Timeframe: {cipher.get('timeframe') or 'N/A'}")
        cipher_lines.append(f"Read: {cipher['interpretation']}")
    else:
        cipher_lines.extend(
            [
                "Signal: UNAVAILABLE",
                "Confidence: N/A",
                "Timeframe: N/A",
                "Read: Market Cipher unavailable or stale",
            ]
        )
    cipher_text = "\n".join(cipher_lines)

    # --- Reasoning (compact) ---
    reasoning = data.get("reasoning") or []
    if reasoning:
        # Show first 5 reasoning lines
        reason_lines = reasoning[:5]
        reasoning_text = "\n".join(f"- {r}" for r in reason_lines)
        if len(reasoning) > 5:
            reasoning_text += f"\n... +{len(reasoning) - 5} more"
    else:
        reasoning_text = "No reasoning available"

    # --- Footer ---
    footer_parts = []
    processing_time = data.get("processing_time_ms")
    if processing_time is not None:
        footer_parts.append(f"Computed in {processing_time}ms")
    source = data.get("market_data_source")
    if source:
        footer_parts.append(f"Data: {source}")
    playbook_exists = data.get("playbook_exists")
    if playbook_exists is not None:
        footer_parts.append(f"Playbook: {'Yes' if playbook_exists else 'No'}")

    # --- Build fields ---
    fields = [
        {
            "name": "--- Signals ---",
            "value": signals_text[:1024],
            "inline": False,
        },
        {
            "name": "--- Confluences ---",
            "value": confluence_text[:1024],
            "inline": False,
        },
        {
            "name": "--- Levels ---",
            "value": levels_text[:1024],
            "inline": True,
        },
        {
            "name": "--- Macro ---",
            "value": macro_text[:1024],
            "inline": True,
        },
        {
            "name": "--- Market Cipher ---",
            "value": cipher_text[:1024],
            "inline": True,
        },
    ]

    # Only add reasoning if there is content
    if reasoning_text and reasoning_text != "No reasoning available":
        fields.append(
            {
                "name": "--- Reasoning ---",
                "value": reasoning_text[:1024],
                "inline": False,
            }
        )

    return {
        "title": title[:256],  # Discord title limit
        "description": description,
        "color": color,
        "fields": fields,
        "footer": {"text": " | ".join(footer_parts) if footer_parts else "DACLE TA"},
    }
