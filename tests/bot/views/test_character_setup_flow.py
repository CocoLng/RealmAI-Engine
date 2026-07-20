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
async def test_kit_select_labels_are_localized():
    """Kit options must display the campaign-language label, not the raw English name."""
    from engine.character import CharacterClass
    from engine.starter_gear import get_starter_kits

    from bot.i18n import get_kit_label

    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.char_class = CharacterClass.ROGUE
    view.state = SetupStep.KIT_MOTIV
    view._build_kit_motiv_components()

    kit_select = next(c for c in view.children if isinstance(c, ui.Select) and c.custom_id == "setup_kit")
    kits = get_starter_kits(CharacterClass.ROGUE)
    assert [o.label for o in kit_select.options] == [
        get_kit_label("fr", k.name, "name") for k in kits
    ]
    # "Shadow Blade" must not leak through untranslated
    assert "Lame de l'ombre" in [o.label for o in kit_select.options]
    assert "Shadow Blade" not in [o.label for o in kit_select.options]


@pytest.mark.asyncio
async def test_kit_select_values_stay_canonical_english():
    """Only the display label is translated — the stored value stays the engine key."""
    from engine.character import CharacterClass
    from engine.starter_gear import get_starter_kits

    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.char_class = CharacterClass.ROGUE
    view.state = SetupStep.KIT_MOTIV
    view._build_kit_motiv_components()

    kit_select = next(c for c in view.children if isinstance(c, ui.Select) and c.custom_id == "setup_kit")
    assert [o.value for o in kit_select.options] == [k.name for k in get_starter_kits(CharacterClass.ROGUE)]


@pytest.mark.asyncio
async def test_kit_select_descriptions_are_localized():
    from engine.character import CharacterClass
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.char_class = CharacterClass.ROGUE
    view.state = SetupStep.KIT_MOTIV
    view._build_kit_motiv_components()

    kit_select = next(c for c in view.children if isinstance(c, ui.Select) and c.custom_id == "setup_kit")
    shadow = next(o for o in kit_select.options if o.value == "Shadow Blade")
    assert shadow.description == "Un roublard en double lame pour le combat rapproché."


@pytest.mark.asyncio
async def test_kit_select_unknown_language_falls_back_to_english():
    """An untranslated language keeps the engine name and description."""
    from engine.character import CharacterClass
    from engine.starter_gear import get_starter_kits

    view = CharacterSetupFlow(user_id=1, language="es", on_complete=AsyncMock())
    view.char_class = CharacterClass.ROGUE
    view.state = SetupStep.KIT_MOTIV
    view._build_kit_motiv_components()

    kit_select = next(c for c in view.children if isinstance(c, ui.Select) and c.custom_id == "setup_kit")
    kits = get_starter_kits(CharacterClass.ROGUE)
    assert [o.label for o in kit_select.options] == [k.name for k in kits]
    assert [o.description for o in kit_select.options] == [k.description[:100] for k in kits]


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


async def test_confirm_without_preview_does_not_persist():
    """Confirm on a stale view must not hand None to on_complete.

    The preview is only built at the REVIEW step; a view that never got
    there (or whose transition failed) would otherwise crash on
    ``char.name`` and, worse, persist a broken roster entry.
    """
    on_complete = AsyncMock()
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=on_complete)

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_confirm(interaction)

    on_complete.assert_not_called()
    content = interaction.response.edit_message.call_args.kwargs["content"]
    assert "n'a pas pu être finalisée" in content


async def test_cancel_notifies_the_lobby():
    """Annuler must tell the cog so the roster leaves the CREATING state."""
    on_cancel = AsyncMock()
    on_complete = AsyncMock()
    view = CharacterSetupFlow(
        user_id=1, language="fr", on_complete=on_complete, on_cancel=on_cancel,
    )

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_cancel(interaction)

    on_cancel.assert_awaited_once()
    on_complete.assert_not_called()
    content = interaction.response.edit_message.call_args.kwargs["content"]
    assert "annulée" in content


async def test_cancel_without_callback_still_closes_the_flow():
    """The callback stays optional — unit tests build flows without a lobby."""
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_cancel(interaction)

    interaction.response.edit_message.assert_awaited_once()


async def test_timeout_notifies_the_lobby():
    """A flow left idle for 10 min is an abandon too — same lobby notice."""
    on_cancel = AsyncMock()
    view = CharacterSetupFlow(
        user_id=1, language="fr", on_complete=AsyncMock(), on_cancel=on_cancel,
    )

    await view.on_timeout()

    on_cancel.assert_awaited_once()


async def test_review_recap_translates_kit_and_motivation():
    """Step 6/6 must not fall back to the canonical English keys.

    The kit select (step 5/6) shows French labels; the recap used to render
    the raw engine keys, so the same kit changed language between screens.
    """
    from engine.character import AbilityScores, CharacterClass, Race, Skill
    view = CharacterSetupFlow(user_id=1, language="fr", on_complete=AsyncMock())
    view.name = "Thorin"
    view.concept = ""
    view.race = Race.DWARF
    view.char_class = CharacterClass.FIGHTER
    view.ability_scores = AbilityScores(STR=15, DEX=14, CON=13, INT=12, WIS=10, CHA=8)
    view.skill_proficiencies = [Skill.ATHLETICS]
    view.kit_name = "Sword & Shield"
    view.motivation_key = "Contract"

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()
    await view.transition_to(interaction, SetupStep.REVIEW)

    embed = interaction.response.edit_message.call_args.kwargs["embed"]
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Kit de départ"] != "Sword & Shield"
    assert fields["Motivation"] != "Contract"
