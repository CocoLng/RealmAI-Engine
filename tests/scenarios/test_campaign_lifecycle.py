"""Scenario tests for campaign lifecycle: create, play, save, resume, end."""

from __future__ import annotations

import pytest

from tests.scenarios.scenario_runner import ScenarioRunner


@pytest.mark.asyncio
async def test_start_campaign_creates_session(scenario: ScenarioRunner) -> None:
    """Starting a campaign creates an active session."""
    await scenario.start_campaign(theme="Foret Sombre", players=1)
    assert scenario.session is not None
    assert scenario.session.campaign.name == "Foret Sombre"


@pytest.mark.asyncio
async def test_add_player_registers_character(scenario: ScenarioRunner) -> None:
    """Adding a player creates a character in the session."""
    await scenario.start_campaign(players=1)
    await scenario.add_player("Aragorn", race="human", class_="fighter", player_idx=0)

    char = scenario.get_character(0)
    assert char.name == "Aragorn"
    assert char.level == 1
    assert char.hp > 0


@pytest.mark.asyncio
async def test_two_players_registered(scenario: ScenarioRunner) -> None:
    """Two players can be added to the same campaign."""
    await scenario.start_campaign(players=2)
    await scenario.add_player("Guerrier", race="human", class_="fighter", player_idx=0)
    await scenario.add_player("Mage", race="elf", class_="wizard", player_idx=1)

    assert scenario.get_character(0).name == "Guerrier"
    assert scenario.get_character(1).name == "Mage"
    assert len(scenario.session.characters) == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_save_and_resume_preserves_characters(scenario: ScenarioRunner) -> None:
    """Save then resume round-trips character data."""
    await scenario.start_campaign(players=1)
    await scenario.add_player("Tester", race="dwarf", class_="fighter", player_idx=0)

    char_before = scenario.get_character(0)
    hp_before = char_before.hp
    name_before = char_before.name

    await scenario.save()
    scenario.clear_session()
    assert scenario.session is None

    await scenario.resume()
    assert scenario.session is not None
    assert scenario.get_character(0).name == name_before
    assert scenario.get_character(0).hp == hp_before


@pytest.mark.asyncio
async def test_end_campaign_cleans_up_session(scenario: ScenarioRunner) -> None:
    """Ending a campaign removes the in-memory session."""
    await scenario.start_campaign(players=1)
    await scenario.add_player("Doomed", race="human", class_="fighter")

    assert scenario.session is not None
    await scenario.end_campaign()
    assert scenario.session is None


@pytest.mark.asyncio
async def test_roll_dice(scenario: ScenarioRunner) -> None:
    """Roll dice returns a valid result."""
    await scenario.start_campaign(players=1)
    result = await scenario.roll("2d6+3")
    assert result.content is not None
    assert "2d6+3" in result.content
