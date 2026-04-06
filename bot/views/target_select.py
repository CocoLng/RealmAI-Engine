"""Target selection view — dropdown menu to pick a combat target."""

from __future__ import annotations

import discord
from discord import ui


class TargetSelectView(ui.View):
    """Select menu to pick a target from living combatants."""

    timeout = 60.0

    def __init__(self, targets: list[tuple[str, str]]) -> None:
        """Initialise with a list of selectable targets.

        Parameters
        ----------
        targets:
            List of ``(name, description)`` tuples used to build
            :class:`discord.SelectOption` entries.
        """
        super().__init__(timeout=self.timeout)
        self.selected_target: str | None = None
        options = [
            discord.SelectOption(label=name, description=desc)
            for name, desc in targets
        ]
        self.select_target.options = options  # type: ignore[assignment]

    @ui.select(placeholder="Choisis ta cible...")
    async def select_target(
        self, interaction: discord.Interaction, select: ui.Select["TargetSelectView"]
    ) -> None:
        """Handle target selection."""
        self.selected_target = select.values[0]
        await interaction.response.defer()
        self.stop()
