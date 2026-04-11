"""Character edit flow — sequential editor for selected character fields.

Orchestrates editing of specific character fields by chaining existing
sub-views (StatAssignmentView, SkillSelectionView, etc.) in canonical
order. Only the fields the player chose to modify are presented.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

import discord
from discord import ui

from bot.i18n import ALIGNMENT_LABELS, CLASS_LABELS, RACE_LABELS, get_label
from bot.views.base import LoggedView
from engine.character import Ability, Alignment, CharacterClass, Character, Race, Skill

logger = logging.getLogger(__name__)

# Callback type: async fn(interaction, flow) -> None
OnEditCompleteCallback = Callable[
    [discord.Interaction, "CharacterEditFlow"],
    Coroutine[Any, Any, None],
]

# Canonical order for edit steps
_STEP_ORDER = ("race", "class", "alignment", "stats", "skills", "name")


class CharacterEditFlow:
    """Orchestrates sequential editing of selected character fields.

    Not a View itself — creates and presents temporary views for each step.
    Reuses StatAssignmentView, SkillSelectionView, and a standalone name modal.

    Parameters
    ----------
    character:
        The existing character being edited.
    raw_assignments:
        The original pre-racial-bonus ability assignments (Standard Array mapping).
    language:
        BCP-47 language code for UI labels.
    on_complete:
        Async callback invoked when all edits are done.
    """

    def __init__(
        self,
        character: Character,
        raw_assignments: dict[Ability, int],
        language: str,
        on_complete: OnEditCompleteCallback,
    ) -> None:
        # Working state — initialized from existing character
        self.race: Race = character.race
        self.char_class: CharacterClass = character.char_class
        self.alignment: Alignment = character.alignment
        self.ability_assignments: dict[Ability, int] = dict(raw_assignments)
        self.skill_proficiencies: list[Skill] = list(character.skill_proficiencies)
        self.character_name: str = character.name

        self.original_class: CharacterClass = character.char_class
        self.class_changed: bool = False
        self.language = language
        self._on_complete = on_complete
        self._edit_queue: list[str] = []

    def _apply_cascades(self, fields: set[str]) -> set[str]:
        """Add dependent fields based on cascade rules."""
        if "race" in fields:
            fields.add("stats")  # racial bonuses change
        if "class" in fields:
            fields.add("skills")  # skill pool changes
        return fields

    async def start(
        self,
        interaction: discord.Interaction,
        selected_fields: list[str],
    ) -> None:
        """Apply cascades, build edit queue, and start first step."""
        fields = self._apply_cascades(set(selected_fields))
        self._edit_queue = [f for f in _STEP_ORDER if f in fields]

        logger.info(
            "EDIT_FLOW start user=%s fields=%s queue=%s",
            interaction.user, selected_fields, self._edit_queue,
        )

        await self._advance(interaction)

    async def _advance(self, interaction: discord.Interaction) -> None:
        """Pop the next step and present the appropriate sub-view."""
        if not self._edit_queue:
            await self._finish(interaction)
            return

        step = self._edit_queue.pop(0)
        dispatch = {
            "race": self._show_race_select,
            "class": self._show_class_select,
            "alignment": self._show_alignment_select,
            "stats": self._show_stats,
            "skills": self._show_skills,
            "name": self._show_name,
        }
        handler = dispatch[step]
        await handler(interaction)

    # ── Race ─────────────────────────────────────────────────────────────

    async def _show_race_select(self, interaction: discord.Interaction) -> None:
        """Present a race selection view with current race as default."""
        view = _SingleSelectView(
            placeholder="Choisis ta race...",
            options=[
                discord.SelectOption(
                    label=get_label(RACE_LABELS, self.language, r.value),
                    value=r.value,
                    default=(r == self.race),
                )
                for r in Race
            ],
            on_selected=self._on_race_selected,
        )
        await interaction.response.edit_message(
            content=f"Race actuelle: **{get_label(RACE_LABELS, self.language, self.race.value)}** — Choisis ta nouvelle race:",
            view=view,
        )

    async def _on_race_selected(
        self, interaction: discord.Interaction, value: str,
    ) -> None:
        self.race = Race(value)
        await self._advance(interaction)

    # ── Class ────────────────────────────────────────────────────────────

    async def _show_class_select(self, interaction: discord.Interaction) -> None:
        """Present a class selection view with current class as default."""
        view = _SingleSelectView(
            placeholder="Choisis ta classe...",
            options=[
                discord.SelectOption(
                    label=get_label(CLASS_LABELS, self.language, c.value),
                    value=c.value,
                    default=(c == self.char_class),
                )
                for c in CharacterClass
            ],
            on_selected=self._on_class_selected,
        )
        await interaction.response.edit_message(
            content=f"Classe actuelle: **{get_label(CLASS_LABELS, self.language, self.char_class.value)}** — Choisis ta nouvelle classe:",
            view=view,
        )

    async def _on_class_selected(
        self, interaction: discord.Interaction, value: str,
    ) -> None:
        self.char_class = CharacterClass(value)
        await self._advance(interaction)

    # ── Alignment ────────────────────────────────────────────────────────

    async def _show_alignment_select(self, interaction: discord.Interaction) -> None:
        """Present an alignment selection view with current alignment as default."""
        view = _SingleSelectView(
            placeholder="Choisis ton alignement...",
            options=[
                discord.SelectOption(
                    label=get_label(ALIGNMENT_LABELS, self.language, a.value),
                    value=a.value,
                    default=(a == self.alignment),
                )
                for a in Alignment
            ],
            on_selected=self._on_alignment_selected,
        )
        await interaction.response.edit_message(
            content=f"Alignement actuel: **{get_label(ALIGNMENT_LABELS, self.language, self.alignment.value)}** — Choisis ton nouvel alignement:",
            view=view,
        )

    async def _on_alignment_selected(
        self, interaction: discord.Interaction, value: str,
    ) -> None:
        self.alignment = Alignment(value)
        await self._advance(interaction)

    # ── Stats ────────────────────────────────────────────────────────────

    async def _show_stats(self, interaction: discord.Interaction) -> None:
        """Present the stat assignment view."""
        from bot.views.stat_assignment_view import StatAssignmentView

        stat_view = StatAssignmentView(
            char_class=self.char_class,
            on_confirmed=self._on_stats_confirmed,
        )
        await interaction.response.edit_message(
            content=stat_view.get_status_text(),
            view=stat_view,
        )

    async def _on_stats_confirmed(
        self, interaction: discord.Interaction, assignments: dict[Ability, int],
    ) -> None:
        self.ability_assignments = assignments
        await self._advance(interaction)

    # ── Skills ───────────────────────────────────────────────────────────

    async def _show_skills(self, interaction: discord.Interaction) -> None:
        """Present the skill selection view for the (possibly new) class."""
        from bot.views.skill_selection_view import SkillSelectionView

        skill_view = SkillSelectionView(
            char_class=self.char_class,
            on_confirmed=self._on_skills_confirmed,
        )
        config = skill_view.required_count
        await interaction.response.edit_message(
            content=(
                f"**Selection des competences**\n\n"
                f"Choisis {config} competence{'s' if config > 1 else ''} "
                f"pour ta classe :"
            ),
            view=skill_view,
        )

    async def _on_skills_confirmed(
        self, interaction: discord.Interaction, skills: list[Skill],
    ) -> None:
        self.skill_proficiencies = skills
        await self._advance(interaction)

    # ── Name ─────────────────────────────────────────────────────────────

    async def _show_name(self, interaction: discord.Interaction) -> None:
        """Present a name modal pre-filled with the current name."""
        modal = _EditNameModal(
            current_name=self.character_name,
            on_submitted=self._on_name_submitted,
        )
        await interaction.response.send_modal(modal)

    async def _on_name_submitted(
        self, interaction: discord.Interaction, name: str,
    ) -> None:
        self.character_name = name
        await self._advance(interaction)

    # ── Finish ───────────────────────────────────────────────────────────

    async def _finish(self, interaction: discord.Interaction) -> None:
        """All edits done — determine if class changed and invoke callback."""
        self.class_changed = self.char_class != self.original_class
        logger.info(
            "EDIT_FLOW finish user=%s class_changed=%s",
            interaction.user, self.class_changed,
        )
        await self._on_complete(interaction, self)


# =====================================================================
# Helper views / modals used by the flow
# =====================================================================


class _SingleSelectView(LoggedView):
    """Ephemeral single-select view with a confirm button."""

    timeout = 300.0

    def __init__(
        self,
        placeholder: str,
        options: list[discord.SelectOption],
        on_selected: Callable[
            [discord.Interaction, str], Coroutine[Any, Any, None]
        ],
    ) -> None:
        super().__init__(timeout=self.timeout)
        self._on_selected = on_selected
        self._value: str | None = None

        self.select_menu.placeholder = placeholder  # type: ignore[assignment]
        self.select_menu.options = options  # type: ignore[assignment]

    @ui.select(
        placeholder="Choisis...",
        options=[discord.SelectOption(label="placeholder", value="placeholder")],
    )
    async def select_menu(
        self,
        interaction: discord.Interaction,
        select: ui.Select[_SingleSelectView],
    ) -> None:
        """Store the selected value and enable confirm."""
        self._value = select.values[0]
        self.confirm_button.disabled = False  # type: ignore[assignment]
        await interaction.response.edit_message(view=self)

    @ui.button(label="Confirmer", style=discord.ButtonStyle.success, row=2, disabled=True)
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button[_SingleSelectView],
    ) -> None:
        """Confirm selection and call back."""
        if self._value is None:
            await interaction.response.defer()
            return
        self.stop()
        await self._on_selected(interaction, self._value)


class _EditNameModal(ui.Modal, title="Modifier le nom"):
    """Standalone name modal with a callback, pre-filled with the current name."""

    name_input: ui.TextInput[_EditNameModal] = ui.TextInput(
        label="Nom",
        placeholder="Ex: Thorin",
        min_length=1,
        max_length=50,
    )

    def __init__(
        self,
        current_name: str,
        on_submitted: Callable[
            [discord.Interaction, str], Coroutine[Any, Any, None]
        ],
    ) -> None:
        super().__init__()
        self.name_input.default = current_name
        self._on_submitted = on_submitted

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Store the name and call back."""
        await self._on_submitted(interaction, self.name_input.value)
