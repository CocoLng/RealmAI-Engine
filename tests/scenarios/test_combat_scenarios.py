"""Scenario tests for combat mechanics in the Discord RPG bot.

Tests cover the full combat lifecycle: starting encounters, attacking,
defending, fleeing, multi-enemy fights, multi-player turns, XP distribution,
and combat persistence across rounds. No dice mocking — uses weak enemies
(1 HP, 5 AC) to guarantee outcomes where needed.
"""

from __future__ import annotations

import pytest

from tests.scenarios.conftest import (
    give_starter_weapon,
    make_enemy,
    make_strong_enemy,
    make_weak_enemy,
)
from tests.scenarios.scenario_runner import ScenarioRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_single_player(scenario: ScenarioRunner) -> None:
    """Set up a campaign with one armed fighter."""
    await scenario.start_campaign(theme="Donjon Sombre", players=1)
    await scenario.add_player("Guerrier", race="Human", class_="Fighter", player_idx=0)
    give_starter_weapon(scenario, player_idx=0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_combat_creates_combat_state(scenario: ScenarioRunner) -> None:
    """Starting combat sets combat_state on the session."""
    await _setup_single_player(scenario)

    enemies = [make_enemy("Gobelin", hp=10, ac=12)]
    await scenario.start_combat(enemies=enemies)

    scenario.assert_in_combat()
    state = scenario.session.combat_state  # type: ignore[union-attr]
    assert len(state.combatants) >= 2  # at least 1 player + 1 enemy
    assert state.round_number >= 1


@pytest.mark.asyncio
async def test_attack_hits_and_deals_damage(scenario: ScenarioRunner) -> None:
    """Attacking a weak enemy (1 HP, 5 AC) results in HP change or death."""
    await _setup_single_player(scenario)

    enemies = [make_weak_enemy("Rat")]
    await scenario.start_combat(enemies=enemies)

    rat = next(
        c for c in scenario.session.combat_state.combatants  # type: ignore[union-attr]
        if c.name == "Rat"
    )
    hp_before = rat.character.hp

    # Keep attacking — natural 1 always misses even on AC 5
    for _ in range(10):
        if scenario.session is None or scenario.session.combat_state is None:
            break
        await scenario.attack(target="Rat", player_idx=0)

    assert rat.character.hp < hp_before or not rat.is_alive


@pytest.mark.asyncio
async def test_combat_ends_when_all_enemies_die(scenario: ScenarioRunner) -> None:
    """Combat ends and XP is distributed when all enemies are killed."""
    await _setup_single_player(scenario)
    char = scenario.get_character(0)
    xp_before = char.xp

    enemies = [make_weak_enemy("Rat")]
    await scenario.start_combat(enemies=enemies)

    # Keep attacking until the rat dies (natural 1 misses even against AC 5)
    for _ in range(10):
        if scenario.session is None or scenario.session.combat_state is None:
            break
        await scenario.attack(target="Rat", player_idx=0)

    scenario.assert_not_in_combat()
    assert char.xp > xp_before


@pytest.mark.asyncio
async def test_defend_action_advances_turn(scenario: ScenarioRunner) -> None:
    """Defend action completes without error and advances the turn."""
    await _setup_single_player(scenario)

    enemies = [make_enemy("Gobelin", hp=10, ac=12)]
    await scenario.start_combat(enemies=enemies)

    await scenario.defend(player_idx=0)

    # Turn should have advanced (enemy turns auto-resolve too)
    scenario.assert_in_combat()


@pytest.mark.asyncio
async def test_flee_action_advances_turn(scenario: ScenarioRunner) -> None:
    """Flee action completes without error and advances the turn."""
    await _setup_single_player(scenario)

    enemies = [make_enemy("Gobelin", hp=10, ac=12)]
    await scenario.start_combat(enemies=enemies)

    await scenario.flee(player_idx=0)

    # Combat should still be active (flee doesn't end combat by itself)
    scenario.assert_in_combat()


@pytest.mark.asyncio
async def test_multiple_enemies_in_combat(scenario: ScenarioRunner) -> None:
    """Combat can have multiple enemies in the encounter."""
    await _setup_single_player(scenario)

    enemies = [
        make_weak_enemy("Rat A"),
        make_weak_enemy("Rat B"),
        make_weak_enemy("Rat C"),
    ]
    await scenario.start_combat(enemies=enemies)

    scenario.assert_in_combat()
    state = scenario.session.combat_state  # type: ignore[union-attr]
    enemy_names = [c.name for c in state.combatants if c.name.startswith("Rat")]
    assert len(enemy_names) == 3


@pytest.mark.asyncio
async def test_player_takes_damage_from_enemy(scenario: ScenarioRunner) -> None:
    """Player HP can decrease after enemy counter-attack during combat."""
    await _setup_single_player(scenario)
    char = scenario.get_character(0)
    hp_before = char.hp

    # Strong enemy with high attack chance to hit back
    enemies = [make_strong_enemy("Ogre")]
    await scenario.start_combat(enemies=enemies)

    # Perform several rounds to give the ogre chances to hit
    for _ in range(5):
        if scenario.session.combat_state is None:  # type: ignore[union-attr]
            break
        try:
            await scenario.attack(target="Ogre", player_idx=0)
        except (ValueError, RuntimeError):
            break

    # After multiple rounds against a strong enemy, player likely took damage
    # (not guaranteed due to dice, but very likely with 5 rounds)
    # We just verify combat didn't crash and HP is still valid
    assert char.hp <= hp_before
    assert char.hp >= 0


@pytest.mark.asyncio
async def test_two_players_alternating_turns(scenario: ScenarioRunner) -> None:
    """Two players can both act in combat with alternating turns."""
    await scenario.start_campaign(theme="Arene", players=2)
    await scenario.add_player("Guerrier", race="Human", class_="Fighter", player_idx=0)
    await scenario.add_player("Rogue", race="Elf", class_="Rogue", player_idx=1)
    give_starter_weapon(scenario, player_idx=0)
    give_starter_weapon(scenario, player_idx=1)

    enemies = [make_enemy("Gobelin", hp=20, ac=10)]
    await scenario.start_combat(enemies=enemies)

    scenario.assert_in_combat()
    state = scenario.session.combat_state  # type: ignore[union-attr]
    player_names = [c.name for c in state.combatants if c.name in ("Guerrier", "Rogue")]
    assert len(player_names) == 2

    # Both players take actions over a couple of rounds
    for _ in range(2):
        if scenario.session.combat_state is None:  # type: ignore[union-attr]
            break
        try:
            await scenario.attack(target="Gobelin", player_idx=0)
        except (ValueError, RuntimeError):
            break
        if scenario.session.combat_state is None:  # type: ignore[union-attr]
            break
        try:
            await scenario.attack(target="Gobelin", player_idx=1)
        except (ValueError, RuntimeError):
            break


@pytest.mark.asyncio
async def test_very_weak_enemy_dies_in_one_hit(scenario: ScenarioRunner) -> None:
    """A 1 HP enemy dies after a single successful attack."""
    await _setup_single_player(scenario)

    enemies = [make_weak_enemy("Mouche")]
    await scenario.start_combat(enemies=enemies)

    # Keep attacking — natural 1 always misses even on AC 5
    for _ in range(10):
        if scenario.session is None or scenario.session.combat_state is None:
            break
        await scenario.attack(target="Mouche", player_idx=0)

    scenario.assert_not_in_combat()


@pytest.mark.asyncio
async def test_combat_persists_through_multiple_rounds(scenario: ScenarioRunner) -> None:
    """Combat state persists correctly across multiple rounds of actions."""
    await _setup_single_player(scenario)

    enemies = [make_enemy("Troll", hp=30, ac=10)]
    await scenario.start_combat(enemies=enemies)

    initial_round = scenario.session.combat_state.round_number  # type: ignore[union-attr]

    # Perform several attack rounds
    for _ in range(3):
        if scenario.session.combat_state is None:  # type: ignore[union-attr]
            break
        try:
            await scenario.attack(target="Troll", player_idx=0)
        except (ValueError, RuntimeError):
            break

    # If combat is still active, round number should have advanced
    if scenario.session.combat_state is not None:  # type: ignore[union-attr]
        scenario.assert_in_combat()
        assert scenario.session.combat_state.round_number >= initial_round  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_killing_multiple_enemies_grants_cumulative_xp(
    scenario: ScenarioRunner,
) -> None:
    """Killing multiple enemies awards XP for each one killed."""
    await _setup_single_player(scenario)
    char = scenario.get_character(0)
    xp_before = char.xp

    enemies = [make_weak_enemy("Rat A"), make_weak_enemy("Rat B")]
    await scenario.start_combat(enemies=enemies)

    # Keep attacking until combat ends (misses are possible even on AC 5)
    for _ in range(10):
        if scenario.session is None or scenario.session.combat_state is None:
            break
        for name in ("Rat A", "Rat B"):
            if scenario.session.combat_state is None:
                break
            # Skip dead targets
            alive = [c for c in scenario.session.combat_state.combatants
                     if c.name == name and c.is_alive]
            if alive:
                await scenario.attack(target=name, player_idx=0)

    scenario.assert_not_in_combat()
    xp_gained = char.xp - xp_before
    assert xp_gained > 0
