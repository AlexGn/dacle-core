"""
Analysis Update Formatter
Formats WorkflowResults into rich Discord embeds with TA and Macro confluences.
"""

import discord
from datetime import datetime
from typing import Dict, Any, Optional

class AnalysisFormatter:
    """
    Creates rich embeds for trade candidates in #analysis-updates.
    """
    
    COLOR_LONG = discord.Color.green()
    COLOR_SHORT = discord.Color.red()
    COLOR_NEUTRAL = discord.Color.blue()

    @staticmethod
    def format_candidate_embed(result: Any, macro_data: Optional[Dict] = None) -> discord.Embed:
        """
        Create a rich embed for a trade candidate.
        Session 396: Updated to match user requested "rich" format.
        """
        is_long = getattr(result, 'direction', None) == "LONG"
        is_neutral = getattr(result, 'direction', None) == "NEUTRAL"
        is_skip = getattr(result, 'decision', None) == "SKIP"
        
        # Color and Emoji
        if is_neutral:
            color = AnalysisFormatter.COLOR_NEUTRAL
            emoji = "⚪"
        elif is_long:
            color = AnalysisFormatter.COLOR_LONG
            emoji = "🚀"
        else:
            color = AnalysisFormatter.COLOR_SHORT
            emoji = "🔴"
        
        # Title/Header: 📋 ZRO — NEUTRAL 3.2/10
        title = f"📋 {result.symbol} — {getattr(result, 'direction', 'UNKNOWN')} {result.conviction_score or 0}/10"
        
        # Conviction Breakdown: Conviction: SHORT 0.0/10 | LONG 3.2/10
        long_s = getattr(result, 'long_score', 0) or 0
        short_s = getattr(result, 'short_score', 0) or 0
        description = f"**Conviction**: SHORT {short_s:.1f}/10 | LONG {long_s:.1f}/10\n"
        
        # Decision: Decision: ❌ SKIP (Low conviction)
        decision_emoji = "❌" if is_skip else "✅"
        description += f"**Decision**: {decision_emoji} {result.decision}\n"
        
        # Reasoning if available
        reasoning = getattr(result, 'reasoning', None)
        if reasoning:
            if isinstance(reasoning, list):
                # Scorer returns list of flags — show top 5 as bullet points
                for item in reasoning[:5]:
                    description += f"\n> {item}"
                if len(reasoning) > 5:
                    description += f"\n> _...and {len(reasoning) - 5} more_"
                description += "\n"
            else:
                description += f"\n> {reasoning}\n"
        
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.now()
        )
        
        # 📊 Key Data Section
        def fmt_m(val):
            if not isinstance(val, (int, float)): return "Unknown"
            if val >= 1e9: return f"${val/1e9:.1f}B"
            if val >= 1e6: return f"${val/1e6:.1f}M"
            return f"${val:,.0f}"

        fdv = fmt_m(getattr(result, 'fdv', None))
        mc = fmt_m(getattr(result, 'market_cap', None))
        ratio = f"{result.fdv_mc_ratio:.1f}x" if isinstance(getattr(result, 'fdv_mc_ratio', None), (int, float)) else "Unknown"
        float_pct = f"{result.float_pct:.0f}%" if isinstance(getattr(result, 'float_pct', None), (int, float)) else "Unknown"

        # VC formatting
        investor_tier = getattr(result, 'investor_tier', None) or getattr(result, 'vc_tier_classification', None)
        vc_present = getattr(result, 'vc_present', None)
        tier_1_vc_count = getattr(result, 'tier_1_vc_count', None)
        vc_parts = []
        if isinstance(investor_tier, str) and investor_tier:
            vc_parts.append(investor_tier)
        if isinstance(tier_1_vc_count, int):
            vc_parts.append(f"Tier-1 x{tier_1_vc_count}")
        if vc_present is False:
            vc_fmt = "None"
        elif vc_parts:
            vc_fmt = ", ".join(vc_parts)
        elif vc_present is True:
            vc_fmt = "Present"
        else:
            vc_fmt = "Unknown"
        
        # Price and 24h change
        # Try to get price from entry_price which we now populate with current_price if it's a skip
        price_val = getattr(result, 'entry_price', None)
        price_fmt = f"${price_val:.4f}" if isinstance(price_val, (int, float)) else "Unknown"
        change_val = getattr(result, 'price_24h_change', 0) or 0
        change_fmt = f"{change_val:+.1f}%"
        
        key_data = (
            f"• FDV: `{fdv}` | MC: `{mc}` | FDV/MC: `{ratio}`\n"
            f"• Float: `{float_pct}` | VC: `{vc_fmt}`\n"
            f"• Price: `{price_fmt}` (24h: `{change_fmt}`)"
        )
        
        embed.add_field(
            name="📊 Key Data",
            value=key_data,
            inline=False
        )

        # Macro Alignment (L088)
        if macro_data:
            alignment = macro_data.get("macro_alignment", {})
            status = "✅ ALIGNED" if alignment.get("aligned") else "❌ MISALIGNED"
            embed.add_field(
                name="🌐 Macro (L088)",
                value=f"Status: {status}\n`{alignment.get('recommendation', '')}`",
                inline=False
            )
        
        # Trade Levels (Only for non-skips and if we have any valid values)
        has_sl = isinstance(getattr(result, 'stop_loss', None), (int, float))
        has_tp = isinstance(getattr(result, 'take_profit_1', None), (int, float))
        has_rr = isinstance(getattr(result, 'rr_ratio', None), (int, float))
        if not is_skip and (has_sl or has_tp or has_rr):
            sl = f"${result.stop_loss:.4f}" if isinstance(getattr(result, 'stop_loss', None), (int, float)) else "Unknown"
            tp = f"${result.take_profit_1:.4f}" if isinstance(getattr(result, 'take_profit_1', None), (int, float)) else "Unknown"
            rr = f"{result.rr_ratio:.2f}" if isinstance(getattr(result, 'rr_ratio', None), (int, float)) else "Unknown"
            
            embed.add_field(
                name="📉 Execution Plan",
                value=f"• SL: `{sl}`\n• TP1: `{tp}`\n• R:R: `{rr}`",
                inline=False
            )
        
        # Warning if low conviction
        if is_skip and (result.conviction_score or 0) < 7.0:
            embed.set_footer(text=f"⚠️ Conviction {result.conviction_score or 0} < 7.0 threshold.")
        else:
            embed.set_footer(text="DACLE Autonomous Analysis | Action Required")
        
        return embed
