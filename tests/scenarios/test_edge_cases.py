"""Scenario tests for edge cases: invalid inputs, missing state, boundary conditions."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from engine.combat import is_combat_over
from tests.scenarios.conftest import make_weak_enemy, give_starter_weapon
from tests.scenarios.scenario_runner import ScenarioRunner


@pytest.mark.asyncio
async def test_roll_invalid_expression_returns_ephemeral_error(
    scenario: ScenarioRunner,
) -> None:
    """Rolling with an invalid expression returns an ephemeral error message."""
    await scenario.start_campaign(theme="Edge Cases", players=1)
    result = await scenario.roll("not_a_dice_expr")
    assert result.ephemeral is True
    assert "invalide" in (result.content or "").lower()


@pytest.mark.asyncio
async def test_look_without_location_returns_error(
    scenario: ScenarioRunner,
) -> None:
    """Looking around without a current location returns an ephemeral error."""
    await scenario.start_campaign(theme="Empty World", players=1)
    await scenario.add_player("Scout", race="Human", class_="Fighter", player_idx=0)

    # Session has no current_location set by default in tests
    result = await scenario.look(player_idx=0)
    assert result.ephemeral is True
    assert result.content is not None


@pytest.mark.asyncio
async def test_save_without_active_session_returns_error(
    scenario: ScenarioRunner,
) -> None:
    """Saving without an active session returns an ephemeral error."""
    # No campaign started -- call save directly
    result = await scenario.save()
    assert result.ephemeral is True
    assert "session" in (result.content or "").lower()


@pytest.mark.asyncio
async def test_duplicate_character_raises_integrity_error(
    scenario: ScenarioRunner,
) -> None:
    """Adding a second character for the same player raises IntegrityError."""
    await scenario.start_campaign(theme="Duplicates", players=1)
    await scenario.add_player("First", race="Human", class_="Fighter", player_idx=0)
    assert scenario.get_character(0).name == "First"

    with pytest.raises(IntegrityError):
        await scenario.add_player("Second", race="Elf", class_="Wizard", player_idx=0)


@pytest.mark.asyncio
async def test_attack_invalid_target_raises_value_error(
    scenario: ScenarioRunner,
) -> None:
    """Attacking a target that does not exist raises ValueError."""
    await scenario.start_campaign(theme="Bad Target", players=1)
    await scenario.add_player("Warrior", race="Human", class_="Fighter", player_idx=0)
    give_starter_weapon(scenario, player_idx=0)

    enemies = [make_weak_enemy("Goblin")]
    await scenario.start_combat(enemies=enemies)

    with pytest.raises(ValueError, match="not found or dead"):
        await scenario.attack(target="Ghost", player_idx=0)


@pytest.mark.asyncio
async def test_roll_works_after_campaign_start(
    scenario: ScenarioRunner,
) -> None:
    """Rolls work anytime after campaign start, no character needed."""
    await scenario.start_campaign(theme="Dice Only", players=1)
    # No character added -- roll should still work
    result = await scenario.roll("1d20")
    assert result.content is not None
    assert "1d20" in result.content
    assert result.ephemeral is False


@pytest.mark.asyncio
async def test_start_combat_with_empty_enemies_is_immediately_over(
    scenario: ScenarioRunner,
) -> None:
    """Starting combat with no enemies means combat is immediately over."""
    await scenario.start_campaign(theme="Empty Arena", players=1)
    await scenario.add_player("Lonely", race="Human", class_="Fighter", player_idx=0)

    await scenario.start_combat(enemies=[])

    # Combat state was created but is_combat_over returns True (no enemies alive)
    assert scenario.session is not None
    state = scenario.session.combat_state
    assert state is not None
    assert is_combat_over(state) is True


@pytest.mark.asyncio
async def test_multiple_roll_expressions(
    scenario: ScenarioRunner,
) -> None:
    """Various valid dice expressions all produce results."""
    await scenario.start_campaign(theme="Roll Party", players=1)

    expressions = ["1d20", "4d6", "1d8+5"]
    for expr in expressions:
        result = await scenario.roll(expr)
        assert result.content is not None, f"No content for expression '{expr}'"
        assert expr in result.content, f"Expression '{expr}' not in result: {result.content}"
        assert result.ephemeral is False, f"Roll '{expr}' should not be ephemeral"
