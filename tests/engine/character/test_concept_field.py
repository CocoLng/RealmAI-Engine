"""Tests for the new concept field on Character."""

from engine.character import Ability, AbilityScores, Character, CharacterClass, Race, Size, create_character


def _basic_scores() -> AbilityScores:
    return AbilityScores(STR=15, DEX=14, CON=13, INT=12, WIS=10, CHA=8)


def test_concept_defaults_to_empty_string():
    char = create_character(
        name="Thorin",
        race=Race.DWARF,
        char_class=CharacterClass.FIGHTER,
        ability_scores=_basic_scores(),
    )
    assert char.concept == ""


def test_concept_accepts_custom_text():
    char = create_character(
        name="Thorin",
        race=Race.DWARF,
        char_class=CharacterClass.FIGHTER,
        ability_scores=_basic_scores(),
        concept="A grizzled veteran seeking redemption",
    )
    assert char.concept == "A grizzled veteran seeking redemption"


def test_concept_max_length_enforced():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Character(
            name="X",
            race=Race.HUMAN,
            char_class=CharacterClass.FIGHTER,
            ability_scores=_basic_scores(),
            hp=10, max_hp=10, ac=10, speed=30,
            proficiency_bonus=2,
            saving_throw_proficiencies=(Ability.STR, Ability.CON),
            hit_die="d10",
            size=Size.MEDIUM,
            concept="x" * 201,  # over max_length=200
        )
