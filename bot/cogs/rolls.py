"""Rolls cog — /roll for free dice expressions."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from engine.dice import roll

logger = logging.getLogger(__name__)


class RollsCog(commands.Cog):
    """Dice rolling commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="roll", description="Lance les des (ex: 2d6+3)")
    @app_commands.describe(expression="Expression de des (ex: 1d20, 2d6+3, 4d6)")
    async def roll_dice(self, interaction: discord.Interaction, expression: str) -> None:
        """Roll dice and show the result publicly."""
        try:
            result = roll(expression)
        except (ValueError, AttributeError):
            await interaction.response.send_message(
                f"Expression invalide: `{expression}`. Utilise le format `NdX+M` (ex: 2d6+3).",
                ephemeral=True,
            )
            return

        logger.info("CMD roll user=%s expression=%s result=%d", interaction.user, expression, result.total)

        # Format: 2d6+3 → [4, 5] + 3 = 12
        rolls_str = ", ".join(str(r) for r in result.rolls)
        if result.modifier != 0:
            sign = "+" if result.modifier > 0 else ""
            msg = f"\U0001f3b2 **{result.expression}** \u2192 [{rolls_str}] {sign}{result.modifier} = **{result.total}**"
        else:
            msg = f"\U0001f3b2 **{result.expression}** \u2192 [{rolls_str}] = **{result.total}**"

        await interaction.response.send_message(msg)


async def setup(bot: commands.Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(RollsCog(bot))
