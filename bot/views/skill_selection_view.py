"""Skill selection view — class-based skill proficiency picker."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

import discord
from discord import ui

from bot.views.base import LoggedView
from engine.character import CLASS_SKILL_CHOICES, CharacterClass, Skill, SKILL_ABILITY

logger = logging.getLogger(__name__)

# Callback type: async fn(interaction, skills) -> None
OnSkillsConfirmed = Callable[
    [discord.Interaction, list[Skill]],
    Coroutine[Any, Any, None],
]

# French labels for abilities (short)
_ABILITY_SHORT_FR: dict[str, str] = {
    "STR": "FOR",
    "DEX": "DEX",
    "CON": "CON",
    "INT": "INT",
    "WIS": "SAG",
    "CHA": "CHA",
}


def build_skill_options(char_class: CharacterClass) -> list[discord.SelectOption]:
    """Build select options for the skills available to a class."""
    config = CLASS_SKILL_CHOICES[char_class]
    options: list[discord.SelectOption] = []
    for skill in config.options:
        ability = SKILL_ABILITY[skill]
        ability_label = _ABILITY_SHORT_FR.get(ability.value, ability.value)
        options.append(
            discord.SelectOption(
                label=f"{skill.value} ({ability_label})",
                value=skill.value,
            )
        )
    return options


def get_skill_count(char_class: CharacterClass) -> int:
    """Return how many skills the class must choose."""
    return CLASS_SKILL_CHOICES[char_class].choose


class SkillSelectionView(LoggedView):
    """Skill proficiency selection for a character class.

    Shows available skills as a multi-select with max_values set to the
    class's required skill count.
    """

    timeout = 300.0  # 5 minutes

    def __init__(
        self,
        char_class: CharacterClass,
        on_confirmed: OnSkillsConfirmed,
    ) -> None:
        super().__init__(timeout=self.timeout)
        self.char_class = char_class
        self._on_confirmed = on_confirmed
        self.selected_skills: list[Skill] = []

        config = CLASS_SKILL_CHOICES[char_class]
        self.required_count = config.choose

        # Configure the select menu
        options = build_skill_options(char_class)
        self.skill_select.options = options  # type: ignore[assignment]
        self.skill_select.max_values = config.choose  # type: ignore[assignment]
        self.skill_select.min_values = config.choose  # type: ignore[assignment]
        placeholder = f"Choisis {config.choose} competence{'s' if config.choose > 1 else ''}..."
        self.skill_select.placeholder = placeholder  # type: ignore[assignment]

        self.confirm_button.disabled = True  # type: ignore[assignment]

    # ── Select: pick skills ───────────────────────────────────────────────

    @ui.select(
        placeholder="Choisis tes competences...",
        options=[discord.SelectOption(label="placeholder", value="placeholder")],
        min_values=1,
        max_values=1,
    )
    async def skill_select(
        self,
        interaction: discord.Interaction,
        select: ui.Select[SkillSelectionView],
    ) -> None:
        """Handle skill selection."""
        selected = [Skill(v) for v in select.values]
        self.selected_skills = selected
        self.confirm_button.disabled = False  # type: ignore[assignment]

        skills_text = ", ".join(
            f"**{s.value}** ({_ABILITY_SHORT_FR.get(SKILL_ABILITY[s].value, SKILL_ABILITY[s].value)})"
            for s in selected
        )
        await interaction.response.edit_message(
            content=(
                f"**Selection des competences**\n\n"
                f"Competences choisies: {skills_text}\n\n"
                f"Clique sur **Confirmer** pour valider."
            ),
            view=self,
        )

    # ── Buttons ───────────────────────────────────────────────────────────

    @ui.button(label="Confirmer", style=discord.ButtonStyle.success, row=2)
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button[SkillSelectionView],
    ) -> None:
        """Confirm the skill selection."""
        if len(self.selected_skills) != self.required_count:
            await interaction.response.defer()
            return

        self.stop()
        await self._on_confirmed(interaction, list(self.selected_skills))
