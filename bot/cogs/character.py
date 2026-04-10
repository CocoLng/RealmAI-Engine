"""Character cog — character creation, viewing, and leveling."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.embeds.character_embed import build_character_embed
from bot.views.character_create_view import CharacterCreateView
from db.repositories import PlayerCharacterRepository
from engine.character import (
    assign_standard_array,
    check_level_up,
    create_character,
    level_up,
)
from engine.inventory import create_inventory
from engine.spells import create_spellcaster_state

if TYPE_CHECKING:
    from bot.bot import RealmBot

logger = logging.getLogger(__name__)


class CharacterCog(commands.Cog):
    """Character management: create, view, level up."""

    def __init__(self, bot: RealmBot) -> None:
        self.bot = bot

    @app_commands.command(name="create_character", description="Cree un nouveau personnage")
    async def create_character_cmd(self, interaction: discord.Interaction) -> None:
        """Start the character creation flow for the calling user."""
        session = self.bot.get_session(interaction.channel_id)
        if session is None:
            await interaction.response.send_message(
                "Aucune session active. Utilise `/start_campaign` ou `/resume`.",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        if user_id in session.characters:
            await interaction.response.send_message(
                "Tu as deja un personnage dans cette campagne.",
                ephemeral=True,
            )
            return

        # Send character create view
        view = CharacterCreateView()
        await interaction.response.send_message(
            "Creation de personnage -- Choisis ta race :",
            view=view,
            ephemeral=True,
        )

        # Wait for the view to complete (user fills all selects + modal)
        timed_out = await view.wait()
        if timed_out or not view.completed:
            return

        # All selections made — these are guaranteed non-None after completed=True
        assert view.race is not None
        assert view.char_class is not None
        assert view.alignment is not None
        assert view.character_name is not None
        assert view.ability_assignments is not None
        assert view.skill_proficiencies is not None

        scores = assign_standard_array(view.ability_assignments, view.race)
        character = create_character(
            name=view.character_name,
            race=view.race,
            char_class=view.char_class,
            ability_scores=scores,
            alignment=view.alignment,
            skill_proficiencies=view.skill_proficiencies,
        )
        inventory = create_inventory()
        spellcaster = create_spellcaster_state(view.char_class, 1)

        # Save to session
        session.characters[user_id] = character
        session.inventories[user_id] = inventory
        session.spellcasters[user_id] = spellcaster

        # Save to DB
        db_session = self.bot.db_factory()
        try:
            pc_repo = PlayerCharacterRepository(db_session)
            pc_repo.save(user_id, session.campaign.id, character, inventory, spellcaster)
            db_session.commit()
        finally:
            db_session.close()

        logger.info(
            "CHAR created name=%s race=%s class=%s user=%s campaign=%s",
            character.name, view.race.value, view.char_class.value,
            interaction.user, session.campaign.id,
        )

        embed = build_character_embed(character)
        # Use followup since the original interaction was already responded to
        await interaction.followup.send(
            content=f"**{character.name}** a ete cree !",
            embed=embed,
            ephemeral=True,
        )

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
                "Tu n'as pas de personnage. Utilise `/create_character`.",
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
