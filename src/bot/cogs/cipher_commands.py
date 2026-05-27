"""
Discord Cipher Commands — /cipher and /rotation

Exposes Market Cipher B indicator state and capital rotation signals
for all tracked macro indices via slash commands.

/cipher          — all Tier 1+2 indices overview with emoji signal state
/cipher <index>  — detailed snapshot for one index (WT, MFI, MACD, Chop)
/rotation        — current sector rotation signal
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.data.cipher_cache_service import get_cache_freshness
from src.utils.logger import get_logger
from src.bot.utils.interaction_response import safe_defer, safe_send

logger = get_logger(__name__)

# Signal → emoji
_SIGNAL_EMOJI = {
    "REVERSAL_UP": "🟢⬆️",
    "BULLISH_MOMENTUM": "🟢",
    "NEUTRAL": "🟡",
    "CHOPPY": "⚪",
    "BEARISH_MOMENTUM": "🔴",
    "REVERSAL_DOWN": "🔴⬇️",
}

_ZONE_EMOJI = {
    "overbought": "🔴",
    "oversold": "🟢",
    "neutral": "🟡",
}


def _signal_emoji(signal: str) -> str:
    return _SIGNAL_EMOJI.get(signal, "⚪")


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# CoinGecko API uses internal IDs that often differ from trading symbols.
_COINGECKO_ID_MAP: Dict[str, str] = {
    "XMR": "monero",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BCH": "bitcoin-cash",
    "XRP": "ripple",
    "DOT": "polkadot",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "MATIC": "matic-network",  # historical, may migrate to POL
    "SHIB": "shiba-inu",
    "LINK": "chainlink",
    "LTC": "litecoin",
    "AVAX": "avalanche-2",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "ETC": "ethereum-classic",
    "XLM": "stellar",
    "TRX": "tron",
    "FIL": "filecoin",
    "ICP": "internet-computer",
    "NEAR": "near",
    "APT": "aptos",
    "ARB": "arbitrum",
    "OP": "optimism",
    "SUI": "sui",
    "SEI": "sei-network",
    "TIA": "celestia",
    "DYM": "dymension",
    "PYTH": "pyth-network",
    "JUP": "jupiter-exchange-solana",
    "WIF": "dogwifcoin",
    "BONK": "bonk",
    "PEPE": "pepe",
    "FLOKI": "floki",
    "WLD": "worldcoin-wld",
}


# Minimum 24h quote volume ($) for a Binance market to be considered active.
# If volume is below this, the pair may be halted/delisted and the price frozen.
_BINANCE_MIN_QUOTE_VOLUME = 1_000_000.0


def _coingecko_id(symbol: str) -> str:
    """Return the CoinGecko API ID for a trading symbol."""
    return _COINGECKO_ID_MAP.get(symbol.upper(), symbol.lower())


def _fetch_realtime_price(symbol: str, cached_close: Optional[float] = None) -> Optional[float]:
    """
    Fetch current spot price from live APIs.

    Pipeline:
      1. Binance — fastest, but only accepted if market status is TRADING
         and 24h volume is above the halt threshold. This prevents frozen
         prices from delisted / halted pairs (e.g. XMRUSDT status=BREAK).
      2. Blofin — perp/futures price, validated via ccxt.
      3. CoinGecko — spot aggregator with symbol→ID mapping for tokens whose
         CoinGecko ID differs from their ticker (e.g. XMR → monero).

    Args:
        symbol: Trading symbol (e.g. "XMR", "BTC").
        cached_close: Optional last 4H close for sanity-check comparison.
                      If the fetched price deviates >50% from cached_close,
                      a warning is logged but the price is still returned
                      (the divergence may be genuine volatility).

    Returns:
        Live price or None if all sources fail.
    """
    import requests as _req

    prices_from: Dict[str, float] = {}

    # ── 1. Binance (with market-status guard) ────────────────────────────
    try:
        # 1a. Verify market is actively trading
        info_resp = _req.get(
            f"https://api.binance.com/api/v3/exchangeInfo?symbol={symbol}USDT",
            timeout=5,
        )
        info_resp.raise_for_status()
        info = info_resp.json()
        symbols = info.get("symbols", [])
        if symbols:
            status = symbols[0].get("status", "")
            if status != "TRADING":
                logger.warning(
                    "[cipher-token] %s: Binance market status=%s (not TRADING), skipping",
                    symbol, status,
                )
            else:
                # 1b. Fetch price AND 24h volume for sanity check
                ticker_resp = _req.get(
                    f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}USDT",
                    timeout=5,
                )
                ticker_resp.raise_for_status()
                ticker = ticker_resp.json()
                quote_vol = _safe_float(ticker.get("quoteVolume"))
                price = _safe_float(ticker.get("lastPrice"))
                if price is not None and price > 0:
                    if quote_vol is not None and quote_vol < _BINANCE_MIN_QUOTE_VOLUME:
                        logger.warning(
                            "[cipher-token] %s: Binance quoteVolume %.0f < %.0f — possible thin/halting market, skipping",
                            symbol, quote_vol, _BINANCE_MIN_QUOTE_VOLUME,
                        )
                    else:
                        prices_from["binance"] = price
                        logger.debug("[cipher-token] %s: real-time price from Binance: %.4f", symbol, price)
    except Exception as e:
        logger.debug("[cipher-token] %s: Binance fetch error: %s", symbol, e)

    # ── 2. Blofin fallback ───────────────────────────────────────────────
    try:
        from src.data.fetchers.blofin_fetcher import BlofinFetcher

        blofin = BlofinFetcher()
        if blofin.validate_token(symbol):
            ohlcv = blofin.fetch_ohlcv(symbol, timeframe="1m", limit=1)
            if ohlcv and len(ohlcv) > 0:
                price = _safe_float(ohlcv[0][4])  # close is index 4
                if price is not None and price > 0:
                    prices_from["blofin"] = price
                    logger.debug("[cipher-token] %s: real-time price from Blofin: %.4f", symbol, price)
    except Exception as e:
        logger.debug("[cipher-token] %s: Blofin fetch error: %s", symbol, e)

    # ── 3. CoinGecko final fallback ──────────────────────────────────────
    try:
        cg_id = _coingecko_id(symbol)
        resp = _req.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd",
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        price = _safe_float(data.get(cg_id, {}).get("usd"))
        if price is not None and price > 0:
            prices_from["coingecko"] = price
            logger.debug("[cipher-token] %s: real-time price from CoinGecko: %.4f", symbol, price)
    except Exception as e:
        logger.debug("[cipher-token] %s: CoinGecko fetch error: %s", symbol, e)

    # ── Select best price ────────────────────────────────────────────────
    # Priority: Binance > Blofin > CoinGecko
    for source in ("binance", "blofin", "coingecko"):
        if source in prices_from:
            price = prices_from[source]

            # Sanity check against cached close
            if cached_close is not None and cached_close > 0:
                deviation = abs(price - cached_close) / cached_close
                if deviation > 0.50:
                    logger.warning(
                        "[cipher-token] %s: %s price %.2f deviates %.0f%% from cached close %.2f — "
                        "verify before trading",
                        symbol, source, price, deviation * 100, cached_close,
                    )
            return price

    logger.warning("[cipher-token] %s: all real-time price sources failed", symbol)
    return None


def _compute_cipher_score(snap) -> dict:
    """
    Compute a 0-100 composite score from cipher snapshot.

    Returns dict with:
      score: 0-100 (50 = neutral)
      max_possible: maximum achievable score for active indicators
      net_votes: raw sum of indicator votes
      active: number of active indicators
      aligned: votes in signal direction
      conflicting: votes against signal direction
      category: "BULLISH" | "BEARISH" | "NEUTRAL" | "CHOPPY" | "REVERSAL"
    """
    wt = getattr(snap, "wavetrend", None)
    mfi = getattr(snap, "mfi", None)
    vwap = getattr(snap, "vwap", None)
    cvd = getattr(snap, "cvd", None)
    macd = getattr(snap, "macd", None)
    stoch = getattr(snap, "stochastic", None)
    rsi_div = getattr(snap, "rsi_divergence", "none")
    wt_div = getattr(snap, "wt_divergence", None)
    ha_bull = getattr(snap, "ha_bullish_streak", 0)
    ha_bear = getattr(snap, "ha_bearish_streak", 0)
    mfi_vw_val = getattr(snap, "mfi_vw", None)
    mfi_vw_bull = getattr(snap, "mfi_vw_is_bullish", False)

    votes = [
        1 if (wt and wt.wt1 is not None and wt.wt2 is not None and wt.wt1 > wt.wt2) else (-1 if (wt and wt.wt1 is not None and wt.wt2 is not None and wt.wt1 < wt.wt2) else 0),
        1 if (macd and macd.direction == "bullish") else (-1 if (macd and macd.direction == "bearish") else 0),
        1 if (mfi and mfi.is_bullish) else (-1 if (mfi and not mfi.is_bullish) else 0),
        1 if (vwap and vwap.above_zero) else (-1 if (vwap and not vwap.above_zero) else 0),
        1 if (rsi_div == "bullish") else (-1 if (rsi_div == "bearish") else 0),
        1 if (cvd and cvd.available and cvd.divergence_type == "positive") else (-1 if (cvd and cvd.available and cvd.divergence_type == "negative") else 0),
        1 if (wt_div == "bullish") else (-1 if (wt_div == "bearish") else 0),
        1 if (ha_bull >= 2) else (-1 if (ha_bear >= 2) else 0),
        1 if (mfi_vw_bull) else (-1 if (mfi_vw_val is not None and not mfi_vw_bull) else 0),
    ]

    net = sum(votes)
    active = sum(1 for v in votes if v != 0)

    if active == 0:
        # Even with no active indicators, respect the signal category
        sig = getattr(snap, "signal", "NEUTRAL")
        cat = "NEUTRAL"
        if sig in ("REVERSAL_UP", "REVERSAL_DOWN"):
            cat = "REVERSAL"
        elif sig == "CHOPPY":
            cat = "CHOPPY"
        elif sig == "BULLISH_MOMENTUM":
            cat = "BULLISH"
        elif sig == "BEARISH_MOMENTUM":
            cat = "BEARISH"
        return {"score": 50, "max_possible": 0, "net_votes": 0, "active": 0, "aligned": 0, "conflicting": 0, "category": cat}

    # Determine category based on signal and net votes
    if snap.signal in ("REVERSAL_UP",):
        category = "REVERSAL"
    elif snap.signal in ("REVERSAL_DOWN",):
        category = "REVERSAL"
    elif snap.signal == "CHOPPY":
        category = "CHOPPY"
    elif snap.signal in ("BULLISH_MOMENTUM",):
        category = "BULLISH"
    elif snap.signal in ("BEARISH_MOMENTUM",):
        category = "BEARISH"
    else:
        category = "NEUTRAL"

    aligned = sum(1 for v in votes if (net > 0 and v > 0) or (net < 0 and v < 0))
    conflicting = sum(1 for v in votes if (net > 0 and v < 0) or (net < 0 and v > 0))

    # 0-100 scale: 50 + (net/active) * 50
    score = 50 + (net / active) * 50
    score = max(0.0, min(100.0, score))

    return {
        "score": round(score, 1),
        "max_possible": active,
        "net_votes": net,
        "active": active,
        "aligned": aligned,
        "conflicting": conflicting,
        "category": category,
    }


def _score_bar(score: float) -> str:
    """Render a simple ASCII bar for the score."""
    filled = int(score / 10)
    empty = 10 - filled
    bar = "█" * filled + "░" * empty
    return f"`{bar}` {score:.0f}/100"


def _build_score_field(snap, label: str = "Composite Score") -> dict:
    """Build a Discord embed field dict for the composite score."""
    sc = _compute_cipher_score(snap)
    score = sc["score"]
    net = sc["net_votes"]
    active = sc["active"]
    aligned = sc["aligned"]
    conflicting = sc["conflicting"]
    category = sc["category"]

    if category == "BULLISH":
        emoji = "🟢"
        color_note = "Bullish"
    elif category == "BEARISH":
        emoji = "🔴"
        color_note = "Bearish"
    elif category == "REVERSAL":
        emoji = "🟢⬆️" if net > 0 else "🔴⬇️"
        color_note = "Reversal"
    elif category == "CHOPPY":
        emoji = "⚪"
        color_note = "Choppy"
    else:
        emoji = "🟡"
        color_note = "Neutral"

    bar = _score_bar(score)
    conf_pct = int(snap.confidence * 100)

    value = (
        f"{emoji} **{color_note}** — {bar}\n"
        f"Confidence: **{conf_pct}%**  |  Active indicators: {active}\n"
        f"Votes: {net:+.0f} (🟢{aligned} aligned, 🔴{conflicting} conflicting)"
    )
    return {"name": f"{label}", "value": value[:1024], "inline": False}


def _check_signal_flip(symbol: str, resolution: str, current_signal: str) -> Optional[str]:
    """Check if signal flipped from previous cached snapshot."""
    try:
        from src.data.cipher_cache_service import get_cipher_snapshot
        prev = get_cipher_snapshot(symbol, resolution, allow_stale=True)
        if prev is not None and prev.signal != current_signal:
            return f"⚡ **Signal Flip**: {prev.signal} → {current_signal}"
    except Exception:
        pass
    return None


def _indicator_lines(snap) -> list[str]:
    """Build detailed indicator text lines from a cipher snapshot."""
    lines: list[str] = []

    def _fmt(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return val

    if snap.wavetrend:
        wt = snap.wavetrend
        zone_e = _ZONE_EMOJI.get(wt.zone, "🟡")
        long_flag = " 🔔 LONG" if wt.long_signal else ""
        short_flag = " 🔔 SHORT" if wt.short_signal else ""
        lines.append(
            f"WaveTrend: WT1={_fmt(wt.wt1):.2f} WT2={_fmt(wt.wt2):.2f} "
            f"Zone={zone_e}{wt.zone}{long_flag}{short_flag}"
        )

    if snap.mfi:
        mfi_e = "🟢" if snap.mfi.is_bullish else "🔴"
        lines.append(f"MFI: {mfi_e} {_fmt(snap.mfi.value):.3f}")

    if snap.macd:
        macd_e = "🟢" if snap.macd.direction == "bullish" else "🔴" if snap.macd.direction == "bearish" else "🟡"
        lines.append(f"MACD: {macd_e} {snap.macd.direction} (hist={_fmt(snap.macd.histogram):.4f})")

    if snap.stochastic:
        st = snap.stochastic
        st_e = "🟢" if st.zone == "oversold" else "🔴" if st.zone == "overbought" else "🟡"
        lines.append(f"Stochastic: {st_e} K={_fmt(st.k):.1f} D={_fmt(st.d):.1f} ({st.zone})")

    if snap.choppiness is not None:
        chop_e = "⚪" if snap.choppiness > 61.8 else "🟢"
        chop_label = "CHOPPY (ranging)" if snap.choppiness > 61.8 else "TRENDING"
        lines.append(f"Choppiness: {chop_e} {_fmt(snap.choppiness):.1f} — {chop_label}")

    gold_signal = getattr(snap, "gold_signal", False)
    if gold_signal:
        lines.append("🥇 Gold Signal: WT1 extreme oversold reversal triggered!")

    wt_div = getattr(snap, "wt_divergence", None)
    if wt_div:
        wt_div_e = "🟢" if wt_div == "bullish" else "🔴"
        wt_str = _fmt(getattr(snap, "wt_divergence_strength", 0.0))
        if isinstance(wt_str, float):
            wt_str = f"{wt_str:.2f}"
        lines.append(
            f"WT Divergence: {wt_div_e} {wt_div.title()} "
            f"(str={wt_str})"
        )

    rsi_div = getattr(snap, "rsi_divergence", "none")
    if rsi_div and rsi_div != "none":
        rsi_div_e = "🟢" if rsi_div == "bullish" else "🔴"
        rsi_str = _fmt(getattr(snap, "rsi_divergence_strength", "none"))
        if isinstance(rsi_str, float):
            rsi_str = f"{rsi_str:.2f}"
        lines.append(f"RSI Divergence: {rsi_div_e} {rsi_div.title()} (str={rsi_str})")

    ha_bull = getattr(snap, "ha_bullish_streak", 0)
    ha_bear = getattr(snap, "ha_bearish_streak", 0)
    if ha_bull >= 2:
        lines.append(f"Heikin Ashi: 🟢 {ha_bull}-bar bullish streak")
    elif ha_bear >= 2:
        lines.append(f"Heikin Ashi: 🔴 {ha_bear}-bar bearish streak")

    if snap.cvd and snap.cvd.available and snap.cvd.divergence_detected:
        div_e = "🟢" if snap.cvd.divergence_type == "positive" else "🔴"
        cvd_str = _fmt(snap.cvd.strength)
        if isinstance(cvd_str, float):
            cvd_str = f"{cvd_str:.2f}"
        lines.append(f"CVD: {div_e} {snap.cvd.divergence_type} (str={cvd_str})")

    if snap.reasons:
        lines.append(f"Reasons: {', '.join(str(r) for r in snap.reasons[:3])}")

    return lines


def _build_checklist(snap) -> list[str]:
    """Compact green/red checklist of key cipher conditions."""
    lines = []
    wt = getattr(snap, "wavetrend", None)
    if wt:
        dot_ok = wt.long_signal or wt.short_signal
        lines.append(f"{'🟢' if dot_ok else '🔴'} Green/Red Dot  (WT1={wt.wt1:.1f} WT2={wt.wt2:.1f})")
    else:
        lines.append("⚪ Green/Red Dot  (no data)")

    rsi = getattr(snap, "rsi", None)
    if rsi:
        lines.append(f"{'🟢' if rsi.above_50 else '🔴'} RSI {'> 50' if rsi.above_50 else '< 50'}  ({rsi.value:.1f})")
    else:
        lines.append("⚪ RSI  (no data)")

    st = getattr(snap, "stochastic", None)
    if st:
        sto_ok = st.k > 50
        lines.append(f"{'🟢' if sto_ok else '🔴'} STO {'> 50' if sto_ok else '< 50'}  (K={st.k:.1f})")
    else:
        lines.append("⚪ STO  (no data)")

    mfi = getattr(snap, "mfi", None)
    if mfi:
        lines.append(f"{'🟢' if mfi.is_bullish else '🔴'} MFI {'bullish' if mfi.is_bullish else 'bearish'}  ({mfi.value:.1f})")
    else:
        lines.append("⚪ MFI  (no data)")

    vwap = getattr(snap, "vwap", None)
    if vwap:
        lines.append(f"{'🟢' if vwap.above_zero else '🔴'} VWAP {'above' if vwap.above_zero else 'below'} zero  ({vwap.value:.1f})")
    else:
        lines.append("⚪ VWAP  (no data)")

    chop = getattr(snap, "choppiness", None)
    if chop is not None:
        trending = chop < 61.8
        lines.append(f"{'🟢' if trending else '🔴'} Choppiness {'< 61.8 (trending)' if trending else '> 61.8 (ranging)'}  ({chop:.1f})")
    else:
        lines.append("⚪ Choppiness  (no data)")

    return lines


def _build_token_embed(
    symbol: str,
    snap_4h,
    snap_1d,
    price: Optional[float],
    flip_4h: Optional[str] = None,
    flip_1d: Optional[str] = None,
    cached_close: Optional[float] = None,
) -> discord.Embed:
    """Build a unified decision-card embed for /cipher-token.

    Layout (top → bottom):
      1. Header: Signal emoji + symbol + live price
      2. Decision Card: composite score, confidence, HTF alignment, verdict
      3. 4H Checklist: compact green/red dots for key conditions
      4. 4H Indicators: full technical detail (folded)
      5. 1D Summary: signal + confidence + score only
      6. Warnings: flip alerts + reversal freshness
      7. Directional Guidance: actionable text
    """
    signals = [snap_4h.signal]
    if snap_1d:
        signals.append(snap_1d.signal)

    bullish = {"REVERSAL_UP", "BULLISH_MOMENTUM"}
    bearish = {"REVERSAL_DOWN", "BEARISH_MOMENTUM"}
    choppy = {"CHOPPY", "NEUTRAL"}

    bull_count = sum(1 for s in signals if s in bullish)
    bear_count = sum(1 for s in signals if s in bearish)
    chop_count = sum(1 for s in signals if s in choppy)

    if bull_count >= 2:
        alignment = "BULLISH ALIGNMENT"
        color = discord.Color.green()
        guidance = "Both timeframes bullish. HTF confirms 4H — LONG setups preferred."
    elif bear_count >= 2:
        alignment = "BEARISH ALIGNMENT"
        color = discord.Color.red()
        guidance = "Both timeframes bearish. HTF confirms 4H — SHORT setups preferred."
    elif bull_count == 1 and bear_count == 0:
        alignment = "4H BULLISH (no 1D confirmation)"
        color = discord.Color.blue()
        guidance = "4H bullish but 1D unconfirmed. Scalp longs only, tight stops."
    elif bear_count == 1 and bull_count == 0:
        alignment = "4H BEARISH (no 1D confirmation)"
        color = discord.Color.orange()
        guidance = "4H bearish but 1D unconfirmed. Scalp shorts only, tight stops."
    elif chop_count >= len(signals):
        alignment = "NEUTRAL / CHOPPY"
        color = discord.Color.light_grey()
        guidance = "No clear direction. Avoid new entries, wait for breakout."
    else:
        alignment = "MIXED SIGNALS"
        color = discord.Color.gold()
        guidance = "Conflicting signals across timeframes. Stand aside or reduce size."

    # ── Header ─────────────────────────────────────────────────────────
    sig_emoji = _signal_emoji(snap_4h.signal)
    title = f"{sig_emoji} Cipher: {symbol}"
    desc_parts = []
    if price:
        desc_parts.append(f"**${price:,.4f}**")
    if cached_close and cached_close != price:
        desc_parts.append(f"*(last 4H close: ${cached_close:,.4f})*")
    embed = discord.Embed(
        title=title,
        description="\n".join(desc_parts) if desc_parts else "",
        color=color,
    )

    # ── Decision Card (top field) ──────────────────────────────────────
    sc_4h = _compute_cipher_score(snap_4h)
    score_bar = _score_bar(sc_4h["score"])
    conf_4h = int(snap_4h.confidence * 100)
    htf_badge = "✅ HTF Confirms" if (bull_count >= 2 or bear_count >= 2) else "⚠️ HTF Unconfirmed"

    decision_value = (
        f"**{snap_4h.signal}** ({conf_4h}%) — {alignment}\n"
        f"{score_bar}\n"
        f"{htf_badge}"
    )
    embed.add_field(name="📊 Decision Card", value=decision_value, inline=False)

    # ── 4H Checklist ───────────────────────────────────────────────────
    checklist = _build_checklist(snap_4h)
    checklist_value = "\n".join(checklist)
    embed.add_field(name="4H Checklist", value=checklist_value, inline=False)

    # ── 4H Indicators (full detail, compact) ────────────────────────────
    ind_4h = _indicator_lines(snap_4h)
    value_4h = "\n".join(f"  {line}" for line in ind_4h) if ind_4h else "  ⚪ No detail available"
    embed.add_field(name="4H Indicators", value=value_4h[:1024], inline=False)

    # ── 1D Summary (lightweight) ──────────────────────────────────────
    if snap_1d:
        sc_1d = _compute_cipher_score(snap_1d)
        score_bar_1d = _score_bar(sc_1d["score"])
        conf_1d = int(snap_1d.confidence * 100)
        value_1d = (
            f"{_signal_emoji(snap_1d.signal)} **{snap_1d.signal}** ({conf_1d}%)\n"
            f"{score_bar_1d}"
        )
        embed.add_field(name="1D Summary", value=value_1d, inline=False)
    else:
        embed.add_field(
            name="1D Timeframe",
            value="⚪ *Insufficient data (need 35+ daily bars)*",
            inline=False,
        )

    # ── Warnings ───────────────────────────────────────────────────────
    if flip_4h:
        embed.add_field(name="⚡ 4H Signal Change", value=flip_4h, inline=False)
    if flip_1d:
        embed.add_field(name="⚡ 1D Signal Change", value=flip_1d, inline=False)

    if snap_4h.signal in ("REVERSAL_UP", "REVERSAL_DOWN"):
        embed.add_field(
            name="⚠️ Fresh Reversal",
            value="Signal just changed on the latest 4H candle. Wait for the next candle to confirm before entering.",
            inline=False,
        )

    # ── Directional Guidance ─────────────────────────────────────────────
    embed.add_field(name="Directional Guidance", value=guidance, inline=False)

    return embed


def _build_setup_embed(
    symbol: str,
    snap_4h,
    snap_1d,
    price: Optional[float],
    macro_snaps: Dict[str, Any],
    cached_close: Optional[float] = None,
) -> discord.Embed:
    """Build a unified GO/NO-GO/WAIT setup card for /setup.

    Combines token cipher snapshot + macro context into a single decision.
    """
    # ── Token score & alignment ────────────────────────────────────────
    sc_4h = _compute_cipher_score(snap_4h)
    token_score = sc_4h["score"]

    signals = [snap_4h.signal]
    if snap_1d:
        signals.append(snap_1d.signal)

    bullish = {"REVERSAL_UP", "BULLISH_MOMENTUM"}
    bearish = {"REVERSAL_DOWN", "BEARISH_MOMENTUM"}
    bull_count = sum(1 for s in signals if s in bullish)
    bear_count = sum(1 for s in signals if s in bearish)
    htf_aligned = bull_count >= 2 or bear_count >= 2

    # ── Macro context ────────────────────────────────────────────────────
    macro_lines: List[str] = []
    macro_score_sum = 0.0
    macro_score_n = 0
    macro_bull = 0
    macro_bear = 0

    for key in ("BTC.D", "USDT.D", "TOTAL"):
        snap = macro_snaps.get(key)
        if snap is None:
            macro_lines.append(f"`{key}` ⚪ *no data*")
            continue
        emoji = _signal_emoji(snap.signal)
        conf = int(snap.confidence * 100)
        sc = _compute_cipher_score(snap)
        if sc and sc.get("active", 0) > 0:
            macro_score_sum += sc["score"]
            macro_score_n += 1
            cat = sc.get("category", "NEUTRAL")
            if cat in ("BULLISH", "REVERSAL"):
                macro_bull += 1
            elif cat in ("BEARISH",):
                macro_bear += 1
        macro_lines.append(f"{emoji} `{key}` **{snap.signal}** ({conf}%)")

    macro_score = round(macro_score_sum / macro_score_n, 1) if macro_score_n > 0 else 50.0

    # ── Verdict logic ────────────────────────────────────────────────────
    # Weekend guard
    now = datetime.now(timezone.utc)
    is_weekend = now.weekday() >= 5  # Saturday=5, Sunday=6

    # Fresh reversal warning
    is_fresh_reversal = snap_4h.signal in ("REVERSAL_UP", "REVERSAL_DOWN")

    # Choppiness
    chop = getattr(snap_4h, "choppiness", None)
    is_choppy = chop is not None and chop > 61.8

    # Direction consensus
    token_bullish = snap_4h.signal in bullish or sc_4h["category"] in ("BULLISH", "REVERSAL")
    token_bearish = snap_4h.signal in bearish or sc_4h["category"] in ("BEARISH",)
    macro_bullish = macro_score >= 55
    macro_bearish = macro_score <= 45

    # Combined score (60% token, 40% macro)
    combined = round(token_score * 0.6 + macro_score * 0.4, 1)

    # Verdict determination
    if is_weekend:
        verdict = "NO-GO"
        verdict_emoji = "🔴"
        color = discord.Color.red()
        verdict_reason = "Weekend guard active — David avoids weekend trades"
    elif is_choppy:
        verdict = "NO-GO"
        verdict_emoji = "🔴"
        color = discord.Color.red()
        verdict_reason = "Choppy market (>61.8) — technical setups unreliable"
    elif is_fresh_reversal:
        verdict = "WAIT"
        verdict_emoji = "🟡"
        color = discord.Color.gold()
        verdict_reason = "Fresh reversal on 4H — wait for next candle confirmation"
    elif token_bullish and macro_bearish:
        verdict = "WAIT"
        verdict_emoji = "🟡"
        color = discord.Color.gold()
        verdict_reason = "Token bullish but macro bearish — conflicting bias, reduce size or wait"
    elif token_bearish and macro_bullish:
        verdict = "WAIT"
        verdict_emoji = "🟡"
        color = discord.Color.gold()
        verdict_reason = "Token bearish but macro bullish — conflicting bias, reduce size or wait"
    elif combined >= 60 and htf_aligned:
        verdict = "GO"
        verdict_emoji = "🟢"
        color = discord.Color.green()
        verdict_reason = "Token + macro aligned, HTF confirms — setup valid"
    elif combined >= 55:
        verdict = "WAIT"
        verdict_emoji = "🟡"
        color = discord.Color.gold()
        verdict_reason = "Directional edge present but not fully confirmed — consider reduced size"
    elif combined <= 40:
        verdict = "NO-GO"
        verdict_emoji = "🔴"
        color = discord.Color.red()
        verdict_reason = "Weak or bearish confluence — avoid entry"
    else:
        verdict = "WAIT"
        verdict_emoji = "🟡"
        color = discord.Color.gold()
        verdict_reason = "No clear edge — stand aside"

    # ── Build embed ──────────────────────────────────────────────────────
    sig_emoji = _signal_emoji(snap_4h.signal)
    title = f"{verdict_emoji} Setup: {symbol}"
    desc_parts = []
    if price:
        desc_parts.append(f"**${price:,.4f}**")
    if cached_close and cached_close != price:
        desc_parts.append(f"*(last 4H close: ${cached_close:,.4f})*")

    embed = discord.Embed(
        title=title,
        description="\n".join(desc_parts) if desc_parts else "",
        color=color,
    )

    # ── Verdict field (top) ──────────────────────────────────────────────
    embed.add_field(
        name=f"Verdict: {verdict}",
        value=f"{verdict_reason}\nCombined score: **{combined:.0f}/100**",
        inline=False,
    )

    # ── Token Decision Card ──────────────────────────────────────────────
    score_bar = _score_bar(token_score)
    conf_4h = int(snap_4h.confidence * 100)
    htf_badge = "✅ HTF Confirms" if htf_aligned else "⚠️ HTF Unconfirmed"
    token_value = (
        f"**{snap_4h.signal}** ({conf_4h}%)\n"
        f"{score_bar}\n"
        f"{htf_badge}"
    )
    embed.add_field(name=f"📊 {symbol} 4H", value=token_value, inline=False)

    # ── 4H Checklist ────────────────────────────────────────────────────
    checklist = _build_checklist(snap_4h)
    embed.add_field(name="4H Checklist", value="\n".join(checklist), inline=False)

    # ── Macro Context ────────────────────────────────────────────────────
    macro_value = "\n".join(macro_lines)
    macro_value += f"\n\nMacro composite: **{macro_score:.0f}/100** (🟢{macro_bull} 🔴{macro_bear})"
    embed.add_field(name="Macro Context", value=macro_value, inline=False)

    # ── 1D Summary ────────────────────────────────────────────────────────
    if snap_1d:
        sc_1d = _compute_cipher_score(snap_1d)
        score_bar_1d = _score_bar(sc_1d["score"])
        conf_1d = int(snap_1d.confidence * 100)
        value_1d = (
            f"{_signal_emoji(snap_1d.signal)} **{snap_1d.signal}** ({conf_1d}%)\n"
            f"{score_bar_1d}"
        )
        embed.add_field(name="1D Summary", value=value_1d, inline=False)
    else:
        embed.add_field(
            name="1D Timeframe",
            value="⚪ *Insufficient data (need 35+ daily bars)*",
            inline=False,
        )

    # ── Invalidation Criteria ────────────────────────────────────────────
    invalidation_lines = []
    if is_fresh_reversal:
        invalidation_lines.append("• Next 4H candle fails to confirm reversal (closes against signal)")
    if htf_aligned:
        invalidation_lines.append("• 1D signal flips against 4H direction")
    else:
        invalidation_lines.append("• 1D does not confirm 4H within 1-2 candles")
    if token_bullish:
        invalidation_lines.append("• RSI drops below 50 or STO drops below 50")
        invalidation_lines.append("• VWAP crosses below zero")
    else:
        invalidation_lines.append("• RSI rises above 50 or STO rises above 50")
        invalidation_lines.append("• VWAP crosses above zero")
    invalidation_lines.append("• Choppiness rises above 61.8 (market goes ranging)")

    embed.add_field(
        name="Invalidation Criteria",
        value="\n".join(invalidation_lines),
        inline=False,
    )

    # ── Risk Notes ───────────────────────────────────────────────────────
    risk_lines = []
    if is_weekend:
        risk_lines.append("🔴 **Weekend guard active** — David avoids Saturday/Sunday trades")
    if is_choppy:
        risk_lines.append("🔴 **Choppy market** — technical edge degraded")
    if is_fresh_reversal:
        risk_lines.append("🟡 **Fresh reversal** — confirmation needed on next candle")
    if not htf_aligned:
        risk_lines.append("🟡 **HTF unconfirmed** — scalp only, tight stops")
    if not risk_lines:
        risk_lines.append("🟢 Risk guards clear")

    embed.add_field(name="Risk Notes", value="\n".join(risk_lines), inline=False)

    # ── Footer ───────────────────────────────────────────────────────────
    try:
        f = get_cache_freshness("4H")
        age_str = f"Data {f['age_hours']}h old" if f['age_hours'] >= 0 else "No cache"
        warn = " ⚠️ STALE" if f.get('severely_stale') else ""
        embed.set_footer(text=f"{age_str}{warn} | Next check: ~1h")
    except Exception:
        embed.set_footer(text="Next check: ~1h")

    return embed


def _build_overview_embed(snapshots: dict) -> discord.Embed:
    """Build a compact overview embed for all available cipher snapshots."""

    tier1 = ["BTC.D", "USDT.D", "TOTAL", "TOTAL2", "TOTAL3", "OTHERS.D", "ETH/BTC"]
    tier2 = ["MEME.C", "AI.C", "LAYER1.C", "DEPIN.C", "RWA.C", "SOLANA.C"]

    # Compute aggregated composite score across all available indices
    scores = []
    bull_count = 0
    bear_count = 0
    neut_count = 0
    for key in list(tier1) + list(tier2):
        snap = snapshots.get(key)
        if snap is None:
            continue
        sc = _compute_cipher_score(snap)
        if sc and sc.get("active", 0) > 0:
            scores.append(sc["score"])
            cat = sc.get("category", "NEUTRAL")
            if cat in ("BULLISH", "REVERSAL"):
                bull_count += 1
            elif cat in ("BEARISH",):
                bear_count += 1
            else:
                neut_count += 1

    agg_score = round(sum(scores) / len(scores), 1) if scores else 50.0
    agg_bar = _score_bar(agg_score)

    if agg_score >= 60:
        agg_color = discord.Color.green()
        agg_emoji = "🟢"
        agg_label = "Bullish Bias"
    elif agg_score <= 40:
        agg_color = discord.Color.red()
        agg_emoji = "🔴"
        agg_label = "Bearish Bias"
    else:
        agg_color = discord.Color.gold()
        agg_emoji = "🟡"
        agg_label = "Neutral / Mixed"

    embed = discord.Embed(
        title=f"{agg_emoji} Market Cipher — {agg_label} ({agg_score:.0f}/100)",
        color=agg_color,
    )

    # Aggregated composite score field at the top
    embed.add_field(
        name="Aggregated Composite Score",
        value=f"{agg_bar}\n🟢 Bullish: {bull_count} | 🔴 Bearish: {bear_count} | 🟡 Neutral: {neut_count}",
        inline=False,
    )

    # Action timing hint
    if agg_score >= 75:
        embed.add_field(
            name="⏰ Action Window",
            value="Strong directional consensus — entries still viable if HTF confirms.",
            inline=False,
        )
    elif agg_score <= 25:
        embed.add_field(
            name="⏰ Action Window",
            value="Strong bearish consensus — short setups still viable if HTF confirms.",
            inline=False,
        )
    elif 45 <= agg_score <= 55:
        embed.add_field(
            name="⏰ Action Window",
            value="No clear consensus — stand aside or wait for breakout.",
            inline=False,
        )

    def _build_section(keys: list) -> str:
        lines = []
        for key in keys:
            snap = snapshots.get(key)
            if snap is None:
                lines.append(f"`{key:<10}` ⚪ *no data*")
                continue
            emoji = _signal_emoji(snap.signal)
            conf_pct = int(snap.confidence * 100)
            sc = _compute_cipher_score(snap)
            score_str = f" [{sc['score']:.0f}]" if sc and sc.get("active", 0) > 0 else ""
            wt_str = ""
            if snap.wavetrend:
                wt_str = f" WT1={snap.wavetrend.wt1:.1f}"
            chop_str = ""
            if snap.choppiness:
                chop_str = f" Chop={snap.choppiness:.0f}"
            lines.append(
                f"{emoji} `{key:<10}` **{snap.signal}** ({conf_pct}%){score_str}{wt_str}{chop_str}"
            )
        return "\n".join(lines) if lines else "*none*"

    embed.add_field(name="Tier 1 — Core Macro", value=_build_section(tier1), inline=False)
    embed.add_field(name="Tier 2 — Sector Rotation", value=_build_section(tier2), inline=False)

    available = sum(1 for k in list(tier1) + list(tier2) if k in snapshots)
    # Add freshness
    try:
        f = get_cache_freshness("4H")
        age_str = f"Data {f['age_hours']}h old" if f['age_hours'] >= 0 else "No cache"
        warn = " ⚠️ STALE" if f.get('severely_stale') else ""
        embed.set_footer(text=f"{age_str} | {available}/{len(tier1)+len(tier2)} indices | 4H{warn}")
    except Exception:
        embed.set_footer(text=f"{available}/{len(tier1)+len(tier2)} indices computed | 4H resolution")
    return embed


def _build_detail_embed(index_key: str, snap) -> discord.Embed:
    """Build a detailed embed for a single index cipher snapshot."""
    emoji = _signal_emoji(snap.signal)
    color = (
        discord.Color.green()
        if snap.signal in ("REVERSAL_UP", "BULLISH_MOMENTUM")
        else discord.Color.red()
        if snap.signal in ("REVERSAL_DOWN", "BEARISH_MOMENTUM")
        else discord.Color.light_grey()
    )
    # Composite score section
    score_field = _build_score_field(snap, label="Composite Score")

    embed = discord.Embed(
        title=f"{emoji} Cipher: {index_key}",
        description=f"**Signal**: {snap.signal}  |  Confidence: {int(snap.confidence*100)}%",
        color=color,
    )

    embed.add_field(name=score_field["name"], value=score_field["value"], inline=False)

    if snap.wavetrend:
        wt = snap.wavetrend
        zone_e = _ZONE_EMOJI.get(wt.zone, "🟡")
        long_flag = " 🔔 LONG SIGNAL" if wt.long_signal else ""
        short_flag = " 🔔 SHORT SIGNAL" if wt.short_signal else ""
        embed.add_field(
            name="WaveTrend",
            value=f"WT1={wt.wt1:.2f}  WT2={wt.wt2:.2f}  Zone={zone_e}{wt.zone}{long_flag}{short_flag}",
            inline=False,
        )

    if snap.mfi:
        mfi_e = "🟢" if snap.mfi.is_bullish else "🔴"
        embed.add_field(name="MFI", value=f"{mfi_e} {snap.mfi.value:.3f}", inline=True)

    if snap.macd:
        macd_e = "🟢" if snap.macd.direction == "bullish" else "🔴" if snap.macd.direction == "bearish" else "🟡"
        embed.add_field(
            name="MACD",
            value=f"{macd_e} {snap.macd.direction} (hist={snap.macd.histogram:.4f})",
            inline=True,
        )

    if snap.stochastic:
        st = snap.stochastic
        st_e = "🟢" if st.zone == "oversold" else "🔴" if st.zone == "overbought" else "🟡"
        embed.add_field(name="Stochastic", value=f"{st_e} K={st.k:.1f} D={st.d:.1f} ({st.zone})", inline=True)

    if snap.choppiness is not None:
        chop_e = "⚪" if snap.choppiness > 61.8 else "🟢"
        chop_label = "CHOPPY (ranging)" if snap.choppiness > 61.8 else "TRENDING"
        embed.add_field(name="Choppiness", value=f"{chop_e} {snap.choppiness:.1f} — {chop_label}", inline=True)

    gold_signal = getattr(snap, "gold_signal", False)
    if gold_signal:
        embed.add_field(name="🥇 Gold Signal", value="WT1 extreme oversold reversal triggered!", inline=False)

    wt_div = getattr(snap, "wt_divergence", None)
    if wt_div:
        wt_div_e = "🟢" if wt_div == "bullish" else "🔴"
        wt_str = getattr(snap, "wt_divergence_strength", 0.0)
        if isinstance(wt_str, (int, float)):
            wt_str = f"{wt_str:.2f}"
        embed.add_field(name="WT Fractal Divergence", value=f"{wt_div_e} {wt_div.title()} (str={wt_str})", inline=True)

    rsi_div = getattr(snap, "rsi_divergence", "none")
    if rsi_div and rsi_div != "none":
        rsi_div_e = "🟢" if rsi_div == "bullish" else "🔴"
        rsi_str = getattr(snap, "rsi_divergence_strength", "none")
        if isinstance(rsi_str, (int, float)):
            rsi_str = f"{rsi_str:.2f}"
        embed.add_field(name="RSI Divergence", value=f"{rsi_div_e} {rsi_div.title()} ({rsi_str})", inline=True)

    ha_bull = getattr(snap, "ha_bullish_streak", 0)
    ha_bear = getattr(snap, "ha_bearish_streak", 0)
    if ha_bull >= 2:
        embed.add_field(name="Heikin Ashi", value=f"🟢 {ha_bull}-bar bullish streak", inline=True)
    elif ha_bear >= 2:
        embed.add_field(name="Heikin Ashi", value=f"🔴 {ha_bear}-bar bearish streak", inline=True)
    else:
        embed.add_field(name="Heikin Ashi", value="🟡 neutral", inline=True)

    if snap.cvd and snap.cvd.available and snap.cvd.divergence_detected:
        div_e = "🟢" if snap.cvd.divergence_type == "positive" else "🔴"
        embed.add_field(
            name="CVD Divergence",
            value=f"{div_e} {snap.cvd.divergence_type} (str={snap.cvd.strength:.2f})",
            inline=True,
        )

    if snap.reasons:
        embed.add_field(name="Reasons", value="\n".join(f"• {r}" for r in snap.reasons[:5]), inline=False)

    # Reversal freshness warning
    if snap.signal in ("REVERSAL_UP", "REVERSAL_DOWN"):
        embed.add_field(
            name="⚠️ Fresh Reversal",
            value="Signal just changed on the latest 4H candle. Wait for the next candle to confirm before entering.",
            inline=False,
        )

    embed.set_footer(text=f"Last bar: {snap.timestamp or 'unknown'}  |  Bars: {snap.bars_used}")
    return embed


def _build_rotation_embed(rotation) -> discord.Embed:
    """Build embed for a rotation signal."""
    if rotation is None:
        embed = discord.Embed(
            title="Sector Rotation",
            description="No rotation detected — sectors not diverging.",
            color=discord.Color.light_grey(),
        )
        return embed

    if rotation.risk_off:
        color = discord.Color.red()
        title = "⚠️ Risk-Off: Capital Rotating to Cash"
    elif rotation.from_sectors and rotation.to_sectors:
        color = discord.Color.orange()
        title = "🔄 Sector Rotation Detected"
    else:
        color = discord.Color.light_grey()
        title = "Sector Rotation"

    embed = discord.Embed(
        title=title,
        description=rotation.context,
        color=color,
    )

    embed.add_field(name="Confidence", value=f"{int(rotation.confidence*100)}%", inline=True)

    if rotation.from_sectors:
        embed.add_field(name="Cooling (exit)", value=", ".join(rotation.from_sectors), inline=True)
    if rotation.to_sectors:
        embed.add_field(name="Heating (entry)", value=", ".join(rotation.to_sectors), inline=True)

    macro_parts = []
    if rotation.usdt_d_falling is True:
        macro_parts.append("🟢 USDT.D falling (capital inflow)")
    elif rotation.usdt_d_falling is False:
        macro_parts.append("🔴 USDT.D rising (defensive)")
    if rotation.btc_d_direction:
        macro_parts.append(f"BTC.D {rotation.btc_d_direction}")
    if macro_parts:
        embed.add_field(name="Macro Context", value="\n".join(macro_parts), inline=False)

    if rotation.sector_scores:
        score_lines = [
            f"`{k:<10}` {'+' if v>=0 else ''}{v:.2f}"
            for k, v in sorted(rotation.sector_scores.items(), key=lambda x: -x[1])
        ]
        embed.add_field(name="Sector Scores", value="\n".join(score_lines), inline=False)

    if rotation.timestamp:
        embed.set_footer(text=f"Last candle: {rotation.timestamp}")
    try:
        f = get_cache_freshness("4H")
        if f['age_hours'] >= 0:
            warn = " ⚠️ DATA STALE" if f.get('severely_stale') else ""
            embed.set_footer(text=f"Data {f['age_hours']}h old{warn} | {embed.footer.text if embed.footer else ''}")
    except Exception:
        pass
    return embed


class CipherCommands(commands.Cog):
    """Cog for cipher and sector rotation slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="cipher",
        description="Market Cipher indicator state for all tracked macro indices",
    )
    @app_commands.describe(index="Optional: specific index key e.g. BTC.D, MEME.C, TOTAL")
    async def cipher(self, interaction: discord.Interaction, index: str = ""):
        """Show cipher state for all indices or a single index."""
        await safe_defer(interaction, ephemeral=False, thinking=True, command_name="cipher", logger=logger)

        try:
            from src.data.cipher_cache_service import get_all_cipher_snapshots, get_cipher_snapshot

            if index:
                snap = get_cipher_snapshot(index.upper(), resolution="4H", allow_stale=True)
                if snap is None:
                    await safe_send(
                        interaction,
                        command_name="cipher",
                        logger=logger,
                        content=f"No cipher data available for `{index.upper()}`. "
                                f"Cache may still be building (needs ~35 bars).",
                    )
                    return
                embed = _build_detail_embed(index.upper(), snap)
            else:
                snapshots = get_all_cipher_snapshots(resolution="4H", allow_stale=True)
                embed = _build_overview_embed(snapshots)

            await safe_send(interaction, command_name="cipher", logger=logger, embed=embed)

        except Exception as e:
            logger.error("[cipher] Command error: %s", e, exc_info=True)
            await safe_send(
                interaction,
                command_name="cipher",
                logger=logger,
                content="Failed to load cipher data. Check logs.",
            )

    @app_commands.command(
        name="rotation",
        description="Current sector rotation signal — where is capital flowing?",
    )
    async def rotation(self, interaction: discord.Interaction):
        """Detect and display active sector rotation."""
        await safe_defer(interaction, ephemeral=False, thinking=True, command_name="rotation", logger=logger)

        try:
            from src.analysis.capital_rotation_detector import detect_rotation
            rotation_signal = detect_rotation(resolution="4H")
            embed = _build_rotation_embed(rotation_signal)
            await safe_send(interaction, command_name="rotation", logger=logger, embed=embed)

        except Exception as e:
            logger.error("[rotation] Command error: %s", e, exc_info=True)
            await safe_send(
                interaction,
                command_name="rotation",
                logger=logger,
                content="Failed to compute rotation signal. Check logs.",
            )

    @app_commands.command(
        name="cipher-token",
        description="Cipher indicator state for a specific token (fetches via TradingView)",
    )
    @app_commands.describe(symbol="Token symbol e.g. BTC, ETH, SOL, ARB")
    async def cipher_token(self, interaction: discord.Interaction, symbol: str):
        """Compute and display cipher state for any token symbol."""
        await safe_defer(interaction, ephemeral=False, command_name="cipher-token", logger=logger)

        try:
            symbol = symbol.upper().strip()

            # 1. Try loading from rolling cache first
            from src.data.indices_ohlcv_fetcher import load_ohlcv_series, get_series_length, _cache_file
            series = load_ohlcv_series(symbol, "4H", limit=200)
            bars = len(series.get("closes", []))

            # 2. If cache has too few bars, fetch from external sources
            if bars < 50:
                logger.info(f"[cipher-token] {symbol}: only {bars} cached bars, fetching externally...")
                import requests as _req
                from src.data.indices_ohlcv_fetcher import _append_to_cache, _tv_candle_timestamp
                fetched = False

                # 2a. Try TradingView scanner first (one candle)
                try:
                    tv_symbol = f"BINANCE:{symbol}USDT"
                    resp = _req.post(
                        "https://scanner.tradingview.com/global/scan",
                        json={
                            "symbols": {"tickers": [tv_symbol]},
                            "columns": [
                                "open|240", "high|240", "low|240", "close|240", "volume|240",
                                "open|D", "high|D", "low|D", "close|D", "volume|D",
                            ],
                        },
                        timeout=8,
                    )
                    resp.raise_for_status()
                    tv_data = resp.json()
                    if tv_data.get("data"):
                        vals = tv_data["data"][0]["d"]
                        if vals and len(vals) >= 5:
                            row_4h = {
                                "ts": _tv_candle_timestamp("4H"),
                                "o": float(vals[0]), "h": float(vals[1]),
                                "l": float(vals[2]), "c": float(vals[3]),
                                "v": float(vals[4]) if vals[4] is not None else 0.0,
                            }
                            _append_to_cache(symbol, "4H", row_4h, 500)
                            if len(vals) >= 10:
                                row_1d = {
                                    "ts": _tv_candle_timestamp("1D"),
                                    "o": float(vals[5]), "h": float(vals[6]),
                                    "l": float(vals[7]), "c": float(vals[8]),
                                    "v": float(vals[9]) if vals[9] is not None else 0.0,
                                }
                                _append_to_cache(symbol, "1D", row_1d, 300)
                            fetched = True
                            logger.info(f"[cipher-token] {symbol}: fetched from TradingView")
                except Exception as e:
                    logger.warning(f"[cipher-token] {symbol}: TV fetch failed: {e}")

                # 2b. Fallback to Binance API (multiple candles) if TV failed
                if not fetched:
                    try:
                        burl = f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval=4h&limit=50"
                        bresp = _req.get(burl, timeout=8)
                        bresp.raise_for_status()
                        klines = bresp.json()
                        if isinstance(klines, list) and len(klines) >= 10:
                            for k in klines:
                                row_4h = {
                                    "ts": k[0] / 1000,
                                    "o": float(k[1]), "h": float(k[2]),
                                    "l": float(k[3]), "c": float(k[4]),
                                    "v": float(k[5]),
                                }
                                _append_to_cache(symbol, "4H", row_4h, 500)
                            # Also fetch daily
                            dresp = _req.get(
                                f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval=1d&limit=50",
                                timeout=8,
                            )
                            dresp.raise_for_status()
                            dlines = dresp.json()
                            if isinstance(dlines, list):
                                for k in dlines:
                                    row_1d = {
                                        "ts": k[0] / 1000,
                                        "o": float(k[1]), "h": float(k[2]),
                                        "l": float(k[3]), "c": float(k[4]),
                                        "v": float(k[5]),
                                    }
                                    _append_to_cache(symbol, "1D", row_1d, 300)
                            fetched = True
                            logger.info(f"[cipher-token] {symbol}: fetched {len(klines)} bars from Binance")
                    except Exception as e:
                        logger.warning(f"[cipher-token] {symbol}: Binance fetch failed: {e}")

                # 2c. Fallback to Blofin API if Binance also failed
                if not fetched:
                    try:
                        from src.data.fetchers.blofin_fetcher import BlofinFetcher
                        blofin = BlofinFetcher()
                        if blofin.validate_token(symbol):
                            ohlcv_4h = blofin.fetch_ohlcv(symbol, timeframe="4h", limit=50)
                            if ohlcv_4h and len(ohlcv_4h) >= 10:
                                for candle in ohlcv_4h:
                                    row_4h = {
                                        "ts": candle[0] / 1000,
                                        "o": float(candle[1]), "h": float(candle[2]),
                                        "l": float(candle[3]), "c": float(candle[4]),
                                        "v": float(candle[5]),
                                    }
                                    _append_to_cache(symbol, "4H", row_4h, 500)
                                # Also fetch daily
                                ohlcv_1d = blofin.fetch_ohlcv(symbol, timeframe="1d", limit=50)
                                if ohlcv_1d:
                                    for candle in ohlcv_1d:
                                        row_1d = {
                                            "ts": candle[0] / 1000,
                                            "o": float(candle[1]), "h": float(candle[2]),
                                            "l": float(candle[3]), "c": float(candle[4]),
                                            "v": float(candle[5]),
                                        }
                                        _append_to_cache(symbol, "1D", row_1d, 300)
                                fetched = True
                                logger.info(f"[cipher-token] {symbol}: fetched {len(ohlcv_4h)} bars from Blofin")
                    except Exception as e:
                        logger.warning(f"[cipher-token] {symbol}: Blofin fetch failed: {e}")

                bars = get_series_length(symbol, "4H")
                logger.info(f"[cipher-token] {symbol}: now {bars} cached bars (after external fetch)")

            # 3. Compute cipher snapshot from cached data
            from src.ta.cipher_engine import run_cipher_on_series
            from src.data.cipher_cache_service import compute_and_cache_token_snapshot
            series = load_ohlcv_series(symbol, "4H", limit=200)
            if len(series.get("closes", [])) < 35:
                await safe_send(
                    interaction,
                    command_name="cipher-token",
                    logger=logger,
                    content=(
                        f"Could not load enough data for `{symbol}`: only {len(series.get('closes', []))} bars "
                        f"(need 35). This usually means the token is not available on Binance or Blofin "
                        f"or the external APIs are temporarily unreachable. Try a major symbol like BTC, ETH, or SOL."
                    ),
                )
                return

            snap_4h = run_cipher_on_series(symbol, "4H", series)

            # 3b. Compute 1D cipher for HTF confirmation
            snap_1d = None
            try:
                series_1d = load_ohlcv_series(symbol, "1D", limit=200)
                if len(series_1d.get("closes", [])) >= 35:
                    snap_1d = run_cipher_on_series(symbol, "1D", series_1d)
            except Exception as e:
                logger.debug(f"[cipher-token] {symbol}: 1D cipher unavailable: {e}")

            # Check signal flips BEFORE caching so we compare against old cache
            flip_4h = _check_signal_flip(symbol, "4H", snap_4h.signal) if snap_4h else None
            flip_1d = _check_signal_flip(symbol, "1D", snap_1d.signal) if snap_1d else None

            # Cache AFTER flip detection so next invocation has the new snapshot as baseline
            try:
                if snap_1d:
                    compute_and_cache_token_snapshot(symbol, "1D")
            except Exception:
                pass
            try:
                compute_and_cache_token_snapshot(symbol, "4H")
            except Exception:
                pass

            # Current price: prefer real-time spot, fallback to last cached 4H close
            cached_close = _safe_float(series.get("closes", [None])[-1])
            price = _fetch_realtime_price(symbol, cached_close=cached_close) or cached_close

            # 4. Build and send clean multi-timeframe embed
            embed = _build_token_embed(
                symbol, snap_4h, snap_1d, price,
                flip_4h=flip_4h, flip_1d=flip_1d,
                cached_close=cached_close,
            )

            # Add freshness footer
            fres = get_cache_freshness("4H")
            if fres['age_hours'] >= 0:
                age_str = f"Data {fres['age_hours']}h old | {bars} bars"
                warn = " [STALE]" if fres.get('severely_stale') else ""
                embed.set_footer(text=f"{age_str} | {snap_4h.signal}{warn}")

            await safe_send(interaction, command_name="cipher-token", logger=logger, embed=embed)

        except Exception as e:
            logger.error("[cipher-token] Command error: %s", e, exc_info=True)
            await safe_send(
                interaction,
                command_name="cipher-token",
                logger=logger,
                content=f"Failed to compute cipher for `{symbol}`. The token may not be available on Binance. Error: {e}",
            )

    @app_commands.command(
        name="setup",
        description="Full GO/NO-GO setup card for a token: cipher + macro context + verdict",
    )
    @app_commands.describe(symbol="Token symbol e.g. BTC, ETH, SOL, ARB")
    async def setup(self, interaction: discord.Interaction, symbol: str):
        """Unified setup decision card combining token + macro + risk guards."""
        await safe_defer(interaction, ephemeral=False, command_name="setup", logger=logger)

        try:
            symbol = symbol.upper().strip()

            # ── 1. Load / fetch token OHLCV ──────────────────────────────────────
            from src.data.indices_ohlcv_fetcher import load_ohlcv_series, get_series_length
            from src.ta.cipher_engine import run_cipher_on_series
            from src.data.cipher_cache_service import compute_and_cache_token_snapshot

            series = load_ohlcv_series(symbol, "4H", limit=200)
            bars = len(series.get("closes", []))

            if bars < 50:
                logger.info(f"[setup] {symbol}: only {bars} cached bars, fetching externally...")
                import requests as _req
                from src.data.indices_ohlcv_fetcher import _append_to_cache, _tv_candle_timestamp
                fetched = False

                # 1a. TradingView
                try:
                    tv_symbol = f"BINANCE:{symbol}USDT"
                    resp = _req.post(
                        "https://scanner.tradingview.com/global/scan",
                        json={
                            "symbols": {"tickers": [tv_symbol]},
                            "columns": [
                                "open|240", "high|240", "low|240", "close|240", "volume|240",
                                "open|D", "high|D", "low|D", "close|D", "volume|D",
                            ],
                        },
                        timeout=8,
                    )
                    resp.raise_for_status()
                    tv_data = resp.json()
                    if tv_data.get("data"):
                        vals = tv_data["data"][0]["d"]
                        if vals and len(vals) >= 5:
                            row_4h = {
                                "ts": _tv_candle_timestamp("4H"),
                                "o": float(vals[0]), "h": float(vals[1]),
                                "l": float(vals[2]), "c": float(vals[3]),
                                "v": float(vals[4]) if vals[4] is not None else 0.0,
                            }
                            _append_to_cache(symbol, "4H", row_4h, 500)
                            if len(vals) >= 10:
                                row_1d = {
                                    "ts": _tv_candle_timestamp("1D"),
                                    "o": float(vals[5]), "h": float(vals[6]),
                                    "l": float(vals[7]), "c": float(vals[8]),
                                    "v": float(vals[9]) if vals[9] is not None else 0.0,
                                }
                                _append_to_cache(symbol, "1D", row_1d, 300)
                            fetched = True
                except Exception as e:
                    logger.warning(f"[setup] {symbol}: TV fetch failed: {e}")

                # 1b. Binance fallback
                if not fetched:
                    try:
                        burl = f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval=4h&limit=50"
                        bresp = _req.get(burl, timeout=8)
                        bresp.raise_for_status()
                        klines = bresp.json()
                        if isinstance(klines, list) and len(klines) >= 10:
                            for k in klines:
                                row_4h = {"ts": k[0] / 1000, "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
                                _append_to_cache(symbol, "4H", row_4h, 500)
                            dresp = _req.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval=1d&limit=50", timeout=8)
                            dresp.raise_for_status()
                            dlines = dresp.json()
                            if isinstance(dlines, list):
                                for k in dlines:
                                    row_1d = {"ts": k[0] / 1000, "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
                                    _append_to_cache(symbol, "1D", row_1d, 300)
                            fetched = True
                    except Exception as e:
                        logger.warning(f"[setup] {symbol}: Binance fetch failed: {e}")

                # 1c. Blofin fallback
                if not fetched:
                    try:
                        from src.data.fetchers.blofin_fetcher import BlofinFetcher
                        blofin = BlofinFetcher()
                        if blofin.validate_token(symbol):
                            ohlcv_4h = blofin.fetch_ohlcv(symbol, timeframe="4h", limit=50)
                            if ohlcv_4h and len(ohlcv_4h) >= 10:
                                for candle in ohlcv_4h:
                                    row_4h = {"ts": candle[0] / 1000, "o": float(candle[1]), "h": float(candle[2]), "l": float(candle[3]), "c": float(candle[4]), "v": float(candle[5])}
                                    _append_to_cache(symbol, "4H", row_4h, 500)
                                ohlcv_1d = blofin.fetch_ohlcv(symbol, timeframe="1d", limit=50)
                                if ohlcv_1d:
                                    for candle in ohlcv_1d:
                                        row_1d = {"ts": candle[0] / 1000, "o": float(candle[1]), "h": float(candle[2]), "l": float(candle[3]), "c": float(candle[4]), "v": float(candle[5])}
                                        _append_to_cache(symbol, "1D", row_1d, 300)
                                fetched = True
                    except Exception as e:
                        logger.warning(f"[setup] {symbol}: Blofin fetch failed: {e}")

                bars = get_series_length(symbol, "4H")

            series = load_ohlcv_series(symbol, "4H", limit=200)
            if len(series.get("closes", [])) < 35:
                await safe_send(
                    interaction,
                    command_name="setup",
                    logger=logger,
                    content=(
                        f"Could not load enough data for `{symbol}`: only {len(series.get('closes', []))} bars "
                        f"(need 35). Try a major symbol like BTC, ETH, or SOL."
                    ),
                )
                return

            snap_4h = run_cipher_on_series(symbol, "4H", series)

            # 1D snapshot
            snap_1d = None
            try:
                series_1d = load_ohlcv_series(symbol, "1D", limit=200)
                if len(series_1d.get("closes", [])) >= 35:
                    snap_1d = run_cipher_on_series(symbol, "1D", series_1d)
            except Exception as e:
                logger.debug(f"[setup] {symbol}: 1D cipher unavailable: {e}")

            # Cache after computation
            try:
                if snap_1d:
                    compute_and_cache_token_snapshot(symbol, "1D")
            except Exception:
                pass
            try:
                compute_and_cache_token_snapshot(symbol, "4H")
            except Exception:
                pass

            # Price
            cached_close = _safe_float(series.get("closes", [None])[-1])
            price = _fetch_realtime_price(symbol, cached_close=cached_close) or cached_close

            # ── 2. Load macro snapshots ──────────────────────────────────────────
            from src.data.cipher_cache_service import get_cipher_snapshot
            macro_keys = ("BTC.D", "USDT.D", "TOTAL")
            macro_snaps = {}
            for k in macro_keys:
                try:
                    s = get_cipher_snapshot(k, resolution="4H", allow_stale=True)
                    if s:
                        macro_snaps[k] = s
                except Exception as e:
                    logger.debug(f"[setup] Macro {k} unavailable: {e}")

            # ── 3. Build setup embed ─────────────────────────────────────────────
            embed = _build_setup_embed(symbol, snap_4h, snap_1d, price, macro_snaps, cached_close=cached_close)
            await safe_send(interaction, command_name="setup", logger=logger, embed=embed)

        except Exception as e:
            logger.error("[setup] Command error: %s", e, exc_info=True)
            await safe_send(
                interaction,
                command_name="setup",
                logger=logger,
                content=f"Failed to build setup card for `{symbol}`. Error: {e}",
            )

    async def cog_app_command_error(self, interaction, error):
        logger.error("[CipherCommands] %s", error, exc_info=error)


async def setup(bot: commands.Bot):
    """Setup function for Discord extension loading."""
    await bot.add_cog(CipherCommands(bot))
