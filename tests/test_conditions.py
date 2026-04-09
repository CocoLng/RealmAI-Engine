"""Tests for engine/conditions.py."""

import pytest
from pydantic import ValidationError

from engine.character import Ability
from engine.conditions import (
    ActiveCondition,
    ConditionType,
    apply_condition,
    auto_fails_str_dex_saves,
    cannot_move,
    get_condition,
    get_exhaustion_level,
    grants_advantage_to_attackers,
    has_condition,
    has_disadvantage_on_attacks,
    is_incapacitated,
    remove_condition,
    tick_durations,
)


# ---------------------------------------------------------------------------
# ConditionType enum
# ---------------------------------------------------------------------------


class TestConditionType:
    def test_all_conditions_exist(self) -> None:
        assert len(ConditionType) == 15

    def test_values_are_human_readable(self) -> None:
        assert ConditionType.BLINDED == "Blinded"
        assert ConditionType.UNCONSCIOUS == "Unconscious"


# ---------------------------------------------------------------------------
# ActiveCondition model
# ---------------------------------------------------------------------------


class TestActiveCondition:
    def test_creation_basic(self) -> None:
        c = ActiveCondition(condition_type=ConditionType.POISONED)
        assert c.condition_type == ConditionType.POISONED
        assert c.source == ""
        assert c.duration_rounds is None
        assert c.save_ability is None
        assert c.save_dc is None
        assert c.exhaustion_level == 0

    def test_creation_full(self) -> None:
        c = ActiveCondition(
            condition_type=ConditionType.FRIGHTENED,
            source="Dragon Fear",
            duration_rounds=3,
            save_ability=Ability.WIS,
            save_dc=15,
        )
        assert c.source == "Dragon Fear"
        assert c.duration_rounds == 3
        assert c.save_ability == Ability.WIS
        assert c.save_dc == 15

    def test_exhaustion_level_bounds(self) -> None:
        c = ActiveCondition(
            condition_type=ConditionType.EXHAUSTION, exhaustion_level=6
        )
        assert c.exhaustion_level == 6

        with pytest.raises(ValidationError):
            ActiveCondition(
                condition_type=ConditionType.EXHAUSTION, exhaustion_level=7
            )

        with pytest.raises(ValidationError):
            ActiveCondition(
                condition_type=ConditionType.EXHAUSTION, exhaustion_level=-1
            )

    def test_duration_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ActiveCondition(
                condition_type=ConditionType.BLINDED, duration_rounds=0
            )

    def test_roundtrip_serialization(self) -> None:
        c = ActiveCondition(
            condition_type=ConditionType.PARALYZED,
            source="Hold Person",
            duration_rounds=10,
            save_ability=Ability.WIS,
            save_dc=14,
        )
        data = c.model_dump()
        c2 = ActiveCondition.model_validate(data)
        assert c == c2


# ---------------------------------------------------------------------------
# apply_condition
# ---------------------------------------------------------------------------


class TestApplyCondition:
    def test_add_new_condition(self) -> None:
        conditions: list[ActiveCondition] = []
        apply_condition(
            conditions, ActiveCondition(condition_type=ConditionType.BLINDED)
        )
        assert len(conditions) == 1
        assert conditions[0].condition_type == ConditionType.BLINDED

    def test_replace_same_type(self) -> None:
        conditions = [
            ActiveCondition(
                condition_type=ConditionType.POISONED, source="Poison Spray"
            )
        ]
        apply_condition(
            conditions,
            ActiveCondition(
                condition_type=ConditionType.POISONED, source="Green Dragon Breath"
            ),
        )
        assert len(conditions) == 1
        assert conditions[0].source == "Green Dragon Breath"

    def test_exhaustion_stacks(self) -> None:
        conditions = [
            ActiveCondition(
                condition_type=ConditionType.EXHAUSTION, exhaustion_level=2
            )
        ]
        apply_condition(
            conditions,
            ActiveCondition(condition_type=ConditionType.EXHAUSTION),
        )
        assert len(conditions) == 1
        assert conditions[0].exhaustion_level == 3

    def test_exhaustion_caps_at_6(self) -> None:
        conditions = [
            ActiveCondition(
                condition_type=ConditionType.EXHAUSTION, exhaustion_level=6
            )
        ]
        apply_condition(
            conditions,
            ActiveCondition(condition_type=ConditionType.EXHAUSTION),
        )
        assert len(conditions) == 1
        assert conditions[0].exhaustion_level == 6

    def test_returns_same_list(self) -> None:
        conditions: list[ActiveCondition] = []
        result = apply_condition(
            conditions, ActiveCondition(condition_type=ConditionType.PRONE)
        )
        assert result is conditions

    def test_multiple_different_types(self) -> None:
        conditions: list[ActiveCondition] = []
        apply_condition(
            conditions, ActiveCondition(condition_type=ConditionType.BLINDED)
        )
        apply_condition(
            conditions, ActiveCondition(condition_type=ConditionType.POISONED)
        )
        assert len(conditions) == 2


