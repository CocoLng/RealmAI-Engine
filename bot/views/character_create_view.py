"""Character creation view — progressive select menus + name modal."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import discord
from discord import ui

from engine.character import Alignment, CharacterClass, Race

# Callback type: async fn(interaction, view) -> None
OnCompleteCallback = Callable[
    [discord.Interaction, "CharacterCreateView"],
    Coroutine[Any, Any, None],
]


class CharacterCreateView(ui.View):
    """Three select menus (race, class, alignment) that unlock progressively.

    Once all three are chosen the :class:`CharacterNameModal` is presented
    to collect the character name. ``self.completed`` is ``True`` when the
    entire flow finishes.

    Parameters
    ----------
    on_complete:
        Optional async callback invoked after the name modal is submitted.
        Receives ``(interaction, view)`` so the caller can handle character
        creation without subclassing. Used by the onboarding launcher.
    """

    timeout = 120.0

    def __init__(
        self,
        on_complete: OnCompleteCallback | None = None,
    ) -> None:
        super().__init__(timeout=self.timeout)
        self.race: Race | None = None
        self.char_class: CharacterClass | None = None
        self.alignment: Alignment | None = None
        self.character_name: str | None = None
        self.completed: bool = False
        self._on_complete = on_complete

        # Class and alignment are locked until the previous choice is made
        self.select_class.disabled = True  # type: ignore[assignment]
        self.select_alignment.disabled = True  # type: ignore[assignment]

    # ── Race ──────────────────────────────────────────────────────────────

    @ui.select(
        placeholder="Choisis ta race...",
        options=[discord.SelectOption(label=r.value, value=r.value) for r in Race],
    )
    async def select_race(
        self,
        interaction: discord.Interaction,
        select: ui.Select["CharacterCreateView"],
    ) -> None:
        """Handle race selection and unlock the class menu."""
        self.race = Race(select.values[0])
        self.select_class.disabled = False  # type: ignore[assignment]
        await interaction.response.edit_message(
            content=f"Race: **{self.race.value}** -- Choisis ta classe:", view=self
        )

    # ── Class ─────────────────────────────────────────────────────────────

    @ui.select(
        placeholder="Choisis ta classe...",
        options=[
            discord.SelectOption(label=c.value, value=c.value) for c in CharacterClass
        ],
    )
    async def select_class(
        self,
        interaction: discord.Interaction,
        select: ui.Select["CharacterCreateView"],
    ) -> None:
        """Handle class selection and unlock the alignment menu."""
        self.char_class = CharacterClass(select.values[0])
        self.select_alignment.disabled = False  # type: ignore[assignment]
        await interaction.response.edit_message(
            content=(
                f"Race: **{self.race.value}** | "  # type: ignore[union-attr]
                f"Classe: **{self.char_class.value}** -- Choisis ton alignement:"
            ),
            view=self,
        )

    # ── Alignment ─────────────────────────────────────────────────────────

    @ui.select(
        placeholder="Choisis ton alignement...",
        options=[
            discord.SelectOption(label=a.value, value=a.value) for a in Alignment
        ],
    )
    async def select_alignment(
        self,
        interaction: discord.Interaction,
        select: ui.Select["CharacterCreateView"],
    ) -> None:
        """Handle alignment selection and open the name modal."""
        self.alignment = Alignment(select.values[0])
        modal = CharacterNameModal(self)
        await interaction.response.send_modal(modal)


class CharacterNameModal(ui.Modal, title="Nom du personnage"):
    """Modal that collects the character's name to finish creation."""

    name_input: ui.TextInput[CharacterNameModal] = ui.TextInput(
        label="Nom", placeholder="Ex: Thorin", min_length=1, max_length=50
    )

    def __init__(self, parent_view: CharacterCreateView) -> None:
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Store the name, mark the parent view complete, and stop it."""
        self.parent_view.character_name = self.name_input.value
        self.parent_view.completed = True
        self.parent_view.stop()
        if self.parent_view._on_complete is not None:
            await self.parent_view._on_complete(interaction, self.parent_view)
        else:
            await interaction.response.defer()
