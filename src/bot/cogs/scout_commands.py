from src.utils.logger import get_logger
import discord
from discord import app_commands
from discord.ext import commands
from src.agent.reasoning.evolver import CapabilityEvolver

logger = get_logger(__name__)

class ScoutCommands(commands.Cog):
    """
    Tier 6: Capability Scout
    Autonomous system auditing to find and propose high-value tools.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.evolver = CapabilityEvolver()
        logger.info("ScoutCommands cog initialized")

    @app_commands.command(name="scout-gaps", description="Audit the system to identify high-value functional gaps")
    async def scout_gaps(self, interaction: discord.Interaction):
        """Run a proactive system audit and report gaps."""
        await interaction.response.defer()
        
        try:
            audit = self.evolver.system_audit()
            
            if audit["status"] != "AUDIT_COMPLETE":
                await interaction.followup.send(f"⚠️ Audit incomplete: {audit.get('recommendation', 'Unknown reason')}")
                return

            embed = discord.Embed(
                title="🔍 SYSTEM CAPABILITY AUDIT",
                description=f"Analyzed **{audit['total_trades_analyzed']}** trades to identify blind spots.",
                color=discord.Color.blue()
            )
            
            for gap in audit["top_gaps"]:
                value_str = f"**Impact:** {gap['impact']} | **Effort:** {gap['difficulty']}\n"
                value_str += f"**Why:** {gap['reasoning']}"
                embed.add_field(name=f"🛠️ {gap['name']}", value=value_str, inline=False)
                
            embed.set_footer(text="Dacle Autonomous Scout (Tier 6.1)")
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Scout command failed: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error running audit: {str(e)}")

    async def cog_app_command_error(self, interaction, error):
        logger.error(f"[ScoutCommands] {error}", exc_info=error)


async def setup(bot: commands.Bot):
    await bot.add_cog(ScoutCommands(bot))
    logger.info("ScoutCommands cog loaded")
