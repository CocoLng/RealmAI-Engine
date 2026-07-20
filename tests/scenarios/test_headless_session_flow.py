"""Tests for the headless SessionCog.start_campaign driver (Lead 5).

Drives `SessionCog.start_campaign.callback` end-to-end through the
LobbyView, the CharacterSetupFlow (via Lead 4 headless driver), and the
launch path. ``create_session_channel`` and ``_pregenerate_campaign_world``
are patched so the test never touches Discord or Ollama.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.lobby_state import LobbyPlayerStatus
from engine.character import (
    CharacterClass,
    Race,
    Skill,
)
from engine.starter_gear import get_starter_kits
from tests.scenarios.headless_session_flow import HeadlessSessionFlow
from tests.scenarios.scenario_runner import ScenarioRunner
from world.location import Location
from world.story_arc import StoryArc, StoryBeat


def _make_arc(*, theme: str = "Crise du royaume") -> StoryArc:
    """Minimal but schema-valid StoryArc for launch tests.

    The model requires at least 8 beats — we fill with exploration stubs
    since the launch path only reads premise / situation / call_to_action
    / villain_name to build the opening crawl.
    """
    beats = [
        StoryBeat(
            beat_number=i + 1,
            title=f"Étape {i + 1}",
            description=f"Description de l'étape {i + 1}.",
            location_hint=f"Lieu {i + 1}",
            encounter_type="exploration",
            objectives=[],
        )
        for i in range(8)
    ]
    return StoryArc(
        campaign_id="",  # filled by the cog
        theme=theme,
        villain_name="Necros",
        villain_motivation="Asservir le royaume aux forces du néant.",
        premise="Le royaume vacille au bord du gouffre — un mal ancien revient.",
        situation="Une malédiction ronge la cité depuis trois lunes.",
        call_to_action="Vous êtes les seuls à pouvoir l'arrêter.",
        beats=beats,
    )


def _make_location(*, name: str = "La Croisée") -> Location:
    return Location(
        name=name,
        description="Un carrefour battu par le vent. Trois chemins partent.",
        arrival_hook="Vous arrivez à la croisée des chemins.",
        connections=["Forêt sombre", "Marais brumeux"],
        exit_aliases={
            "Forêt sombre": ["nord", "north"],
            "Marais brumeux": ["sud", "south"],
        },
        npcs_present=[],
    )


@pytest.mark.asyncio
async def test_full_session_flow_one_player_lands_opening_and_scene(
    scenario: ScenarioRunner,
) -> None:
    """Happy path: 1 host joins, completes setup, launches → opening + scene appear."""
    driver = HeadlessSessionFlow(scenario_runner=scenario)

    async with driver:
        await driver.start(
            host_idx=0,
            theme="Crise du royaume",
            pregen_arc=_make_arc(),
            pregen_location=_make_location(),
        )

        fighter_kit = get_starter_kits(CharacterClass.FIGHTER)[0].name
        await driver.add_player(
            player_idx=0,
            name="Thorin",
            race=Race.DWARF,
            char_class=CharacterClass.FIGHTER,
            skills=[Skill.ATHLETICS, Skill.PERCEPTION],
            kit_name=fighter_kit,
            motivation_key="Contract",
        )

        session = await driver.launch()

    # Session is live in the bot
    assert scenario.bot.sessions[scenario.channel.id] is session
    assert scenario.channel.id not in scenario.bot.lobbies
    assert session.current_location is not None
    assert session.current_location.name == "La Croisée"
    assert session.story_arc is not None
    assert session.story_arc.villain_name == "Necros"
    # Host's character is wired into the session
    host = scenario._make_player(0)
    assert session.characters[host.id].name == "Thorin"
    assert session.characters[host.id].char_class is CharacterClass.FIGHTER

    # Opening crawl (📜 + campaign name) AND scene embed (location name) both
    # landed in the channel during launch.
    embed_titles = [
        m.embed.title for m in scenario.channel_capture.messages
        if m.embed is not None and m.embed.title is not None
    ]
    assert any("Crise du royaume" in t for t in embed_titles), embed_titles
    assert any("La Croisée" in t for t in embed_titles), embed_titles


@pytest.mark.asyncio
async def test_two_players_complete_setup_then_host_launches(
    scenario: ScenarioRunner,
) -> None:
    """Two players join, each builds a different class, host launches."""
    driver = HeadlessSessionFlow(scenario_runner=scenario)

    async with driver:
        await driver.start(
            host_idx=0,
            theme="Duo",
            pregen_arc=_make_arc(),
            pregen_location=_make_location(),
        )

        wizard_kit = get_starter_kits(CharacterClass.WIZARD)[0].name
        await driver.add_player(
            player_idx=0,
            name="Gandalf",
            race=Race.ELF,
            char_class=CharacterClass.WIZARD,
            skills=[Skill.ARCANA, Skill.HISTORY],
            kit_name=wizard_kit,
            motivation_key="Curiosity",
        )

        rogue_kit = get_starter_kits(CharacterClass.ROGUE)[0].name
        await driver.add_player(
            player_idx=1,
            name="Aria",
            race=Race.HALFLING,
            char_class=CharacterClass.ROGUE,
            skills=[
                Skill.STEALTH, Skill.SLEIGHT_OF_HAND,
                Skill.PERCEPTION, Skill.ACROBATICS,
            ],
            kit_name=rogue_kit,
            motivation_key="Personal",
        )

        session = await driver.launch()

    p1 = scenario._make_player(0)
    p2 = scenario._make_player(1)
    assert p1.id in session.characters
    assert p2.id in session.characters
    assert session.characters[p1.id].char_class is CharacterClass.WIZARD
    assert session.characters[p2.id].char_class is CharacterClass.ROGUE


@pytest.mark.asyncio
async def test_lobby_transitions_from_bot_lobbies_to_bot_sessions(
    scenario: ScenarioRunner,
) -> None:
    """The channel id moves from bot.lobbies to bot.sessions on launch."""
    driver = HeadlessSessionFlow(scenario_runner=scenario)

    async with driver:
        await driver.start(
            host_idx=0,
            theme="Lifecycle",
            pregen_arc=_make_arc(),
            pregen_location=_make_location(),
        )
        # During the lobby phase, channel id lives in bot.lobbies
        assert scenario.channel.id in scenario.bot.lobbies
        assert scenario.channel.id not in scenario.bot.sessions

        fighter_kit = get_starter_kits(CharacterClass.FIGHTER)[0].name
        await driver.add_player(
            player_idx=0,
            name="Solo",
            race=Race.HUMAN,
            char_class=CharacterClass.FIGHTER,
            skills=[Skill.ATHLETICS, Skill.PERCEPTION],
            kit_name=fighter_kit,
            motivation_key="Contract",
        )
        # Still in lobby until host launches
        assert scenario.channel.id in scenario.bot.lobbies

        await driver.launch()

    assert scenario.channel.id not in scenario.bot.lobbies
    assert scenario.channel.id in scenario.bot.sessions


@pytest.mark.asyncio
async def test_cancelled_setup_marks_the_player_cancelled_in_the_roster(
    scenario: ScenarioRunner,
) -> None:
    """Annuler must not leave the player stuck at « Création en cours »."""
    driver = HeadlessSessionFlow(scenario_runner=scenario)

    async with driver:
        await driver.start(host_idx=0, theme="Abandon")
        await driver.cancel_player(player_idx=0)

        lobby = scenario.bot.lobbies[scenario.channel.id]
        host = scenario._make_player(0)
        assert lobby.players[host.id].status is LobbyPlayerStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancelled_player_can_join_again(
    scenario: ScenarioRunner,
) -> None:
    """A cancelled player re-clicking Rejoindre goes back to CREATING."""
    driver = HeadlessSessionFlow(scenario_runner=scenario)

    async with driver:
        await driver.start(host_idx=0, theme="Second essai")
        await driver.cancel_player(player_idx=0)
        await driver.click_join(player_idx=0)

        lobby = scenario.bot.lobbies[scenario.channel.id]
        host = scenario._make_player(0)
        assert lobby.players[host.id].status is LobbyPlayerStatus.CREATING


@pytest.mark.asyncio
async def test_stale_cancel_does_not_downgrade_a_ready_player(
    scenario: ScenarioRunner,
) -> None:
    """Cancelling an abandoned first flow must not unmake a finished character."""
    driver = HeadlessSessionFlow(scenario_runner=scenario)

    async with driver:
        await driver.start(
            host_idx=0,
            theme="Double flow",
            pregen_arc=_make_arc(),
            pregen_location=_make_location(),
        )
        # First flow, left open (the player will bail out of it later).
        stale_flow = await driver.click_join(player_idx=0)
        # Second flow, driven to completion → the player is READY.
        await driver.add_player(
            player_idx=0,
            name="Thorin",
            race=Race.DWARF,
            char_class=CharacterClass.FIGHTER,
            skills=[Skill.ATHLETICS, Skill.PERCEPTION],
            kit_name=get_starter_kits(CharacterClass.FIGHTER)[0].name,
            motivation_key="Contract",
        )

        inter = scenario._make_interaction(0)
        inter.response.edit_message = AsyncMock()
        await stale_flow._on_cancel(inter)

        lobby = scenario.bot.lobbies[scenario.channel.id]
        host = scenario._make_player(0)
        assert lobby.players[host.id].status is LobbyPlayerStatus.READY
