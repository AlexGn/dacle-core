"""
Discord Cipher Commands — /cipher and /rotation

Exposes Market Cipher B indicator state and capital rotation signals
for all tracked macro indices via slash commands.

/cipher          — all Tier 1+2 indices overview with emoji signal state
/cipher <index>  — detailed snapshot for one index (WT, MFI, MACD, Chop)
/rotation        — current sector rotation signal
"""

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.logger import get_logger
from src.bot.utils.interaction_response import safe_defer, safe_send
from src.data.cipher_cache_service import get_cache_freshness

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


def _build_overview_embed(snapshots: dict) -> discord.Embed:
    """Build a compact overview embed for all available cipher snapshots."""
    embed = discord.Embed(
        title="Market Cipher — Index Overview",
        color=discord.Color.blue(),
    )

    tier1 = ["BTC.D", "USDT.D", "TOTAL", "TOTAL2", "TOTAL3", "OTHERS.D", "ETH/BTC"]
    tier2 = ["MEME.C", "AI.C", "LAYER1.C", "DEPIN.C", "RWA.C", "SOLANA.C"]

    def _build_section(keys: list) -> str:
        lines = []
        for key in keys:
            snap = snapshots.get(key)
            if snap is None:
                lines.append(f"`{key:<10}` ⚪ *no data*")
                continue
            emoji = _signal_emoji(snap.signal)
            conf_pct = int(snap.confidence * 100)
            wt_str = ""
            if snap.wavetrend:
                wt_str = f" WT1={snap.wavetrend.wt1:.1f}"
            chop_str = ""
            if snap.choppiness:
                chop_str = f" Chop={snap.choppiness:.0f}"
            lines.append(
                f"{emoji} `{key:<10}` **{snap.signal}** ({conf_pct}%){wt_str}{chop_str}"
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
    embed = discord.Embed(
        title=f"{emoji} Cipher: {index_key}",
        description=f"**Signal**: {snap.signal}  |  Confidence: {int(snap.confidence*100)}%",
        color=color,
    )

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

    if snap.gold_signal:
        embed.add_field(name="🥇 Gold Signal", value="WT1 extreme oversold reversal triggered!", inline=False)

    if snap.wt_divergence:
        wt_div_e = "🟢" if snap.wt_divergence == "bullish" else "🔴"
        embed.add_field(name="WT Fractal Divergence", value=f"{wt_div_e} {snap.wt_divergence.title()} (str={snap.wt_divergence_strength:.2f})", inline=True)

    if snap.rsi_divergence and snap.rsi_divergence != "none":
        rsi_div_e = "🟢" if snap.rsi_divergence == "bullish" else "🔴"
        embed.add_field(name="RSI Divergence", value=f"{rsi_div_e} {snap.rsi_divergence.title()} ({snap.rsi_divergence_strength})", inline=True)

    if snap.ha_bullish_streak >= 2:
        embed.add_field(name="Heikin Ashi", value=f"🟢 {snap.ha_bullish_streak}-bar bullish streak", inline=True)
    elif snap.ha_bearish_streak >= 2:
        embed.add_field(name="Heikin Ashi", value=f"🔴 {snap.ha_bearish_streak}-bar bearish streak", inline=True)
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

            # 2. If cache has too few bars, fetch via TradingView scanner (no ccxt)
            if bars < 50:
                logger.info(f"[cipher-token] {symbol}: only {bars} cached bars, fetching via TV...")
                import requests as _req
                from datetime import datetime as _dt, timezone as _tz
                tv_symbol = f"BINANCE:{symbol}USDT"
                if symbol in ("BTC", "ETH", "SOL"):
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
                    timeout=15,
                )
                resp.raise_for_status()
                tv_data = resp.json()
                if tv_data.get("data"):
                    vals = tv_data["data"][0]["d"]
                    if vals and len(vals) >= 5:
                        # Append to cache for future use
                        from src.data.indices_ohlcv_fetcher import _append_to_cache, _tv_candle_timestamp
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
                        bars = get_series_length(symbol, "4H")
                        logger.info(f"[cipher-token] {symbol}: now {bars} cached bars (after TV fetch)")

            # 3. Compute cipher snapshot from cached data
            from src.ta.cipher_engine import run_cipher_on_series
            from src.data.cipher_cache_service import compute_and_cache_token_snapshot
            series = load_ohlcv_series(symbol, "4H", limit=200)
            if len(series.get("closes", [])) < 35:
                await safe_send(
                    interaction,
                    command_name="cipher-token",
                    logger=logger,
                    content=f"Insufficient data for `{symbol}`: only {len(series.get('closes', []))} bars (need 35). Try again after more data accumulates.",
                )
                return

            snap = run_cipher_on_series(symbol, "4H", series)

            # Cache for future use
            try:
                compute_and_cache_token_snapshot(symbol, "4H")
            except Exception:
                pass

            # 4. Build and send embed
            from src.bot.cogs.cipher_commands import _build_detail_embed
            embed = _build_detail_embed(symbol, snap)

            # Add freshness footer
            from src.data.cipher_cache_service import get_cache_freshness
            fres = get_cache_freshness("4H")
            if fres['age_hours'] >= 0:
                age_str = f"Data {fres['age_hours']}h old | {bars} bars"
                warn = " ⚠️" if fres.get('severely_stale') else ""
                embed.set_footer(text=f"{age_str} | {snap.signal}{warn}")

            await safe_send(interaction, command_name="cipher-token", logger=logger, embed=embed)

        except Exception as e:
            logger.error("[cipher-token] Command error: %s", e, exc_info=True)
            await safe_send(
                interaction,
                command_name="cipher-token",
                logger=logger,
                content=f"Failed to compute cipher for `{symbol}`. The token may not be available on Binance. Error: {e}",
            )

    async def cog_app_command_error(self, interaction, error):
        logger.error("[CipherCommands] %s", error, exc_info=error)


async def setup(bot: commands.Bot):
    """Setup function for Discord extension loading."""
    await bot.add_cog(CipherCommands(bot))
