"""End-to-end scenario: lobby-driven character creation.

Drives the lobby state machine directly (not the Discord components) to
prove the wiring between LobbyState, character creation, and the eventual
GameSession transition. Real engine + real DB, no Discord, no AI.
"""

from __future__ import annotations

import pytest

from bot.lobby_state import LobbyPlayerStatus, LobbyState
from db.repositories import PlayerCharacterRepository
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    apply_racial_bonuses,
    create_character,
)
from engine.inventory import create_inventory
from engine.spells import create_spellcaster_state
from engine.starter_gear import apply_starter_kit, get_starter_kits

from tests.scenarios.scenario_runner import ScenarioRunner


@pytest.mark.asyncio
async def test_lobby_two_players_complete_setup_then_launch(
    scenario: ScenarioRunner,
) -> None:
    """Full lobby flow with two players ending in a GameSession.

    Steps:
    1. Build a LobbyState with two players who joined.
    2. Each player completes their character (modeled after on_setup_complete).
    3. Verify both are READY and the lobby gates launch.
    4. Build a GameSession from the lobby roster.
    5. Verify the session has both characters with their kits applied.
    """
    p1 = scenario._make_player(0)
    p2 = scenario._make_player(1)

    lobby = LobbyState(creator_id=p1.id, language="fr")

    # ---- Player 1 joins and creates a Fighter ----
    lobby.add_player(p1.id)
    assert lobby.players[p1.id].status == LobbyPlayerStatus.JOINED

    lobby.set_status(p1.id, LobbyPlayerStatus.CREATING)
    raw_scores = AbilityScores(STR=15, DEX=13, CON=14, INT=10, WIS=12, CHA=8)
    boosted = apply_racial_bonuses(raw_scores, Race.HUMAN)
    char1 = create_character(
        name="Aragorn",
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=boosted,
        skill_proficiencies=[],
        concept="Ranger of the North",
    )
    inv1 = create_inventory()
    kits1 = get_starter_kits(CharacterClass.FIGHTER)
    inv1 = apply_starter_kit(kits1[0], inv1)
    spell1 = create_spellcaster_state(CharacterClass.FIGHTER, level=1)

    p1_record = lobby.players[p1.id]
    p1_record.character = char1
    p1_record.inventory = inv1
    p1_record.spellcaster = spell1
    p1_record.kit_name = kits1[0].name
    p1_record.motivation_key = "duty"
    lobby.set_status(p1.id, LobbyPlayerStatus.READY)

    # Lobby has at least one ready player => launch is allowed
    assert lobby.has_any_ready() is True

    # ---- Player 2 joins and creates a Wizard ----
    lobby.add_player(p2.id)
    lobby.set_status(p2.id, LobbyPlayerStatus.CREATING)

    raw_scores2 = AbilityScores(STR=8, DEX=12, CON=13, INT=15, WIS=14, CHA=10)
    boosted2 = apply_racial_bonuses(raw_scores2, Race.ELF)
    char2 = create_character(
        name="Gandalf",
        race=Race.ELF,
        char_class=CharacterClass.WIZARD,
        ability_scores=boosted2,
        skill_proficiencies=[],
        concept="Wandering scholar",
    )
    inv2 = create_inventory()
    kits2 = get_starter_kits(CharacterClass.WIZARD)
    inv2 = apply_starter_kit(kits2[0], inv2)
    spell2 = create_spellcaster_state(CharacterClass.WIZARD, level=1)

    p2_record = lobby.players[p2.id]
    p2_record.character = char2
    p2_record.inventory = inv2
    p2_record.spellcaster = spell2
    p2_record.kit_name = kits2[0].name
    p2_record.motivation_key = "knowledge"
    lobby.set_status(p2.id, LobbyPlayerStatus.READY)

    # ---- Persist both characters via the same path the cog uses ----
    # (Re-using a real campaign so we know FKs are honored.)
    await scenario.start_campaign(theme="Lobby End-to-End", players=2)
    session = scenario.session
    assert session is not None

    db_session = scenario.bot.db_factory()
    try:
        repo = PlayerCharacterRepository(db_session)
        repo.save(p1.id, session.campaign.id, char1, inv1, spell1)
        repo.save(p2.id, session.campaign.id, char2, inv2, spell2)
        db_session.commit()
    finally:
        db_session.close()

    # ---- "Launch": build the GameSession contents from the lobby ----
    ready_players = [
        p for p in lobby.players.values()
        if p.status == LobbyPlayerStatus.READY
        and p.character is not None
    ]
    assert len(ready_players) == 2

    # Inject characters into the session (mirrors what
    # _launch_campaign_from_lobby does internally).
    for p in ready_players:
        assert p.character is not None
        assert p.inventory is not None
        session.characters[p.user_id] = p.character
        session.inventories[p.user_id] = p.inventory
        if p.spellcaster is not None:
            session.spellcasters[p.user_id] = p.spellcaster

    # ---- Verifications ----
    assert p1.id in session.characters
    assert p2.id in session.characters
    assert session.characters[p1.id].name == "Aragorn"
    assert session.characters[p2.id].name == "Gandalf"
    assert session.characters[p1.id].char_class is CharacterClass.FIGHTER
    assert session.characters[p2.id].char_class is CharacterClass.WIZARD

    # Inventories were populated with starter kits — kits typically equip
    # items rather than dropping them in the bag, so we check both surfaces.
    inv1_total = len(session.inventories[p1.id].items) + len(session.inventories[p1.id].equipped)
    inv2_total = len(session.inventories[p2.id].items) + len(session.inventories[p2.id].equipped)
    assert inv1_total > 0
    assert inv2_total > 0

    # Wizards are casters
    assert session.spellcasters[p2.id] is not None


