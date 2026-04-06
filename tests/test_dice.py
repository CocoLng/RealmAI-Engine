"""Tests for engine/dice.py — dice expression parsing and rolling."""

import pytest

from engine.dice import (
    D20CheckResult,
    DiceResult,
    RollOutcome,
    _compute_outcome,
    roll,
    roll_check,
)


class TestDiceResult:
    """DiceResult model validation."""

    def test_dice_result_fields(self) -> None:
        result = DiceResult(
            expression="2d6+3",
            rolls=[4, 2],
            modifier=3,
            total=9,
        )
        assert result.expression == "2d6+3"
        assert result.rolls == [4, 2]
        assert result.modifier == 3
        assert result.total == 9

    def test_dice_result_immutable_via_dump(self) -> None:
        result = DiceResult(expression="1d6", rolls=[3], modifier=0, total=3)
        data = result.model_dump()
        assert data == {
            "expression": "1d6",
            "rolls": [3],
            "modifier": 0,
            "total": 3,
        }


class TestRollBasic:
    """Basic dice rolling."""

    def test_single_die(self) -> None:
        result = roll("1d6")
        assert result.expression == "1d6"
        assert len(result.rolls) == 1
        assert 1 <= result.rolls[0] <= 6
        assert result.modifier == 0
        assert result.total == result.rolls[0]

    def test_multiple_dice(self) -> None:
        result = roll("3d8")
        assert len(result.rolls) == 3
        assert all(1 <= r <= 8 for r in result.rolls)
        assert result.modifier == 0
        assert result.total == sum(result.rolls)

    def test_d20(self) -> None:
        result = roll("1d20")
        assert 1 <= result.total <= 20


class TestRollWithModifier:
    """Dice expressions with + or - modifiers."""

    def test_positive_modifier(self) -> None:
        result = roll("1d6+3")
        assert result.modifier == 3
        assert result.total == result.rolls[0] + 3

    def test_negative_modifier(self) -> None:
        result = roll("1d6-2")
        assert result.modifier == -2
        assert result.total == result.rolls[0] - 2

    def test_zero_modifier_explicit(self) -> None:
        result = roll("1d6+0")
        assert result.modifier == 0

    def test_large_modifier(self) -> None:
        result = roll("1d20+10")
        assert result.modifier == 10
        assert 11 <= result.total <= 30


class TestRollRanges:
    """Statistical range validation over many rolls."""

    @pytest.mark.parametrize(
        "expression,min_val,max_val",
        [
            ("1d6", 1, 6),
            ("2d6", 2, 12),
            ("1d20+5", 6, 25),
            ("3d8+2", 5, 26),
            ("1d4-1", 0, 3),
            ("4d6", 4, 24),
        ],
    )
    def test_roll_stays_in_range(
        self, expression: str, min_val: int, max_val: int
    ) -> None:
        for _ in range(100):
            result = roll(expression)
            assert min_val <= result.total <= max_val, (
                f"{expression} produced {result.total}, expected {min_val}-{max_val}"
            )


class TestRollEdgeCases:
    """Edge cases and error handling."""

    def test_single_d1(self) -> None:
        """A 1-sided die always returns 1."""
        result = roll("1d1")
        assert result.total == 1

    def test_many_dice(self) -> None:
        result = roll("10d6")
        assert len(result.rolls) == 10
        assert 10 <= result.total <= 60

    def test_whitespace_stripped(self) -> None:
        result = roll(" 2d6 + 3 ")
        assert result.expression == "2d6+3"
        assert result.modifier == 3

    def test_invalid_expression_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid dice expression"):
            roll("not_dice")

    def test_zero_dice_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid dice expression"):
            roll("0d6")

    def test_zero_sides_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid dice expression"):
            roll("1d0")

    def test_negative_dice_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid dice expression"):
            roll("-1d6")


# ---------------------------------------------------------------------------
# D20 check outcome tests
# ---------------------------------------------------------------------------


