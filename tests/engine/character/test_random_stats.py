"""Tests for 4d6-drop-lowest stat generation and auto-assignment."""

import random
import pytest

from engine.character import Ability, CharacterClass
from engine.character.random_stats import (
    CLASS_STAT_PRIORITY,
    auto_assign_random,
    roll_4d6_drop_lowest,
)


def test_roll_returns_six_ints():
    random.seed(42)
    rolls = roll_4d6_drop_lowest()
    assert len(rolls) == 6
    assert all(isinstance(r, int) for r in rolls)


def test_roll_each_in_range_3_18():
    random.seed(0)
    for _ in range(50):
        rolls = roll_4d6_drop_lowest()
        for r in rolls:
            assert 3 <= r <= 18, f"Roll {r} out of [3, 18]"


def test_roll_sorted_descending():
    random.seed(123)
    rolls = roll_4d6_drop_lowest()
    assert rolls == sorted(rolls, reverse=True)


def test_all_classes_have_priority():
    for char_class in CharacterClass:
        assert char_class in CLASS_STAT_PRIORITY


def test_priority_lists_have_six_distinct_abilities():
    for char_class, prio in CLASS_STAT_PRIORITY.items():
        assert len(prio) == 6
        assert set(prio) == set(Ability)


def test_auto_assign_maps_highest_to_priority_first():
    rolls = [18, 17, 16, 15, 14, 13]  # already sorted desc
    assignment = auto_assign_random(CharacterClass.FIGHTER, rolls)
    assert assignment[Ability.STR] == 18
    assert assignment[Ability.CON] == 17
    assert assignment[Ability.DEX] == 16
    assert assignment[Ability.WIS] == 15
    assert assignment[Ability.INT] == 14
    assert assignment[Ability.CHA] == 13


def test_auto_assign_wizard_priority():
    rolls = [18, 17, 16, 15, 14, 13]
    assignment = auto_assign_random(CharacterClass.WIZARD, rolls)
    assert assignment[Ability.INT] == 18  # wizard top stat
    assert assignment[Ability.STR] == 13  # wizard dump stat


def test_auto_assign_requires_six_rolls():
    with pytest.raises(ValueError):
        auto_assign_random(CharacterClass.FIGHTER, [15, 14, 13])  # only 3
