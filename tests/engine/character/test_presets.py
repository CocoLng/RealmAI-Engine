"""Tests for class-optimized stat presets."""

from engine.character import CharacterClass, Ability
from engine.character.presets import CLASS_STAT_PRESETS, get_class_preset


def test_all_classes_have_preset():
    for char_class in CharacterClass:
        assert char_class in CLASS_STAT_PRESETS, f"Missing preset for {char_class}"


def test_each_preset_uses_standard_array():
    standard = sorted([15, 14, 13, 12, 10, 8])
    for char_class, preset in CLASS_STAT_PRESETS.items():
        values = sorted(preset.values())
        assert values == standard, f"{char_class} preset is not Standard Array: {values}"


def test_each_preset_assigns_all_six_abilities():
    for char_class, preset in CLASS_STAT_PRESETS.items():
        assert set(preset.keys()) == set(Ability), f"{char_class} missing abilities"


def test_get_class_preset_returns_copy():
    p1 = get_class_preset(CharacterClass.FIGHTER)
    p2 = get_class_preset(CharacterClass.FIGHTER)
    p1[Ability.STR] = 20
    assert p2[Ability.STR] == 15  # original untouched


def test_fighter_prioritizes_str():
    preset = get_class_preset(CharacterClass.FIGHTER)
    assert preset[Ability.STR] == 15


def test_wizard_prioritizes_int():
    preset = get_class_preset(CharacterClass.WIZARD)
    assert preset[Ability.INT] == 15


def test_cleric_prioritizes_wis():
    preset = get_class_preset(CharacterClass.CLERIC)
    assert preset[Ability.WIS] == 15


def test_rogue_prioritizes_dex():
    preset = get_class_preset(CharacterClass.ROGUE)
    assert preset[Ability.DEX] == 15


def test_ranger_prioritizes_dex_then_wis():
    preset = get_class_preset(CharacterClass.RANGER)
    assert preset[Ability.DEX] == 15
    assert preset[Ability.WIS] == 14


def test_barbarian_prioritizes_str_then_con():
    preset = get_class_preset(CharacterClass.BARBARIAN)
    assert preset[Ability.STR] == 15
    assert preset[Ability.CON] == 14
