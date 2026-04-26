"""Character cog — character viewing and leveling.

Character creation happens via the lobby flow in /start_campaign.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.embeds.character_embed import build_character_embed
from db.repositories import PlayerCharacterRepository
from engine.character import check_level_up, level_up

if TYPE_CHECKING:
    from bot.bot import RealmBot

logger = logging.getLogger(__name__)


class CharacterCog(commands.Cog):
    """Character management: view sheet, level up."""

    def __init__(self, bot: RealmBot) -> None:
        self.bot = bot

    @app_commands.command(name="character", description="Affiche ta fiche de personnage")
    @app_commands.describe(public="Afficher publiquement (visible par tous)")
    async def character(
        self, interaction: discord.Interaction, public: bool = False,
    ) -> None:
        """Display the calling user's character sheet."""
        session = self.bot.get_session(interaction.channel_id)
        if session is None:
            await interaction.response.send_message(
                "Aucune session active.", ephemeral=True,
            )
            return

        user_id = interaction.user.id
        char = session.characters.get(user_id)
        if char is None:
            await interaction.response.send_message(
                "Tu n'as pas de personnage. La creation se fait au lancement de la campagne.",
                ephemeral=True,
            )
            return

        embed = build_character_embed(char)
        await interaction.response.send_message(embed=embed, ephemeral=not public)

    @app_commands.command(name="level_up", description="Monte de niveau si tu as assez d'XP")
    @app_commands.describe(public="Afficher publiquement")
    async def level_up_cmd(
        self, interaction: discord.Interaction, public: bool = False,
    ) -> None:
        """Level up the calling user's character if they have enough XP."""
        session = self.bot.get_session(interaction.channel_id)
        if session is None:
            await interaction.response.send_message(
                "Aucune session active.", ephemeral=True,
            )
            return

        user_id = interaction.user.id
        char = session.characters.get(user_id)
        if char is None:
            await interaction.response.send_message(
                "Tu n'as pas de personnage.", ephemeral=True,
            )
            return

        if not check_level_up(char):
            await interaction.response.send_message(
                f"Pas assez d'XP pour monter au niveau {char.level + 1}.",
                ephemeral=True,
            )
            return

        level_up(char)
        logger.info(
            "CHAR levelup name=%s level=%d user=%s",
            char.name, char.level, interaction.user,
        )

        # Save to DB
        db_session = self.bot.db_factory()
        try:
            pc_repo = PlayerCharacterRepository(db_session)
            inv = session.inventories[user_id]
            spell = session.spellcasters.get(user_id)
            pc_repo.update(user_id, session.campaign.id, char, inv, spell)
            db_session.commit()
        finally:
            db_session.close()

        embed = build_character_embed(char)
        await interaction.response.send_message(
            content=f"**{char.name}** passe au niveau {char.level} !",
            embed=embed,
            ephemeral=not public,
        )


async def setup(bot: commands.Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(CharacterCog(bot))  # type: ignore[arg-type]
