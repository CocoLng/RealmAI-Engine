"""Tests for the enriched create_character() function."""

from engine.character import (
    Ability,
    AbilityScores,
    Alignment,
    CharacterClass,
    Race,
    Skill,
    assign_standard_array,
    create_character,
)
from engine.character.classes import CLASS_FEATURES
from engine.character.races import RACIAL_FEATURES


def _base_scores() -> AbilityScores:
    return AbilityScores(STR=15, DEX=14, CON=13, INT=12, WIS=10, CHA=8)


def _scores_with_race(race: Race) -> AbilityScores:
    return assign_standard_array(
        {
            Ability.STR: 15,
            Ability.DEX: 14,
            Ability.CON: 13,
            Ability.INT: 12,
            Ability.WIS: 10,
            Ability.CHA: 8,
        },
        race,
    )


class TestCreateCharacterFeatures:
    def test_features_includes_racial_and_class(self) -> None:
        scores = _scores_with_race(Race.ELF)
        char = create_character("Legolas", Race.ELF, CharacterClass.RANGER, scores)
        feature_names = {f.name for f in char.features}
        # Racial
        for rf in RACIAL_FEATURES[Race.ELF]:
            assert rf.name in feature_names
        # Class
        for cf in CLASS_FEATURES[CharacterClass.RANGER]:
            assert cf.name in feature_names

    def test_fighter_has_fighting_style_and_second_wind(self) -> None:
        scores = _base_scores()
        char = create_character("Thor", Race.HUMAN, CharacterClass.FIGHTER, scores)
        feature_names = {f.name for f in char.features}
        assert "Fighting Style" in feature_names
        assert "Second Wind" in feature_names

    def test_elf_has_darkvision_keen_senses_fey_ancestry(self) -> None:
        scores = _scores_with_race(Race.ELF)
        char = create_character("Aerin", Race.ELF, CharacterClass.WIZARD, scores)
        feature_names = {f.name for f in char.features}
        assert "Darkvision" in feature_names
        assert "Keen Senses" in feature_names
        assert "Fey Ancestry" in feature_names

    def test_human_has_no_racial_features_but_has_class_features(self) -> None:
        scores = _scores_with_race(Race.HUMAN)
        char = create_character("Arthur", Race.HUMAN, CharacterClass.FIGHTER, scores)
        # Human has no racial features in our SRD subset
        racial = [f for f in char.features if f.source_name == "Human"]
        assert len(racial) == 0
        class_features = [f for f in char.features if f.source_name == "Fighter"]
        assert len(class_features) == 2  # Fighting Style + Second Wind

    def test_features_list_is_combined_in_order(self) -> None:
        """Racial features come before class features."""
        scores = _scores_with_race(Race.ELF)
        char = create_character("Elara", Race.ELF, CharacterClass.ROGUE, scores)
        racial_count = len(RACIAL_FEATURES[Race.ELF])
        class_count = len(CLASS_FEATURES[CharacterClass.ROGUE])
        assert len(char.features) == racial_count + class_count
        # First features are racial
        for i, rf in enumerate(RACIAL_FEATURES[Race.ELF]):
            assert char.features[i].name == rf.name


class TestCreateCharacterSkills:
    def test_no_skills_defaults_to_empty_list(self) -> None:
        scores = _base_scores()
        char = create_character("Anon", Race.HUMAN, CharacterClass.FIGHTER, scores)
        assert char.skill_proficiencies == []

    def test_explicit_skill_list_stored(self) -> None:
        scores = _base_scores()
        skills = [Skill.ATHLETICS, Skill.PERCEPTION]
        char = create_character(
            "Brenda", Race.HUMAN, CharacterClass.FIGHTER, scores,
            skill_proficiencies=skills,
        )
        assert char.skill_proficiencies == skills

    def test_skill_none_and_empty_list_equivalent(self) -> None:
        scores = _base_scores()
        char_none = create_character("A", Race.HUMAN, CharacterClass.FIGHTER, scores)
        char_empty = create_character(
            "B", Race.HUMAN, CharacterClass.FIGHTER, scores,
            skill_proficiencies=[],
        )
        assert char_none.skill_proficiencies == char_empty.skill_proficiencies == []


class TestCreateCharacterDerivedStats:
    def test_hp_computed_correctly(self) -> None:
        # Fighter d10 hit die. Scores already include racial bonuses.
        # CON=13 → mod=+1, max HP = 10 + 1 = 11
        scores = AbilityScores(STR=15, DEX=14, CON=13, INT=12, WIS=10, CHA=8)
        char = create_character("Tank", Race.HUMAN, CharacterClass.FIGHTER, scores)
        assert char.hp == char.max_hp
        assert char.max_hp == 11  # 10 (die max) + 1 (CON mod from CON=13)

    def test_ac_is_ten_plus_dex_mod(self) -> None:
        # Scores already include racial bonuses (DEX=16 passed directly).
        scores = AbilityScores(STR=8, DEX=16, CON=10, INT=12, WIS=10, CHA=8)
        char = create_character("Quick", Race.ELF, CharacterClass.ROGUE, scores)
        # DEX=16, mod=+3, AC=13
        assert char.ac == 13

    def test_speed_from_race(self) -> None:
        scores = _base_scores()
        char_human = create_character("H", Race.HUMAN, CharacterClass.FIGHTER, scores)
        char_dwarf = create_character("D", Race.DWARF, CharacterClass.FIGHTER, scores)
        assert char_human.speed == 30
        assert char_dwarf.speed == 25

    def test_level_one(self) -> None:
        char = create_character(
            "Newbie", Race.HUMAN, CharacterClass.CLERIC, _base_scores()
        )
        assert char.level == 1
        assert char.xp == 0
        assert char.proficiency_bonus == 2

    def test_alignment_default_true_neutral(self) -> None:
        char = create_character("X", Race.HUMAN, CharacterClass.WIZARD, _base_scores())
        assert char.alignment == Alignment.TRUE_NEUTRAL

    def test_alignment_custom(self) -> None:
        char = create_character(
            "Y", Race.TIEFLING, CharacterClass.ROGUE, _base_scores(),
            alignment=Alignment.CHAOTIC_NEUTRAL,
        )
        assert char.alignment == Alignment.CHAOTIC_NEUTRAL


class TestBackwardCompatibility:
    def test_old_signature_still_works(self) -> None:
        """create_character() with 4 positional args (no skill_proficiencies) still works."""
        scores = _base_scores()
        char = create_character("Legacy", Race.HALF_ORC, CharacterClass.BARBARIAN, scores)
        assert char.name == "Legacy"
        assert char.skill_proficiencies == []
        assert len(char.features) > 0  # now populated automatically

    def test_alignment_kwarg_still_works(self) -> None:
        scores = _base_scores()
        char = create_character(
            "LegacyAlign", Race.HUMAN, CharacterClass.CLERIC, scores,
            alignment=Alignment.LAWFUL_GOOD,
        )
        assert char.alignment == Alignment.LAWFUL_GOOD