# ---------------------------------------------------------------------------
# remove_condition
# ---------------------------------------------------------------------------


class TestRemoveCondition:
    def test_remove_existing(self) -> None:
        conditions = [
            ActiveCondition(condition_type=ConditionType.BLINDED),
            ActiveCondition(condition_type=ConditionType.POISONED),
        ]
        remove_condition(conditions, ConditionType.BLINDED)
        assert len(conditions) == 1
        assert conditions[0].condition_type == ConditionType.POISONED

    def test_remove_not_found_is_noop(self) -> None:
        conditions = [ActiveCondition(condition_type=ConditionType.BLINDED)]
        result = remove_condition(conditions, ConditionType.PRONE)
        assert result is conditions
        assert len(result) == 1
        assert result[0].condition_type == ConditionType.BLINDED

    def test_returns_same_list(self) -> None:
        conditions = [ActiveCondition(condition_type=ConditionType.STUNNED)]
        result = remove_condition(conditions, ConditionType.STUNNED)
        assert result is conditions


# ---------------------------------------------------------------------------
# has_condition
# ---------------------------------------------------------------------------


class TestHasCondition:
    def test_has_true(self) -> None:
        conditions = [ActiveCondition(condition_type=ConditionType.CHARMED)]
        assert has_condition(conditions, ConditionType.CHARMED) is True

    def test_has_false(self) -> None:
        conditions = [ActiveCondition(condition_type=ConditionType.CHARMED)]
        assert has_condition(conditions, ConditionType.BLINDED) is False

    def test_empty_list(self) -> None:
        assert has_condition([], ConditionType.PRONE) is False


# ---------------------------------------------------------------------------
# get_condition
# ---------------------------------------------------------------------------


class TestGetCondition:
    def test_found(self) -> None:
        c = ActiveCondition(
            condition_type=ConditionType.FRIGHTENED, source="Wraith"
        )
        conditions = [c]
        assert get_condition(conditions, ConditionType.FRIGHTENED) is c

    def test_not_found_returns_none(self) -> None:
        conditions = [ActiveCondition(condition_type=ConditionType.BLINDED)]
        assert get_condition(conditions, ConditionType.PRONE) is None


# ---------------------------------------------------------------------------
# tick_durations
# ---------------------------------------------------------------------------


class TestTickDurations:
    def test_decrement(self) -> None:
        conditions = [
            ActiveCondition(
                condition_type=ConditionType.BLINDED, duration_rounds=3
            )
        ]
        tick_durations(conditions)
        assert conditions[0].duration_rounds == 2

    def test_remove_expired(self) -> None:
        conditions = [
            ActiveCondition(
                condition_type=ConditionType.BLINDED, duration_rounds=1
            )
        ]
        tick_durations(conditions)
        assert len(conditions) == 0

    def test_indefinite_untouched(self) -> None:
        conditions = [
            ActiveCondition(condition_type=ConditionType.PETRIFIED)
        ]
        tick_durations(conditions)
        assert len(conditions) == 1
        assert conditions[0].duration_rounds is None

    def test_mixed(self) -> None:
        conditions = [
            ActiveCondition(
                condition_type=ConditionType.BLINDED, duration_rounds=1
            ),
            ActiveCondition(condition_type=ConditionType.POISONED),
            ActiveCondition(
                condition_type=ConditionType.STUNNED, duration_rounds=3
            ),
        ]
        tick_durations(conditions)
        assert len(conditions) == 2
        assert conditions[0].condition_type == ConditionType.POISONED
        assert conditions[1].condition_type == ConditionType.STUNNED
        assert conditions[1].duration_rounds == 2

    def test_returns_same_list(self) -> None:
        conditions: list[ActiveCondition] = [
            ActiveCondition(
                condition_type=ConditionType.PRONE, duration_rounds=5
            )
        ]
        result = tick_durations(conditions)
        assert result is conditions


# ---------------------------------------------------------------------------
# has_disadvantage_on_attacks
# ---------------------------------------------------------------------------


