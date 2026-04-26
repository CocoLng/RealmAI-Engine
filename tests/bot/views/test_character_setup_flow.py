"""Tests for the unified character setup flow (modal + state machine)."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui

from bot.views.character_setup_flow import (
    CharacterSetupFlow,
    IdentityModal,
    SetupStep,
)


def test_identity_modal_has_two_text_inputs():
    modal = IdentityModal(parent_view=None)  # type: ignore[arg-type]
    text_inputs = [c for c in modal.children if isinstance(c, discord.ui.TextInput)]
    assert len(text_inputs) == 2


def test_identity_modal_name_required():
    modal = IdentityModal(parent_view=None)  # type: ignore[arg-type]
    name_field = next(c for c in modal.children if c.label.startswith("Nom"))  # type: ignore[union-attr]
    assert name_field.required is True
    assert name_field.max_length == 32


def test_identity_modal_concept_optional():
    modal = IdentityModal(parent_view=None)  # type: ignore[arg-type]
    concept_field = next(c for c in modal.children if "Concept" in c.label)  # type: ignore[union-attr]
    assert concept_field.required is False
    assert concept_field.max_length == 100


@pytest.mark.asyncio
async def test_race_class_step_select_race_stores_value():
    from engine.character import Race
    on_complete = AsyncMock()
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=on_complete)
    view.state = SetupStep.RACE_CLASS

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    # Simulate the race select callback
    await view._on_race_selected(interaction, [Race.ELF.value])
    assert view.race == Race.ELF


@pytest.mark.asyncio
async def test_race_class_step_select_class_stores_value():
    from engine.character import CharacterClass
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.state = SetupStep.RACE_CLASS

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_class_selected(interaction, [CharacterClass.WIZARD.value])
    assert view.char_class == CharacterClass.WIZARD


@pytest.mark.asyncio
async def test_race_class_step_continue_disabled_until_both_selected():
    from engine.character import CharacterClass, Race
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.state = SetupStep.RACE_CLASS
    view._build_race_class_components()
    # The continue button should be present and disabled
    continue_btn = next(c for c in view.children if isinstance(c, ui.Button) and c.label and "Continuer" in c.label)
    assert continue_btn.disabled
    view.race = Race.ELF
    view.char_class = CharacterClass.WIZARD
    view._refresh_continue_state()
    assert not continue_btn.disabled
