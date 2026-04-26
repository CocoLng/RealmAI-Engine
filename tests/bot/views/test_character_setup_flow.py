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


@pytest.mark.asyncio
async def test_stats_step_preset_button_applies_class_preset():
    from engine.character import CharacterClass, Race
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.race = Race.ELF
    view.char_class = CharacterClass.WIZARD
    view.state = SetupStep.STATS

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_preset_stats(interaction)
    assert view.ability_scores is not None
    # Wizard preset has INT=15
    assert view.ability_scores.INT == 15


@pytest.mark.asyncio
async def test_stats_step_random_button_rolls_and_assigns():
    import random
    random.seed(42)
    from engine.character import CharacterClass, Race
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.race = Race.HUMAN
    view.char_class = CharacterClass.FIGHTER
    view.state = SetupStep.STATS

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_random_stats(interaction)
    assert view.ability_scores is not None
    # All 6 abilities filled
    assert all(getattr(view.ability_scores, a.name) >= 3 for a in __import__("engine.character", fromlist=["Ability"]).Ability)


@pytest.mark.asyncio
async def test_skills_step_select_records_choices():
    from engine.character import CharacterClass, Race, Skill
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.race = Race.HUMAN
    view.char_class = CharacterClass.ROGUE
    view.state = SetupStep.SKILLS

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_skills_selected(interaction, [Skill.STEALTH.value, Skill.DECEPTION.value])
    assert view.skill_proficiencies == [Skill.STEALTH, Skill.DECEPTION]


@pytest.mark.asyncio
async def test_skills_step_uses_class_skill_choices():
    from engine.character import CharacterClass
    from engine.character.classes import CLASS_SKILL_CHOICES
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.char_class = CharacterClass.WIZARD
    view.state = SetupStep.SKILLS
    view._build_skills_components()
    select = next(c for c in view.children if isinstance(c, ui.Select))
    config = CLASS_SKILL_CHOICES[CharacterClass.WIZARD]
    assert len(select.options) == len(config.options)
    assert select.max_values == config.choose


@pytest.mark.asyncio
async def test_kit_motiv_step_records_kit_and_motivation():
    from engine.character import CharacterClass
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.char_class = CharacterClass.FIGHTER
    view.state = SetupStep.KIT_MOTIV

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_kit_selected(interaction, ["Iron Vow"])
    await view._on_motivation_selected(interaction, ["Contract"])
    assert view.kit_name == "Iron Vow"
    assert view.motivation_key == "Contract"


@pytest.mark.asyncio
async def test_review_confirm_calls_on_complete():
    from engine.character import AbilityScores, CharacterClass, Race, Skill
    on_complete = AsyncMock()
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=on_complete)
    view.name = "Thorin"
    view.concept = ""
    view.race = Race.DWARF
    view.char_class = CharacterClass.FIGHTER
    view.ability_scores = AbilityScores(STR=15, DEX=14, CON=13, INT=12, WIS=10, CHA=8)
    view.skill_proficiencies = [Skill.ATHLETICS]
    view.kit_name = "Iron Vow"
    view.motivation_key = "Contract"

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view.transition_to(interaction, SetupStep.REVIEW)
    await view._on_confirm(interaction)
    on_complete.assert_called_once()
    args = on_complete.call_args.args
    assert args[0].name == "Thorin"
    assert args[1] == "Iron Vow"
    assert args[2] == "Contract"
