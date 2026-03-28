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
    def _strip_duplicate_header(text: str) -> str:
        lines = text.splitlines()
        if lines and ("APPROVED" in lines[0] or "BLOCKED" in lines[0]):
            return "\n".join(lines[1:]).lstrip("\n")
        return text

    @staticmethod
    def format_candidate_embed(result: Any, macro_data: Optional[Dict] = None) -> discord.Embed:
        """
        Create a rich embed for a trade candidate.
        Session 396: Updated to match user requested "rich" format.
        """
        is_long = getattr(result, 'direction', None) == "LONG"
        is_neutral = getattr(result, 'direction', None) == "NEUTRAL"
        is_skip = getattr(result, 'decision', None) == "SKIP"
        execution_verdict = getattr(result, "execution_verdict", None)
        has_authoritative_verdict = execution_verdict in {"APPROVED", "BLOCKED"}
        authoritative_approved = execution_verdict == "APPROVED"
        
        # Color and Emoji
        if has_authoritative_verdict:
            color = AnalysisFormatter.COLOR_LONG if authoritative_approved else AnalysisFormatter.COLOR_SHORT
            emoji = "✅" if authoritative_approved else "🛑"
        elif is_neutral:
            color = AnalysisFormatter.COLOR_NEUTRAL
            emoji = "⚪"
        elif is_long:
            color = AnalysisFormatter.COLOR_LONG
            emoji = "🚀"
        else:
            color = AnalysisFormatter.COLOR_SHORT
            emoji = "🔴"
        
        # Title/Header: authoritative execution verdict when available, otherwise conviction view.
        if has_authoritative_verdict:
            title = f"{emoji} {result.symbol} {getattr(result, 'direction', 'UNKNOWN')} — {execution_verdict}"
        else:
            title = f"📋 {result.symbol} — {getattr(result, 'direction', 'UNKNOWN')} {result.conviction_score or 0}/10"
        
        long_s = float(getattr(result, 'long_score', 0) or 0.0)
        short_s = float(getattr(result, 'short_score', 0) or 0.0)
        if has_authoritative_verdict:
            execution_formatted = getattr(result, "execution_formatted_response", None)
            if isinstance(execution_formatted, str) and execution_formatted.strip():
                description = AnalysisFormatter._strip_duplicate_header(execution_formatted.strip())
            else:
                description = (
                    f"**David should trade now**: {'YES' if authoritative_approved else 'NO'}\n"
                    f"**Execution Verdict (authoritative)**: {execution_verdict}\n"
                )
        else:
            description = f"**Conviction**: SHORT {short_s:.1f}/10 | LONG {long_s:.1f}/10\n"
            decision_emoji = "❌" if is_skip else "✅"
            description += f"**Decision**: {decision_emoji} {result.decision}\n"
        
        # Reasoning if available
        reasoning = getattr(result, 'reasoning', None)
        if reasoning and not has_authoritative_verdict:
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

        # Explicitly call out missing data (Session 495 clarity fix)
        short_reasons = getattr(result, "short_reasons", None) or []
        long_reasons = getattr(result, "long_reasons", None) or []
        all_reasons = short_reasons + long_reasons
        
        missing_data_warnings = []
        for reason in all_reasons:
            reason_lower = str(reason).lower()
            if "missing" in reason_lower and "data" in reason_lower:
                missing_data_warnings.append(str(reason))
            elif "missing float % data" in reason_lower or float_pct == "Unknown":
                missing_data_warnings.append("Missing float % data (circulating supply unavailable)")
                
        # Deduplicate warnings
        missing_data_warnings = list(dict.fromkeys(missing_data_warnings))
        
        if missing_data_warnings:
            warning_text = "\n".join([f"• {w}" for w in missing_data_warnings])
            warning_text += f"\n\n💡 **Tip:** Primary sources are incomplete. Run `/audit {result.symbol}` to dispatch an AI specialist."
            embed.add_field(
                name="⚠️ Critical Data Gaps",
                value=warning_text,
                inline=False
            )

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

        if has_authoritative_verdict:
            embed.add_field(
                name="🧭 Conviction Context",
                value=f"SHORT `{short_s:.1f}/10` | LONG `{long_s:.1f}/10`",
                inline=False,
            )

        # Macro Alignment (L088)
        if macro_data:
            alignment = macro_data.get("macro_alignment", {})
            status = "✅ ALIGNED" if alignment.get("aligned") else "❌ MISALIGNED"
            recommendation = alignment.get("recommendation") or "CHECK_MANUALLY"
            regime_label = macro_data.get("regime_label") or macro_data.get("regime") or "UNKNOWN"
            volatility = macro_data.get("volatility", {}) or {}
            vol_class = volatility.get("classification") or "UNKNOWN"
            embed.add_field(
                name="🌐 Macro (L088)",
                value=(
                    f"• Regime: `{regime_label}`\n"
                    f"• Volatility: `{vol_class}`\n"
                    f"• L088: `{status}` — `{recommendation}`"
                ),
                inline=False
            )
        
        # Warning if low conviction
        short_reasons = getattr(result, "short_reasons", None) or []
        long_reasons = getattr(result, "long_reasons", None) or []
        if (is_neutral or is_skip) and (short_reasons or long_reasons) and not has_authoritative_verdict:
            def _fmt_reasons(items: list[str]) -> str:
                if not items:
                    return "• (none captured)"
                lines = []
                for reason in items[:3]:
                    txt = str(reason).strip().replace("\n", " ")
                    if len(txt) > 140:
                        txt = txt[:137] + "..."
                    lines.append(f"• {txt}")
                return "\n".join(lines)

            embed.add_field(
                name="🧭 Direction Debug",
                value=(
                    "**SHORT blockers/reasons**\n"
                    f"{_fmt_reasons(short_reasons)}\n\n"
                    "**LONG blockers/reasons**\n"
                    f"{_fmt_reasons(long_reasons)}"
                ),
                inline=False,
            )

        # Warning if low conviction
        if is_skip and (result.conviction_score or 0) < 7.0 and not has_authoritative_verdict:
            embed.set_footer(text=f"⚠️ Conviction {result.conviction_score or 0} < 7.0 threshold.")
        elif has_authoritative_verdict:
            embed.set_footer(text="Canonical execution verdict from full-analysis")
        else:
            embed.set_footer(text="DACLE Autonomous Analysis | Action Required")
        
        return embed
