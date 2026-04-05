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

    async def cog_app_command_error(self, interaction, error):
        logger.error("[CipherCommands] %s", error, exc_info=error)


async def setup(bot: commands.Bot):
    """Setup function for Discord extension loading."""
    await bot.add_cog(CipherCommands(bot))
