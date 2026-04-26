"""Unified character setup flow — single auto-modifying view, 6 steps.

Replaces CharacterCreateView, StatAssignmentView, SkillSelectionView,
StarterGearView, MotivationView. State transitions edit the same message
via discord.Interaction.response.edit_message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import IntEnum
from typing import TYPE_CHECKING

import discord
from discord import TextStyle, ui

from bot.views.base import LoggedView

if TYPE_CHECKING:
    from engine.character import (
        AbilityScores,
        Character,
        CharacterClass,
        Race,
        Skill,
    )

# (rest of the implementation lands in B4-B10)


class SetupStep(IntEnum):
    """Stages of the unified character setup flow."""

    IDENTITY = 0
    RACE_CLASS = 1
    STATS = 2
    SKILLS = 3
    KIT_MOTIV = 4
    REVIEW = 5


class IdentityModal(ui.Modal, title="Ton aventurier"):
    """Captures name + concept in one submit."""

    name = ui.TextInput(
        label="Nom du personnage",
        placeholder="Ex: Thorin Forgefort",
        min_length=1,
        max_length=32,
        required=True,
    )
    concept = ui.TextInput(
        label="Concept (optionnel)",
        placeholder="Ex: Un voleur repenti cherchant la rédemption",
        max_length=100,
        required=False,
        style=TextStyle.paragraph,
    )

    def __init__(self, parent_view: CharacterSetupFlow) -> None:
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parent_view.name = str(self.name.value)
        self.parent_view.concept = str(self.concept.value or "")
        await self.parent_view.transition_to(interaction, SetupStep.RACE_CLASS)


class CharacterSetupFlow(LoggedView):
    """Stub — full implementation in tasks B4-B10."""

    timeout = 600.0  # 10 minutes for the whole flow

    def __init__(
        self,
        user_id: int,
        language: str,
        on_complete: Callable[[Character, str, str], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=self.timeout)
        self.user_id = user_id
        self.language = language
        self._on_complete = on_complete
        self.state: SetupStep = SetupStep.IDENTITY
        # Accumulators (filled across steps)
        self.name: str | None = None
        self.concept: str | None = None
        self.race: Race | None = None
        self.char_class: CharacterClass | None = None
        self.ability_scores: AbilityScores | None = None
        self.skill_proficiencies: list[Skill] | None = None
        self.kit_name: str | None = None
        self.motivation_key: str | None = None

    async def transition_to(
        self, interaction: discord.Interaction, next_step: SetupStep,
    ) -> None:
        """Rebuild components for next_step and edit_message. Stub."""
        self.state = next_step
        # Implementations land in B4-B10
        raise NotImplementedError(f"Step {next_step} not yet implemented")
