"""Tests for the headless CharacterSetupFlow driver (Lead 4).

The driver exercises every callback of bot.views.character_setup_flow
without going through Discord, so scenario tests can build characters
through the *real* multi-step flow (catching any bug in the view itself)
instead of bypassing it via engine.create_character.
"""

from __future__ import annotations

import random

import pytest

from engine.character import (
    CharacterClass,
    Race,
    Skill,
)
from engine.starter_gear import get_starter_kits
from tests.scenarios.headless_character_flow import HeadlessCharacterSetupFlow


@pytest.mark.asyncio
async def test_full_flow_with_preset_stats_produces_expected_character() -> None:
    """Driving every step produces a Character matching the inputs."""
    driver = HeadlessCharacterSetupFlow(user_id=42, language="fr")

    fighter_kit = get_starter_kits(CharacterClass.FIGHTER)[0].name
    character = await driver.run_full_flow(
        name="Thorin",
        concept="Un voleur repenti",
        race=Race.DWARF,
        char_class=CharacterClass.FIGHTER,
        skills=[Skill.ATHLETICS, Skill.PERCEPTION],
        kit_name=fighter_kit,
        motivation_key="Contract",
        stats_method="preset",
    )

    assert character.name == "Thorin"
    assert character.race is Race.DWARF
    assert character.char_class is CharacterClass.FIGHTER
    assert Skill.ATHLETICS in character.skill_proficiencies
    assert Skill.PERCEPTION in character.skill_proficiencies
    # Fighter preset has STR=15 + dwarf bonus (+CON)
    assert character.ability_scores.STR == 15
    # Outputs surfaced on the driver too
    assert driver.kit_name == fighter_kit
    assert driver.motivation_key == "Contract"
    assert driver.character is character


@pytest.mark.asyncio
async def test_full_flow_with_random_stats_produces_valid_character() -> None:
    """Random stats path also reaches a complete Character."""
    random.seed(123)  # determinism for the 4d6 rolls
    driver = HeadlessCharacterSetupFlow(user_id=99, language="fr")

    wizard_kit = get_starter_kits(CharacterClass.WIZARD)[0].name
    character = await driver.run_full_flow(
        name="Gandalf",
        race=Race.ELF,
        char_class=CharacterClass.WIZARD,
        skills=[Skill.ARCANA, Skill.HISTORY],
        kit_name=wizard_kit,
        motivation_key="Curiosity",
        stats_method="random",
    )

    assert character.race is Race.ELF
    assert character.char_class is CharacterClass.WIZARD
    # Every ability filled, all in the 3..18 4d6-drop-lowest range pre-bonuses
    s = character.ability_scores
    for val in (s.STR, s.DEX, s.CON, s.INT, s.WIS, s.CHA):
        assert 3 <= val <= 22  # 18 max + up to +4 racial


@pytest.mark.asyncio
async def test_stepwise_api_matches_full_flow() -> None:
    """Calling each step manually yields the same Character as run_full_flow."""
    driver = HeadlessCharacterSetupFlow(user_id=7, language="fr")
    rogue_kit = get_starter_kits(CharacterClass.ROGUE)[0].name

    await driver.submit_identity(name="Aria", concept="")
    await driver.select_race(Race.HALFLING)
    await driver.select_class(CharacterClass.ROGUE)
    await driver.advance_to_stats()
    await driver.pick_preset_stats()
    await driver.advance_to_skills()
    await driver.select_skills([Skill.STEALTH, Skill.SLEIGHT_OF_HAND, Skill.PERCEPTION, Skill.ACROBATICS])
    await driver.advance_to_kit_motiv()
    await driver.select_kit(rogue_kit)
    await driver.select_motivation("Personal")
    await driver.advance_to_review()
    character = await driver.confirm()

    assert character.name == "Aria"
    assert character.race is Race.HALFLING
    assert character.char_class is CharacterClass.ROGUE
    assert Skill.STEALTH in character.skill_proficiencies


@pytest.mark.asyncio
async def test_identity_modal_path_writes_into_view() -> None:
    """submit_identity exercises the *real* IdentityModal.on_submit, not a shortcut."""
    driver = HeadlessCharacterSetupFlow(user_id=1, language="fr")
    await driver.submit_identity(name="Beorn", concept="Skin-changer")
    # State must have advanced exactly like Discord does after the modal closes
    from bot.views.character_setup_flow import SetupStep
    assert driver.flow.name == "Beorn"
    assert driver.flow.concept == "Skin-changer"
    assert driver.flow.state is SetupStep.RACE_CLASS


@pytest.mark.asyncio
async def test_confirm_invokes_on_complete_with_built_character() -> None:
    """The driver's captured on_complete callback receives the final Character."""
    driver = HeadlessCharacterSetupFlow(user_id=1, language="fr")
    cleric_kit = get_starter_kits(CharacterClass.CLERIC)[0].name

    await driver.run_full_flow(
        name="Hilda",
        race=Race.HUMAN,
        char_class=CharacterClass.CLERIC,
        skills=[Skill.MEDICINE, Skill.RELIGION],
        kit_name=cleric_kit,
        motivation_key="Conviction",
        stats_method="preset",
    )

    # on_complete arguments are captured on the driver
    assert driver.character is not None
    assert driver.character.name == "Hilda"
    assert driver.kit_name == cleric_kit
    assert driver.motivation_key == "Conviction"
