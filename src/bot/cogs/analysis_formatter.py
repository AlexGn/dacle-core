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
        """
        is_long = getattr(result, 'direction', None) == "LONG"
        is_neutral = getattr(result, 'direction', None) == "NEUTRAL"
        is_skip = getattr(result, 'decision', None) == "SKIP"
        
        if is_neutral:
            color = AnalysisFormatter.COLOR_NEUTRAL
            emoji = "⚪"
        elif is_long:
            color = AnalysisFormatter.COLOR_LONG
            emoji = "🚀"
        else:
            color = AnalysisFormatter.COLOR_SHORT
            emoji = "🔴"
        
        title = f"{emoji} Candidate Trade: {result.symbol} ({getattr(result, 'direction', 'UNKNOWN')})"
        
        description = f"**Conviction Score**: {result.conviction_score}/10\n"
        description += f"**Decision**: {result.decision}\n"
        
        # Add reasoning if available
        reasoning = getattr(result, 'reasoning', None)
        if reasoning:
            description += f"\n**Reasoning**: {reasoning}\n"
        
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.now()
        )
        
        # Conviction details
        embed.add_field(
            name="📊 Conviction",
            value=f"Score: `{result.conviction_score}`\nBQS Grade: `{getattr(result, 'bqs_grade', 'N/A')}`",
            inline=True
        )
        
        # Playbook status
        playbook_status = "✅ Generated" if getattr(result, 'playbook_generated', False) else "❌ Missing"
        embed.add_field(
            name="📁 Playbook",
            value=playbook_status,
            inline=True
        )
        
        # Levels (Entry/SL/TP) - Only show if NOT a skip
        if not is_skip:
            def fmt(val):
                return f"{val:.4f}" if isinstance(val, (int, float)) else "Unknown"

            entry = fmt(getattr(result, 'entry_price', None))
            sl = fmt(getattr(result, 'stop_loss', None))
            tp = fmt(getattr(result, 'take_profit_1', None))
            rr = f"{result.rr_ratio:.2f}" if isinstance(getattr(result, 'rr_ratio', None), (int, float)) else "Unknown"
            
            embed.add_field(
                name="📉 Trade Levels",
                value=f"• Entry: `{entry}`\n• Stop Loss: `{sl}`\n• Take Profit: `{tp}`\n• R:R Ratio: `{rr}`",
                inline=False
            )
        else:
            embed.add_field(
                name="📉 Trade Levels",
                value="*No levels generated for skipped trade*",
                inline=False
            )

        # Macro Alignment (L088)
        if macro_data:
            alignment = macro_data.get("macro_alignment", {})
            status = "✅ ALIGNED" if alignment.get("aligned") else "❌ MISALIGNED"
            embed.add_field(
                name="🌐 Macro (L088)",
                value=f"Status: {status}\n{alignment.get('recommendation', '')}",
                inline=False
            )

        # Technical confluences (if available in result.confluence_factors)
        factors = getattr(result, 'confluence_factors', [])
        if factors:
            factors_text = "\n".join([f"• {f}" for f in factors[:5]])
            embed.add_field(
                name="📈 TA Confluences",
                value=factors_text,
                inline=False
            )
        
        embed.set_footer(text="DACLE Autonomous Analysis | Action Required")
        
        return embed
