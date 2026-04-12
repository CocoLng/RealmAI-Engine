"""Tests for StatAssignmentView — pure logic, no Discord needed."""

from __future__ import annotations


from bot.views.stat_assignment_view import (
    CLASS_PRIMARY_STATS,
    StatAssignmentView,
    get_remaining_values,
)
from engine.character import STANDARD_ARRAY, Ability, CharacterClass


# ---------------------------------------------------------------------------
# get_remaining_values
# ---------------------------------------------------------------------------


class TestGetRemainingValues:
    def test_empty_assignments(self) -> None:
        remaining = get_remaining_values({})
        assert sorted(remaining) == sorted(STANDARD_ARRAY)

    def test_one_assigned(self) -> None:
        remaining = get_remaining_values({Ability.STR: 15})
        assert sorted(remaining) == [8, 10, 12, 13, 14]

    def test_all_assigned(self) -> None:
        assignments = {
            Ability.STR: 15,
            Ability.DEX: 14,
            Ability.CON: 13,
            Ability.INT: 12,
            Ability.WIS: 10,
            Ability.CHA: 8,
        }
        assert get_remaining_values(assignments) == []

    def test_partial_assignments(self) -> None:
        assignments = {Ability.STR: 15, Ability.DEX: 8}
        remaining = get_remaining_values(assignments)
        assert sorted(remaining) == [10, 12, 13, 14]


# ---------------------------------------------------------------------------
# StatAssignmentView state tracking
# ---------------------------------------------------------------------------


class TestStatAssignmentViewState:
    def test_initial_state(self) -> None:
        view = StatAssignmentView(
            char_class=CharacterClass.FIGHTER,
            on_confirmed=_dummy_callback,
        )
        assert view.assignments == {}
        assert view.current_stat == Ability.STR
        assert not view.all_assigned

    def test_assigning_removes_from_remaining(self) -> None:
        view = StatAssignmentView(
            char_class=CharacterClass.WIZARD,
            on_confirmed=_dummy_callback,
        )
        view.assignments[Ability.STR] = 15
        view._current_stat_index = 1
        remaining = get_remaining_values(view.assignments)
        assert 15 not in remaining
        assert view.current_stat == Ability.DEX

    def test_all_assigned_when_six(self) -> None:
        view = StatAssignmentView(
            char_class=CharacterClass.ROGUE,
            on_confirmed=_dummy_callback,
        )
        for i, ability in enumerate(Ability):
            view.assignments[ability] = list(STANDARD_ARRAY)[i]
        view._current_stat_index = 6
        assert view.all_assigned
        assert view.current_stat is None

    def test_reset_clears_all(self) -> None:
        view = StatAssignmentView(
            char_class=CharacterClass.CLERIC,
            on_confirmed=_dummy_callback,
        )
        view.assignments[Ability.STR] = 15
        view.assignments[Ability.DEX] = 14
        view._current_stat_index = 2

        # Simulate reset
        view.assignments.clear()
        view._current_stat_index = 0

        assert view.assignments == {}
        assert view.current_stat == Ability.STR
        assert not view.all_assigned


# ---------------------------------------------------------------------------
# Class hints mapping
# ---------------------------------------------------------------------------


class TestClassHints:
    def test_fighter_hints(self) -> None:
        assert CLASS_PRIMARY_STATS[CharacterClass.FIGHTER] == [Ability.STR, Ability.CON]

    def test_wizard_hints(self) -> None:
        assert CLASS_PRIMARY_STATS[CharacterClass.WIZARD] == [Ability.INT]

    def test_rogue_hints(self) -> None:
        assert CLASS_PRIMARY_STATS[CharacterClass.ROGUE] == [Ability.DEX]

    def test_cleric_hints(self) -> None:
        assert CLASS_PRIMARY_STATS[CharacterClass.CLERIC] == [Ability.WIS]

    def test_ranger_hints(self) -> None:
        assert CLASS_PRIMARY_STATS[CharacterClass.RANGER] == [Ability.DEX, Ability.WIS]

    def test_barbarian_hints(self) -> None:
        assert CLASS_PRIMARY_STATS[CharacterClass.BARBARIAN] == [Ability.STR, Ability.CON]

    def test_all_classes_covered(self) -> None:
        for cls in CharacterClass:
            assert cls in CLASS_PRIMARY_STATS


# ---------------------------------------------------------------------------
# Status text
# ---------------------------------------------------------------------------


class TestStatusText:
    def test_status_shows_assignments(self) -> None:
        view = StatAssignmentView(
            char_class=CharacterClass.FIGHTER,
            on_confirmed=_dummy_callback,
        )
        view.assignments[Ability.STR] = 15
        view._current_stat_index = 1
        text = view.get_status_text()
        assert "15" in text
        assert "Force" in text

    def test_status_shows_remaining(self) -> None:
        view = StatAssignmentView(
            char_class=CharacterClass.WIZARD,
            on_confirmed=_dummy_callback,
        )
        text = view.get_status_text()
        # All values should be in remaining
        assert "15" in text
        assert "8" in text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _dummy_callback(
    interaction: object, assignments: dict[Ability, int],
) -> None:
    """No-op callback for testing."""
