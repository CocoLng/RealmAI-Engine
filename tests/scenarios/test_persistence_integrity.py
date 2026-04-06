"""Scenario tests for save/resume persistence integrity.

Verifies that save/resume round-trips preserve all game state:
character stats, inventory, multi-player data, campaign metadata,
and combat damage.
"""

from __future__ import annotations

import pytest

from tests.scenarios.conftest import give_starter_weapon, make_weak_enemy
from tests.scenarios.scenario_runner import ScenarioRunner


@pytest.mark.asyncio
async def test_save_resume_preserves_character_stats(
    scenario: ScenarioRunner,
) -> None:
    """Save/resume round-trip preserves HP, name, race, and class."""
    await scenario.start_campaign(theme="Donjon des Ombres", players=1)
    await scenario.add_player("Khalid", race="Dwarf", class_="Fighter", player_idx=0)

    char_before = scenario.get_character(0)
    hp_before = char_before.hp
    max_hp_before = char_before.max_hp
    name_before = char_before.name
    race_before = char_before.race
    class_before = char_before.char_class

    await scenario.save()
    scenario.clear_session()
    await scenario.resume()

    char_after = scenario.get_character(0)
    assert char_after.name == name_before
    assert char_after.hp == hp_before
    assert char_after.max_hp == max_hp_before
    assert char_after.race == race_before
    assert char_after.char_class == class_before


@pytest.mark.asyncio
async def test_save_resume_preserves_inventory(scenario: ScenarioRunner) -> None:
    """Save/resume round-trip preserves equipped items."""
    await scenario.start_campaign(theme="Marche des Armes", players=1)
    await scenario.add_player("Gareth", race="Human", class_="Fighter", player_idx=0)

    give_starter_weapon(scenario, player_idx=0)
    inv_before = scenario.get_inventory(0)
    equipped_names_before = sorted(
        item.name for item in inv_before.equipped.values() if item is not None
    )
    assert len(equipped_names_before) > 0, "Weapon should be equipped before save"

    await scenario.save()
    scenario.clear_session()
    await scenario.resume()

    inv_after = scenario.get_inventory(0)
    equipped_names_after = sorted(
        item.name for item in inv_after.equipped.values() if item is not None
    )
    assert equipped_names_after == equipped_names_before


@pytest.mark.asyncio
async def test_save_resume_preserves_two_players(scenario: ScenarioRunner) -> None:
    """Save/resume with two players preserves both characters."""
    await scenario.start_campaign(theme="Duo Aventure", players=2)
    await scenario.add_player("Brynn", race="Elf", class_="Wizard", player_idx=0)
    await scenario.add_player("Tormund", race="Human", class_="Barbarian", player_idx=1)

    name_0 = scenario.get_character(0).name
    name_1 = scenario.get_character(1).name
    hp_0 = scenario.get_character(0).hp
    hp_1 = scenario.get_character(1).hp

    await scenario.save()
    scenario.clear_session()
    await scenario.resume()

    assert scenario.get_character(0).name == name_0
    assert scenario.get_character(1).name == name_1
    assert scenario.get_character(0).hp == hp_0
    assert scenario.get_character(1).hp == hp_1


@pytest.mark.asyncio
async def test_save_resume_preserves_campaign_name(scenario: ScenarioRunner) -> None:
    """Save/resume preserves the campaign name."""
    await scenario.start_campaign(theme="Crypte Oubliee", players=1)
    await scenario.add_player("Scout", race="Human", class_="Rogue", player_idx=0)

    await scenario.save()
    scenario.clear_session()
    await scenario.resume()

    assert scenario.session is not None
    assert scenario.session.campaign.name == "Crypte Oubliee"


@pytest.mark.asyncio
async def test_resume_without_prior_save_gives_error(
    scenario: ScenarioRunner,
) -> None:
    """Resuming when no campaign was saved returns an error message."""
    # Do not start or save anything — go straight to resume
    await scenario.resume()

    resp = scenario.last_response
    # The response should contain some indication that no campaign exists
    has_error = (resp.content is not None and len(resp.content) > 0) or (
        resp.embed is not None
    )
    assert has_error, "Expected an error response when resuming without a save"


@pytest.mark.asyncio
async def test_multiple_save_resume_cycles_no_corruption(
    scenario: ScenarioRunner,
) -> None:
    """Multiple save/resume cycles do not corrupt character data."""
    await scenario.start_campaign(theme="Boucle Temporelle", players=1)
    await scenario.add_player("Sable", race="Elf", class_="Ranger", player_idx=0)

    original_name = scenario.get_character(0).name
    original_hp = scenario.get_character(0).hp
    original_race = scenario.get_character(0).race
    original_class = scenario.get_character(0).char_class

    for _ in range(3):
        await scenario.save()
        scenario.clear_session()
        await scenario.resume()

    char = scenario.get_character(0)
    assert char.name == original_name
    assert char.hp == original_hp
    assert char.race == original_race
    assert char.char_class == original_class


@pytest.mark.asyncio
async def test_save_after_combat_damage_preserves_reduced_hp(
    scenario: ScenarioRunner,
) -> None:
    """Save after taking combat damage preserves the reduced HP value."""
    await scenario.start_campaign(theme="Arene Sanglante", players=1)
    await scenario.add_player("Valrik", race="Human", class_="Fighter", player_idx=0)

    give_starter_weapon(scenario, player_idx=0)
    hp_full = scenario.get_character(0).hp

    # Fight a weak enemy — the enemy may land a hit on its turn
    enemy = make_weak_enemy("Rat Geant")
    await scenario.start_combat(enemies=[enemy])
    await scenario.attack(target="Rat Geant", player_idx=0)

    hp_after_combat = scenario.get_character(0).hp
    # Whether the enemy hit or not, HP should be <= full
    assert hp_after_combat <= hp_full

    await scenario.save()
    scenario.clear_session()
    await scenario.resume()

    assert scenario.get_character(0).hp == hp_after_combat