class TestDisadvantageOnAttacks:
    @pytest.mark.parametrize(
        "condition_type",
        [
            ConditionType.BLINDED,
            ConditionType.FRIGHTENED,
            ConditionType.POISONED,
            ConditionType.PRONE,
            ConditionType.RESTRAINED,
        ],
    )
    def test_conditions_that_impose(self, condition_type: ConditionType) -> None:
        conditions = [ActiveCondition(condition_type=condition_type)]
        assert has_disadvantage_on_attacks(conditions) is True

    def test_empty_list(self) -> None:
        assert has_disadvantage_on_attacks([]) is False

    def test_non_disadvantage_condition(self) -> None:
        conditions = [ActiveCondition(condition_type=ConditionType.CHARMED)]
        assert has_disadvantage_on_attacks(conditions) is False


# ---------------------------------------------------------------------------
# grants_advantage_to_attackers
# ---------------------------------------------------------------------------


class TestAdvantageToAttackers:
    @pytest.mark.parametrize(
        "condition_type",
        [
            ConditionType.BLINDED,
            ConditionType.PARALYZED,
            ConditionType.PETRIFIED,
            ConditionType.PRONE,
            ConditionType.RESTRAINED,
            ConditionType.STUNNED,
            ConditionType.UNCONSCIOUS,
        ],
    )
    def test_conditions_that_grant(self, condition_type: ConditionType) -> None:
        conditions = [ActiveCondition(condition_type=condition_type)]
        assert grants_advantage_to_attackers(conditions) is True

    def test_empty_list(self) -> None:
        assert grants_advantage_to_attackers([]) is False

    def test_non_advantage_condition(self) -> None:
        conditions = [ActiveCondition(condition_type=ConditionType.DEAFENED)]
        assert grants_advantage_to_attackers(conditions) is False


# ---------------------------------------------------------------------------
# is_incapacitated
# ---------------------------------------------------------------------------


class TestIsIncapacitated:
    @pytest.mark.parametrize(
        "condition_type",
        [
            ConditionType.INCAPACITATED,
            ConditionType.PARALYZED,
            ConditionType.PETRIFIED,
            ConditionType.STUNNED,
            ConditionType.UNCONSCIOUS,
        ],
    )
    def test_conditions_that_incapacitate(
        self, condition_type: ConditionType
    ) -> None:
        conditions = [ActiveCondition(condition_type=condition_type)]
        assert is_incapacitated(conditions) is True

    def test_non_incapacitating(self) -> None:
        conditions = [ActiveCondition(condition_type=ConditionType.POISONED)]
        assert is_incapacitated(conditions) is False


# ---------------------------------------------------------------------------
# cannot_move
# ---------------------------------------------------------------------------


class TestCannotMove:
    @pytest.mark.parametrize(
        "condition_type",
        [
            ConditionType.GRAPPLED,
            ConditionType.PARALYZED,
            ConditionType.PETRIFIED,
            ConditionType.RESTRAINED,
            ConditionType.STUNNED,
            ConditionType.UNCONSCIOUS,
        ],
    )
    def test_conditions_that_prevent(
        self, condition_type: ConditionType
    ) -> None:
        conditions = [ActiveCondition(condition_type=condition_type)]
        assert cannot_move(conditions) is True

    def test_non_preventing(self) -> None:
        conditions = [ActiveCondition(condition_type=ConditionType.BLINDED)]
        assert cannot_move(conditions) is False


# ---------------------------------------------------------------------------
# auto_fails_str_dex_saves
# ---------------------------------------------------------------------------


class TestAutoFailSaves:
    @pytest.mark.parametrize(
        "condition_type",
        [
            ConditionType.PARALYZED,
            ConditionType.PETRIFIED,
            ConditionType.STUNNED,
            ConditionType.UNCONSCIOUS,
        ],
    )
    def test_conditions_that_auto_fail(
        self, condition_type: ConditionType
    ) -> None:
        conditions = [ActiveCondition(condition_type=condition_type)]
        assert auto_fails_str_dex_saves(conditions) is True

    def test_non_auto_fail(self) -> None:
        conditions = [ActiveCondition(condition_type=ConditionType.RESTRAINED)]
        assert auto_fails_str_dex_saves(conditions) is False


# ---------------------------------------------------------------------------
# get_exhaustion_level
# ---------------------------------------------------------------------------


class TestExhaustionLevel:
    def test_no_exhaustion(self) -> None:
        conditions = [ActiveCondition(condition_type=ConditionType.BLINDED)]
        assert get_exhaustion_level(conditions) == 0

    def test_empty_list(self) -> None:
        assert get_exhaustion_level([]) == 0

    def test_level_3(self) -> None:
        conditions = [
            ActiveCondition(
                condition_type=ConditionType.EXHAUSTION, exhaustion_level=3
            )
        ]
        assert get_exhaustion_level(conditions) == 3