class TestComputeOutcome:
    """Unit tests for _compute_outcome()."""

    @pytest.mark.parametrize(
        "natural_roll,margin,expected",
        [
            # Nat 1 always CRITICAL_FAILURE, regardless of margin
            (1, 10, RollOutcome.CRITICAL_FAILURE),
            (1, 0, RollOutcome.CRITICAL_FAILURE),
            (1, -10, RollOutcome.CRITICAL_FAILURE),
            # Nat 20 always CRITICAL_SUCCESS, regardless of margin
            (20, -10, RollOutcome.CRITICAL_SUCCESS),
            (20, 0, RollOutcome.CRITICAL_SUCCESS),
            (20, 10, RollOutcome.CRITICAL_SUCCESS),
            # FAILURE: margin <= -5
            (5, -5, RollOutcome.FAILURE),
            (5, -10, RollOutcome.FAILURE),
            # NEAR_FAILURE: margin -4 to -1
            (5, -4, RollOutcome.NEAR_FAILURE),
            (5, -1, RollOutcome.NEAR_FAILURE),
            # NEAR_SUCCESS: margin 0 to 4
            (10, 0, RollOutcome.NEAR_SUCCESS),
            (10, 4, RollOutcome.NEAR_SUCCESS),
            # SUCCESS: margin >= 5
            (15, 5, RollOutcome.SUCCESS),
            (15, 10, RollOutcome.SUCCESS),
        ],
    )
    def test_outcome_tiers(
        self, natural_roll: int, margin: int, expected: RollOutcome
    ) -> None:
        assert _compute_outcome(natural_roll, margin) == expected


class TestD20CheckResult:
    """D20CheckResult model validation."""

    def test_model_fields(self) -> None:
        result = D20CheckResult(
            expression="1d20+3",
            rolls=[15],
            modifier=3,
            total=18,
            dc=15,
            outcome=RollOutcome.NEAR_SUCCESS,
            margin=3,
        )
        assert result.dc == 15
        assert result.outcome == RollOutcome.NEAR_SUCCESS
        assert result.margin == 3

    def test_is_instance_of_dice_result(self) -> None:
        """Liskov: D20CheckResult is a valid DiceResult."""
        result = D20CheckResult(
            expression="1d20",
            rolls=[10],
            modifier=0,
            total=10,
            dc=10,
            outcome=RollOutcome.NEAR_SUCCESS,
            margin=0,
        )
        assert isinstance(result, DiceResult)

    def test_model_dump_includes_all_fields(self) -> None:
        result = D20CheckResult(
            expression="1d20+2",
            rolls=[14],
            modifier=2,
            total=16,
            dc=12,
            outcome=RollOutcome.NEAR_SUCCESS,
            margin=4,
        )
        data = result.model_dump()
        assert data == {
            "expression": "1d20+2",
            "rolls": [14],
            "modifier": 2,
            "total": 16,
            "dc": 12,
            "outcome": "near_success",
            "margin": 4,
        }


class TestRollCheck:
    """Integration tests for roll_check()."""

    def test_returns_d20_check_result(self) -> None:
        result = roll_check("1d20", dc=15)
        assert isinstance(result, D20CheckResult)
        assert isinstance(result, DiceResult)
        assert result.dc == 15
        assert result.margin == result.total - 15

    def test_outcome_matches_roll(self) -> None:
        """Run many checks and verify outcome consistency."""
        for _ in range(200):
            result = roll_check("1d20", dc=10)
            natural = result.rolls[0]
            if natural == 1:
                assert result.outcome == RollOutcome.CRITICAL_FAILURE
            elif natural == 20:
                assert result.outcome == RollOutcome.CRITICAL_SUCCESS
            elif result.margin <= -5:
                assert result.outcome == RollOutcome.FAILURE
            elif result.margin < 0:
                assert result.outcome == RollOutcome.NEAR_FAILURE
            elif result.margin < 5:
                assert result.outcome == RollOutcome.NEAR_SUCCESS
            else:
                assert result.outcome == RollOutcome.SUCCESS

    def test_with_modifier(self) -> None:
        result = roll_check("1d20+5", dc=15)
        assert result.modifier == 5
        assert result.total == result.rolls[0] + 5
        assert result.margin == result.total - 15
