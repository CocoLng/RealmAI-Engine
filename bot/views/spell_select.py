"""Spell selection view — dropdown menu to pick a castable spell."""

from __future__ import annotations

import discord
from discord import ui


class SpellSelectView(ui.View):
    """Select menu to pick a spell from castable spells."""

    timeout = 60.0

    def __init__(self, spells: list[tuple[str, str]]) -> None:
        """Initialise with a list of castable spells.

        Parameters
        ----------
        spells:
            List of ``(name, description)`` tuples used to build
            :class:`discord.SelectOption` entries.
        """
        super().__init__(timeout=self.timeout)
        self.selected_spell: str | None = None
        options = [
            discord.SelectOption(label=name, description=desc)
            for name, desc in spells
        ]
        self.select_spell.options = options  # type: ignore[assignment]

    @ui.select(placeholder="Choisis ton sort...")
    async def select_spell(
        self, interaction: discord.Interaction, select: ui.Select["SpellSelectView"]
    ) -> None:
        """Handle spell selection."""
        self.selected_spell = select.values[0]
        await interaction.response.defer()
        self.stop()
