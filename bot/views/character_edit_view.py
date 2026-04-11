"""Character edit view — field selection menu for modifying an existing character."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import discord
from discord import ui

from bot.i18n import (
    ALIGNMENT_LABELS,
    CLASS_LABELS,
    EDIT_FIELD_LABELS,
    RACE_LABELS,
    get_label,
)
from bot.views.base import LoggedView
from engine.character import Ability, Character, compute_modifier

# Callback type: async fn(interaction, selected_fields) -> None
OnModifyCallback = Callable[
    [discord.Interaction, list[str]],
    Coroutine[Any, Any, None],
]

# Canonical field keys
EDITABLE_FIELDS = ("race", "class", "alignment", "stats", "skills", "name")


def _build_character_summary(character: Character, language: str) -> str:
    """Build a compact text summary of a character for the edit menu."""
    race_label = get_label(RACE_LABELS, language, character.race.value)
    class_label = get_label(CLASS_LABELS, language, character.char_class.value)
    align_label = get_label(ALIGNMENT_LABELS, language, character.alignment.value)

    stats_parts: list[str] = []
    for ability in Ability:
        score = character.ability_scores.get(ability)
        mod = compute_modifier(score)
        sign = "+" if mod >= 0 else ""
        stats_parts.append(f"{ability.value} {score}({sign}{mod})")

    skills_text = ", ".join(s.value for s in character.skill_proficiencies) or "Aucune"

    return (
        f"**{character.name}** — {race_label} {class_label}\n"
        f"Alignement: {align_label}\n"
        f"Stats: {' | '.join(stats_parts)}\n"
        f"HP: {character.hp}/{character.max_hp} | AC: {character.ac} | Vitesse: {character.speed}\n"
        f"Competences: {skills_text}"
    )


class CharacterEditView(LoggedView):
    """Menu for selecting which character fields to edit.

    Shows a summary of the current character and a multi-select to pick
    which fields to modify. The 'Modifier' button triggers the callback
    with the selected field keys.
    """

    timeout = 300.0

    def __init__(
        self,
        character: Character,
        language: str = "en",
        on_modify: OnModifyCallback | None = None,
    ) -> None:
        super().__init__(timeout=self.timeout)
        self.character = character
        self.language = language
        self._on_modify = on_modify
        self._selected_fields: list[str] = []

        # Build localised select options
        self.field_select.options = [  # type: ignore[assignment]
            discord.SelectOption(
                label=get_label(EDIT_FIELD_LABELS, language, key),
                value=key,
            )
            for key in EDITABLE_FIELDS
        ]

    def get_summary_text(self) -> str:
        """Return the character summary text for display."""
        return (
            "**Modification du personnage**\n\n"
            + _build_character_summary(self.character, self.language)
            + "\n\nQue souhaites-tu modifier ?"
        )

    # ── Multi-select: pick fields to edit ────────────────────────────────

    @ui.select(
        placeholder="Choisis les champs a modifier...",
        options=[discord.SelectOption(label="placeholder", value="placeholder")],
        min_values=1,
        max_values=6,
    )
    async def field_select(
        self,
        interaction: discord.Interaction,
        select: ui.Select[CharacterEditView],
    ) -> None:
        """Handle field selection."""
        self._selected_fields = list(select.values)
        self.modify_button.disabled = False  # type: ignore[assignment]
        labels = ", ".join(
            get_label(EDIT_FIELD_LABELS, self.language, f)
            for f in self._selected_fields
        )
        await interaction.response.edit_message(
            content=self.get_summary_text() + f"\n\nSelection: **{labels}**",
            view=self,
        )

    # ── Button: confirm and start editing ────────────────────────────────

    @ui.button(label="Modifier", style=discord.ButtonStyle.primary, row=2, disabled=True)
    async def modify_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button[CharacterEditView],
    ) -> None:
        """Confirm field selection and start the edit flow."""
        if not self._selected_fields:
            await interaction.response.defer()
            return

        self.stop()
        if self._on_modify is not None:
            await self._on_modify(interaction, list(self._selected_fields))
        else:
            await interaction.response.defer()
