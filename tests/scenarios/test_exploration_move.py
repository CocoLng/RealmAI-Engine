"""Scenario tests for ScenarioRunner.move().

The runner's exploration ``move`` used to be a no-op narration stub.
After Lead 3 it resolves the requested direction against
``current_location.exit_aliases`` and delegates to
:func:`bot.world_navigation.change_location` so the in-memory session
state actually mutates and downstream NPCs/items become visible.
"""

from __future__ import annotations

import pytest

from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from engine.character import AbilityScores, Race
from tests.scenarios.scenario_runner import ScenarioRunner
from world.location import Location
from world.npc import NPC, NPCDisposition


def _make_npc(name: str, location_name: str) -> NPC:
    """Minimal commoner NPC for location seeding."""
    return NPC(
        name=name,
        race=Race.HUMAN,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=8,
        max_hp=8,
        ac=10,
        disposition=NPCDisposition.NEUTRAL,
        location_name=location_name,
    )


@pytest.mark.asyncio
async def test_move_delegates_to_change_location_with_preseed(
    scenario: ScenarioRunner,
) -> None:
    """When the destination is already in the DB, move() should swap
    ``session.current_location`` and reload NPCs without needing Ollama."""
    await scenario.start_campaign(theme="Test", players=1)
    await scenario.add_player(name="Aria", race="Elf", class_="Wizard")
    session = scenario.session
    assert session is not None

    start = Location(
        name="Cave entrance",
        description="A wide cave mouth.",
        connections=["Salle des échos"],
        exit_aliases={"Salle des échos": ["nord", "north"]},
    )
    destination = Location(
        name="Salle des échos",
        description="A vast vaulted chamber.",
        connections=["Cave entrance"],
        npcs_present=["Garm"],
        generated=True,
    )
    session.current_location = start

    db = scenario.bot.db_factory()
    try:
        LocationRepository(db).upsert(start, session.campaign.id)
        LocationRepository(db).upsert(destination, session.campaign.id)
        NPCRepository(db).save(_make_npc("Garm", "Salle des échos"), session.campaign.id)
        db.commit()
    finally:
        db.close()

    await scenario.move("nord")

    assert session.current_location is not None
    assert session.current_location.name == "Salle des échos"
    assert "Garm" in session.npcs


@pytest.mark.asyncio
async def test_move_unknown_direction_keeps_location(
    scenario: ScenarioRunner,
) -> None:
    """Direction that maps to no exit must keep current_location intact."""
    await scenario.start_campaign(theme="Test", players=1)
    await scenario.add_player(name="Aria", race="Elf", class_="Wizard")
    session = scenario.session
    assert session is not None
    start = Location(
        name="Cave entrance",
        description="A wide cave mouth.",
        connections=["Salle des échos"],
        exit_aliases={"Salle des échos": ["nord"]},
    )
    session.current_location = start

    await scenario.move("ouest")

    assert session.current_location is not None
    assert session.current_location.name == "Cave entrance"


@pytest.mark.asyncio
async def test_move_falls_back_gracefully_when_no_ollama(
    scenario: ScenarioRunner,
) -> None:
    """If the destination needs LLM hydration but no client is wired, the
    runner must not raise — it returns a stub-like response and leaves
    state untouched."""
    await scenario.start_campaign(theme="Test", players=1)
    await scenario.add_player(name="Aria", race="Elf", class_="Wizard")
    session = scenario.session
    assert session is not None
    assert session.ollama_client is None  # The default scenario has no Ollama
    start = Location(
        name="Cave entrance",
        description="A wide cave mouth.",
        connections=["Salle des échos"],
        exit_aliases={"Salle des échos": ["nord"]},
    )
    session.current_location = start

    cap = await scenario.move("nord")

    assert session.current_location is not None
    assert session.current_location.name == "Cave entrance"
    assert cap.embed is not None or cap.content is not None
