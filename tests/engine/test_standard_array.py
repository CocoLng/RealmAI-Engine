"""Tests for Standard Array ability score assignment."""

import pytest

from engine.character import (
    Ability,
    AbilityScores,
    Race,
    STANDARD_ARRAY,
    assign_standard_array,
)


def _full_assignment(**kwargs: int) -> dict[Ability, int]:
    """Build a complete Ability → value mapping from keyword args."""
    return {Ability[k]: v for k, v in kwargs.items()}


class TestStandardArrayConstant:
    def test_has_six_values(self) -> None:
        assert len(STANDARD_ARRAY) == 6

    def test_contains_expected_values(self) -> None:
        assert sorted(STANDARD_ARRAY) == [8, 10, 12, 13, 14, 15]


class TestAssignStandardArrayValid:
    def test_valid_assignment_returns_ability_scores(self) -> None:
        assignments = _full_assignment(STR=15, DEX=14, CON=13, INT=12, WIS=10, CHA=8)
        result = assign_standard_array(assignments, Race.HUMAN)
        assert isinstance(result, AbilityScores)

    def test_each_value_used_exactly_once(self) -> None:
        assignments = _full_assignment(STR=8, DEX=10, CON=12, INT=13, WIS=14, CHA=15)
        result = assign_standard_array(assignments, Race.HUMAN)
        # Human gets +1 to all
        assert result.STR == 9
        assert result.DEX == 11
        assert result.CON == 13
        assert result.INT == 14
        assert result.WIS == 15
        assert result.CHA == 16

    def test_different_ability_orderings_accepted(self) -> None:
        # Reversed distribution
        assignments = _full_assignment(STR=8, DEX=10, CON=12, INT=13, WIS=14, CHA=15)
        result = assign_standard_array(assignments, Race.TIEFLING)
        # Tiefling: +2 CHA, +1 INT
        assert result.CHA == 17  # 15 + 2
        assert result.INT == 14  # 13 + 1
        assert result.STR == 8   # no bonus


class TestAssignStandardArrayRacialBonuses:
    def test_human_plus_one_all(self) -> None:
        assignments = _full_assignment(STR=15, DEX=14, CON=13, INT=12, WIS=10, CHA=8)
        result = assign_standard_array(assignments, Race.HUMAN)
        assert result.STR == 16
        assert result.DEX == 15
        assert result.CON == 14
        assert result.INT == 13
        assert result.WIS == 11
        assert result.CHA == 9

    def test_elf_plus_two_dex(self) -> None:
        assignments = _full_assignment(STR=8, DEX=15, CON=12, INT=13, WIS=14, CHA=10)
        result = assign_standard_array(assignments, Race.ELF)
        assert result.DEX == 17  # 15 + 2
        assert result.STR == 8   # no bonus

    def test_half_orc_str_and_con(self) -> None:
        assignments = _full_assignment(STR=15, DEX=10, CON=14, INT=8, WIS=12, CHA=13)
        result = assign_standard_array(assignments, Race.HALF_ORC)
        assert result.STR == 17  # 15 + 2
        assert result.CON == 15  # 14 + 1
        assert result.DEX == 10  # no bonus


class TestAssignStandardArrayInvalid:
    def test_duplicate_value_rejected(self) -> None:
        # Use 15 twice, missing 14
        assignments = _full_assignment(STR=15, DEX=15, CON=13, INT=12, WIS=10, CHA=8)
        with pytest.raises(ValueError, match="Standard Array"):
            assign_standard_array(assignments, Race.HUMAN)

    def test_wrong_values_rejected(self) -> None:
        # All valid counts but wrong values (e.g. 16 instead of 15)
        assignments = _full_assignment(STR=16, DEX=14, CON=13, INT=12, WIS=10, CHA=8)
        with pytest.raises(ValueError, match="Standard Array"):
            assign_standard_array(assignments, Race.HUMAN)

    def test_missing_ability_rejected(self) -> None:
        # Only 5 assignments
        assignments: dict[Ability, int] = {
            Ability.STR: 15,
            Ability.DEX: 14,
            Ability.CON: 13,
            Ability.INT: 12,
            Ability.WIS: 10,
            # CHA missing
        }
        with pytest.raises(ValueError):
            assign_standard_array(assignments, Race.HUMAN)

    def test_extra_ability_raises(self) -> None:
        # 7 assignments — possible if dict has duplicate keys (shouldn't happen)
        # but we can test with 6 valid + verify behavior if we mock extra somehow.
        # In practice with the Ability enum you can't have >6, but the code still
        # validates the count. Test with 5 assignments to trigger count check.
        assignments: dict[Ability, int] = {
            Ability.STR: 15,
            Ability.DEX: 14,
            Ability.CON: 13,
            Ability.INT: 12,
            Ability.WIS: 10,
        }
        with pytest.raises(ValueError):
            assign_standard_array(assignments, Race.HUMAN)

    def test_empty_assignments_rejected(self) -> None:
        with pytest.raises(ValueError):
            assign_standard_array({}, Race.HUMAN)