@pytest.mark.asyncio
async def test_lobby_blocks_launch_when_no_player_ready(
    scenario: ScenarioRunner,
) -> None:
    """has_any_ready returns False when nobody finished creation."""
    p1 = scenario._make_player(0)
    lobby = LobbyState(creator_id=p1.id, language="fr")
    lobby.add_player(p1.id)
    # Still JOINED — no character yet
    assert lobby.has_any_ready() is False
    # Even if we transition to CREATING, still not ready
    lobby.set_status(p1.id, LobbyPlayerStatus.CREATING)
    assert lobby.has_any_ready() is False


@pytest.mark.asyncio
async def test_lobby_player_can_cancel_and_leave(
    scenario: ScenarioRunner,
) -> None:
    """A player who cancelled mid-creation does not block the launch gate."""
    p1 = scenario._make_player(0)
    p2 = scenario._make_player(1)
    lobby = LobbyState(creator_id=p1.id, language="fr")

    lobby.add_player(p1.id)
    lobby.add_player(p2.id)

    # P1 finishes
    lobby.set_status(p1.id, LobbyPlayerStatus.CREATING)
    char1 = create_character(
        name="Solo",
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(
            STR=15, DEX=13, CON=14, INT=10, WIS=12, CHA=8,
        ),
        skill_proficiencies=[],
    )
    p1_record = lobby.players[p1.id]
    p1_record.character = char1
    p1_record.inventory = create_inventory()
    p1_record.spellcaster = None
    lobby.set_status(p1.id, LobbyPlayerStatus.READY)

    # P2 cancels mid-flow
    lobby.set_status(p2.id, LobbyPlayerStatus.CREATING)
    lobby.set_status(p2.id, LobbyPlayerStatus.CANCELLED)

    # Launch is still gated as OK because P1 is ready
    assert lobby.has_any_ready() is True

    ready_players = [
        p for p in lobby.players.values()
        if p.status == LobbyPlayerStatus.READY
    ]
    assert len(ready_players) == 1
    assert ready_players[0].user_id == p1.id
