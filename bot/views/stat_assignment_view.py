"""Stat assignment view — Standard Array allocation via sequential selection."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

import discord
from discord import ui

from bot.views.base import LoggedView
from engine.character import STANDARD_ARRAY, Ability, CharacterClass

logger = logging.getLogger(__name__)

# Callback type: async fn(interaction, assignments) -> None
OnStatsConfirmed = Callable[
    [discord.Interaction, dict[Ability, int]],
    Coroutine[Any, Any, None],
]

# Primary stats per class for "Recommande" hints
CLASS_PRIMARY_STATS: dict[CharacterClass, list[Ability]] = {
    CharacterClass.FIGHTER: [Ability.STR, Ability.CON],
    CharacterClass.BARBARIAN: [Ability.STR, Ability.CON],
    CharacterClass.WIZARD: [Ability.INT],
    CharacterClass.ROGUE: [Ability.DEX],
    CharacterClass.CLERIC: [Ability.WIS],
    CharacterClass.RANGER: [Ability.DEX, Ability.WIS],
}

# French labels for abilities
ABILITY_LABELS_FR: dict[Ability, str] = {
    Ability.STR: "Force (STR)",
    Ability.DEX: "Dexterite (DEX)",
    Ability.CON: "Constitution (CON)",
    Ability.INT: "Intelligence (INT)",
    Ability.WIS: "Sagesse (WIS)",
    Ability.CHA: "Charisme (CHA)",
}


def _build_status_text(
    assignments: dict[Ability, int],
    char_class: CharacterClass,
) -> str:
    """Build a status string showing current stat assignments."""
    primary = CLASS_PRIMARY_STATS.get(char_class, [])
    lines: list[str] = []
    for ability in Ability:
        label = ABILITY_LABELS_FR.get(ability, ability.value)
        hint = " *" if ability in primary else ""
        if ability in assignments:
            lines.append(f"**{label}**: {assignments[ability]}{hint}")
        else:
            lines.append(f"{label}: --{hint}")

    remaining = get_remaining_values(assignments)
    remaining_str = ", ".join(str(v) for v in sorted(remaining, reverse=True))

    text = "**Attribution des stats (Standard Array)**\n\n"
    text += "\n".join(lines)
    text += f"\n\nValeurs restantes: `{remaining_str or 'aucune'}`"
    if primary:
        primary_names = ", ".join(ABILITY_LABELS_FR.get(a, a.value) for a in primary)
        text += f"\n_* Recommande pour {char_class.value}: {primary_names}_"
    return text


def get_remaining_values(assignments: dict[Ability, int]) -> list[int]:
    """Return Standard Array values not yet assigned."""
    used = list(assignments.values())
    remaining = list(STANDARD_ARRAY)
    for v in used:
        if v in remaining:
            remaining.remove(v)
    return remaining


class StatAssignmentView(LoggedView):
    """Sequential stat assignment using Standard Array.

    Uses a single select menu where the user picks a value for each stat
    in sequence (STR, DEX, CON, INT, WIS, CHA). After each pick, the
    next stat is presented with remaining values.

    Fits within Discord's 5 action row limit: 1 select + 1 button row.
    """

    timeout = 300.0  # 5 minutes

    def __init__(
        self,
        char_class: CharacterClass,
        on_confirmed: OnStatsConfirmed,
    ) -> None:
        super().__init__(timeout=self.timeout)
        self.char_class = char_class
        self._on_confirmed = on_confirmed
        self.assignments: dict[Ability, int] = {}
        self._current_stat_index: int = 0
        self._all_stats = list(Ability)

        # Initial state: show first stat's value selection
        self._update_select_options()
        self.confirm_button.disabled = True  # type: ignore[assignment]

    @property
    def current_stat(self) -> Ability | None:
        """The stat currently being assigned, or None if all done."""
        if self._current_stat_index < len(self._all_stats):
            return self._all_stats[self._current_stat_index]
        return None

    @property
    def all_assigned(self) -> bool:
        """True when all 6 stats have been assigned."""
        return len(self.assignments) == 6

    def _update_select_options(self) -> None:
        """Update the select menu to show remaining values for current stat."""
        remaining = get_remaining_values(self.assignments)
        stat = self.current_stat

        if stat is None or not remaining:
            self.value_select.options = [  # type: ignore[assignment]
                discord.SelectOption(label="Termine", value="done"),
            ]
            self.value_select.disabled = True  # type: ignore[assignment]
            return

        label = ABILITY_LABELS_FR.get(stat, stat.value)
        self.value_select.placeholder = f"Valeur pour {label}..."  # type: ignore[assignment]
        self.value_select.disabled = False  # type: ignore[assignment]
        self.value_select.options = [  # type: ignore[assignment]
            discord.SelectOption(label=str(v), value=str(v))
            for v in sorted(remaining, reverse=True)
        ]

    def get_status_text(self) -> str:
        """Get the current status text for display."""
        return _build_status_text(self.assignments, self.char_class)

    # ── Select: pick value for current stat ───────────────────────────────

    @ui.select(
        placeholder="Choisis une valeur...",
        options=[discord.SelectOption(label="15", value="15")],  # placeholder
    )
    async def value_select(
        self,
        interaction: discord.Interaction,
        select: ui.Select[StatAssignmentView],
    ) -> None:
        """Assign the selected value to the current stat."""
        stat = self.current_stat
        if stat is None:
            await interaction.response.defer()
            return

        value = int(select.values[0])
        self.assignments[stat] = value
        self._current_stat_index += 1

        # Update UI
        self._update_select_options()
        self.confirm_button.disabled = not self.all_assigned  # type: ignore[assignment]

        await interaction.response.edit_message(
            content=self.get_status_text(),
            view=self,
        )

    # ── Buttons ───────────────────────────────────────────────────────────

    @ui.button(label="Confirmer", style=discord.ButtonStyle.success, row=2)
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button[StatAssignmentView],
    ) -> None:
        """Confirm the stat assignments."""
        if not self.all_assigned:
            await interaction.response.defer()
            return

        self.stop()
        await self._on_confirmed(interaction, dict(self.assignments))

    @ui.button(label="Reinitialiser", style=discord.ButtonStyle.danger, row=2)
    async def reset_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button[StatAssignmentView],
    ) -> None:
        """Reset all stat assignments."""
        self.assignments.clear()
        self._current_stat_index = 0
        self._update_select_options()
        self.confirm_button.disabled = True  # type: ignore[assignment]

        await interaction.response.edit_message(
            content=self.get_status_text(),
            view=self,
        )
