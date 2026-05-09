"""Tests for the Skill system — enum, ability mapping, proficiencies, class choices."""

import pytest

from engine.character import (
    Ability,
    AbilityScores,
    Character,
    CharacterClass,
    ClassSkillConfig,
    Race,
    Skill,
    SKILL_ABILITY,
    CLASS_SKILL_CHOICES,
    apply_racial_bonuses,
    compute_modifier,
    compute_skill_modifier,
    create_character,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def base_scores() -> AbilityScores:
    return AbilityScores(STR=10, DEX=14, CON=10, INT=16, WIS=12, CHA=8)


@pytest.fixture()
def sample_rogue(base_scores: AbilityScores) -> Character:
    scores = apply_racial_bonuses(base_scores, Race.HUMAN)
    char = create_character("Shadow", Race.HUMAN, CharacterClass.ROGUE, scores)
    char.skill_proficiencies = [
        Skill.STEALTH,
        Skill.ACROBATICS,
        Skill.DECEPTION,
        Skill.PERCEPTION,
    ]
    return char


# ---------------------------------------------------------------------------
# Skill enum
# ---------------------------------------------------------------------------


class TestSkillEnum:
    """Skill enum has exactly 18 members."""

    def test_skill_count(self) -> None:
        assert len(Skill) == 18

    def test_all_18_skills_present(self) -> None:
        expected = {
            "ATHLETICS",
            "ACROBATICS",
            "SLEIGHT_OF_HAND",
            "STEALTH",
            "ARCANA",
            "HISTORY",
            "INVESTIGATION",
            "NATURE",
            "RELIGION",
            "ANIMAL_HANDLING",
            "INSIGHT",
            "MEDICINE",
            "PERCEPTION",
            "SURVIVAL",
            "DECEPTION",
            "INTIMIDATION",
            "PERFORMANCE",
            "PERSUASION",
        }
        assert set(Skill.__members__.keys()) == expected


# ---------------------------------------------------------------------------
# SKILL_ABILITY mapping
# ---------------------------------------------------------------------------


class TestSkillAbilityMapping:
    """SKILL_ABILITY maps all 18 skills to correct abilities."""

    def test_all_18_skills_mapped(self) -> None:
        assert len(SKILL_ABILITY) == 18
        for skill in Skill:
            assert skill in SKILL_ABILITY

    @pytest.mark.parametrize(
        "skill,expected_ability",
        [
            (Skill.ATHLETICS, Ability.STR),
            (Skill.ACROBATICS, Ability.DEX),
            (Skill.STEALTH, Ability.DEX),
            (Skill.ARCANA, Ability.INT),
            (Skill.PERCEPTION, Ability.WIS),
            (Skill.PERSUASION, Ability.CHA),
        ],
    )
    def test_specific_mappings(self, skill: Skill, expected_ability: Ability) -> None:
        assert SKILL_ABILITY[skill] == expected_ability


# ---------------------------------------------------------------------------
# compute_skill_modifier
# ---------------------------------------------------------------------------


class TestComputeSkillModifier:
    """compute_skill_modifier adds proficiency bonus when proficient."""

    def test_without_proficiency(self, base_scores: AbilityScores) -> None:
        scores = apply_racial_bonuses(base_scores, Race.HUMAN)
        char = create_character("Test", Race.HUMAN, CharacterClass.FIGHTER, scores)
        # DEX 15 (14+1 human) → mod = +2, no proficiency
        mod = compute_skill_modifier(char, Skill.STEALTH)
        expected = compute_modifier(char.ability_scores.get(Ability.DEX))
        assert mod == expected

    def test_with_proficiency(self, sample_rogue: Character) -> None:
        # DEX 15 (14+1 human) → mod +2, proficiency +2 = +4
        mod = compute_skill_modifier(sample_rogue, Skill.STEALTH)
        dex_mod = compute_modifier(sample_rogue.ability_scores.get(Ability.DEX))
        assert mod == dex_mod + sample_rogue.proficiency_bonus

    def test_proficiency_vs_non_proficiency_difference(
        self, sample_rogue: Character
    ) -> None:
        # Stealth is proficient, Sleight of Hand is not (both DEX)
        stealth_mod = compute_skill_modifier(sample_rogue, Skill.STEALTH)
        sleight_mod = compute_skill_modifier(sample_rogue, Skill.SLEIGHT_OF_HAND)
        assert stealth_mod - sleight_mod == sample_rogue.proficiency_bonus

    def test_different_ability_skills(self, sample_rogue: Character) -> None:
        # INT skill (not proficient): INT 17 → mod +3
        arcana_mod = compute_skill_modifier(sample_rogue, Skill.ARCANA)
        int_mod = compute_modifier(sample_rogue.ability_scores.get(Ability.INT))
        assert arcana_mod == int_mod

    def test_expertise_doubles_proficiency_bonus(
        self, sample_rogue: Character,
    ) -> None:
        # Rogue Expertise: pick 2 skills → double proficiency on those.
        # Stealth was already proficient (+2 prof). With Expertise it
        # should become +4 (proficiency × 2). DEX +2, total +6.
        sample_rogue.expertise_skills = [Skill.STEALTH]
        mod = compute_skill_modifier(sample_rogue, Skill.STEALTH)
        dex_mod = compute_modifier(sample_rogue.ability_scores.get(Ability.DEX))
        assert mod == dex_mod + 2 * sample_rogue.proficiency_bonus

    def test_expertise_without_base_proficiency_still_doubles(
        self, sample_rogue: Character,
    ) -> None:
        # SRD: a skill must be proficient to be Expertise-able. We mirror
        # that here — Expertise alone (without proficiency) STILL counts
        # as double-proficiency, since the only legit way to acquire it
        # implies the proficiency. This keeps the math simple.
        sample_rogue.skill_proficiencies = []
        sample_rogue.expertise_skills = [Skill.SLEIGHT_OF_HAND]
        mod = compute_skill_modifier(sample_rogue, Skill.SLEIGHT_OF_HAND)
        dex_mod = compute_modifier(sample_rogue.ability_scores.get(Ability.DEX))
        assert mod == dex_mod + 2 * sample_rogue.proficiency_bonus

    def test_expertise_does_not_leak_to_other_skills(
        self, sample_rogue: Character,
    ) -> None:
        sample_rogue.expertise_skills = [Skill.STEALTH]
        # Acrobatics is proficient but NOT in expertise — single prof bonus.
        mod = compute_skill_modifier(sample_rogue, Skill.ACROBATICS)
        dex_mod = compute_modifier(sample_rogue.ability_scores.get(Ability.DEX))
        assert mod == dex_mod + sample_rogue.proficiency_bonus


# ---------------------------------------------------------------------------
# CLASS_SKILL_CHOICES
# ---------------------------------------------------------------------------


class TestClassSkillChoices:
    """CLASS_SKILL_CHOICES covers all classes with correct pick counts."""

    def test_all_six_classes_have_entries(self) -> None:
        for cls in CharacterClass:
            assert cls in CLASS_SKILL_CHOICES, f"Missing CLASS_SKILL_CHOICES for {cls}"

    @pytest.mark.parametrize(
        "cls,expected_choose",
        [
            (CharacterClass.FIGHTER, 2),
            (CharacterClass.WIZARD, 2),
            (CharacterClass.ROGUE, 4),
            (CharacterClass.CLERIC, 2),
            (CharacterClass.RANGER, 3),
            (CharacterClass.BARBARIAN, 2),
        ],
    )
    def test_choose_count(
        self, cls: CharacterClass, expected_choose: int
    ) -> None:
        assert CLASS_SKILL_CHOICES[cls].choose == expected_choose

    def test_options_are_valid_skills(self) -> None:
        for cls, config in CLASS_SKILL_CHOICES.items():
            for skill in config.options:
                assert isinstance(skill, Skill), (
                    f"{cls}: {skill} is not a Skill enum"
                )

    def test_choose_not_greater_than_options(self) -> None:
        for cls, config in CLASS_SKILL_CHOICES.items():
            assert config.choose <= len(config.options), (
                f"{cls}: choose={config.choose} > len(options)={len(config.options)}"
            )


class TestClassSkillConfig:
    """ClassSkillConfig model validation."""

    def test_create_config(self) -> None:
        config = ClassSkillConfig(
            choose=2, options=[Skill.ATHLETICS, Skill.ACROBATICS, Skill.STEALTH]
        )
        assert config.choose == 2
        assert len(config.options) == 3

    def test_empty_options(self) -> None:
        config = ClassSkillConfig(choose=0, options=[])
        assert config.choose == 0
        assert config.options == []
