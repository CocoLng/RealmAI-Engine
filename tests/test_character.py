"""Tests for engine/character.py — character creation, stats, and progression."""

import pytest

from engine.character import (
    Ability,
    AbilityScores,
    Alignment,
    Character,
    CharacterClass,
    Race,
    Size,
    add_xp,
    apply_racial_bonuses,
    check_level_up,
    compute_max_hp,
    compute_modifier,
    compute_proficiency_bonus,
    create_character,
    level_up,
    roll_ability_scores,
    CLASS_HIT_DIE,
    CLASS_SAVING_THROWS,
    RACIAL_SIZE,
    RACIAL_SPEED,
    XP_THRESHOLDS,
)
from engine.dice import DiceResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_scores() -> AbilityScores:
    """Standard array-style ability scores."""
    return AbilityScores(STR=15, DEX=14, CON=13, INT=12, WIS=10, CHA=8)


@pytest.fixture()
def sample_fighter(sample_scores: AbilityScores) -> Character:
    """Level-1 Human Fighter."""
    scores = apply_racial_bonuses(sample_scores, Race.HUMAN)
    return create_character("Arden", Race.HUMAN, CharacterClass.FIGHTER, scores)


@pytest.fixture()
def sample_wizard(sample_scores: AbilityScores) -> Character:
    """Level-1 Elf Wizard."""
    scores = apply_racial_bonuses(sample_scores, Race.ELF)
    return create_character("Elara", Race.ELF, CharacterClass.WIZARD, scores)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestAbility:
    """Ability enum has all six abilities."""

    def test_all_six_abilities_exist(self) -> None:
        assert len(Ability) == 6
        for name in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
            assert name in Ability.__members__


class TestRace:
    """Race enum has all seven races."""

    def test_all_seven_races_exist(self) -> None:
        assert len(Race) == 7
        for name in (
            "HUMAN", "ELF", "DWARF", "HALFLING", "HALF_ORC", "GNOME", "TIEFLING",
        ):
            assert name in Race.__members__


class TestCharacterClass:
    """CharacterClass enum has all six classes."""

    def test_all_six_classes_exist(self) -> None:
        assert len(CharacterClass) == 6
        for name in (
            "FIGHTER", "WIZARD", "ROGUE", "CLERIC", "RANGER", "BARBARIAN",
        ):
            assert name in CharacterClass.__members__


class TestAlignment:
    """Alignment enum has all nine alignments."""

    def test_all_nine_alignments_exist(self) -> None:
        assert len(Alignment) == 9


class TestSize:
    """Size enum has Small and Medium."""

    def test_small_and_medium(self) -> None:
        assert len(Size) == 2
        assert "SMALL" in Size.__members__
        assert "MEDIUM" in Size.__members__


# ---------------------------------------------------------------------------
# AbilityScores model
# ---------------------------------------------------------------------------


class TestAbilityScores:
    """AbilityScores Pydantic model validation."""

    def test_valid_scores(self, sample_scores: AbilityScores) -> None:
        assert sample_scores.STR == 15
        assert sample_scores.CHA == 8

    def test_get_by_ability_enum(self, sample_scores: AbilityScores) -> None:
        assert sample_scores.get(Ability.STR) == 15
        assert sample_scores.get(Ability.DEX) == 14
        assert sample_scores.get(Ability.CHA) == 8

    def test_score_below_minimum_raises(self) -> None:
        with pytest.raises(ValueError):
            AbilityScores(STR=0, DEX=10, CON=10, INT=10, WIS=10, CHA=10)

    def test_score_above_maximum_raises(self) -> None:
        with pytest.raises(ValueError):
            AbilityScores(STR=31, DEX=10, CON=10, INT=10, WIS=10, CHA=10)


# ---------------------------------------------------------------------------
# compute_modifier
# ---------------------------------------------------------------------------


class TestComputeModifier:
    """SRD ability modifier formula: (score - 10) // 2."""

    @pytest.mark.parametrize(
        "score,expected",
        [
            (1, -5),
            (3, -4),
            (8, -1),
            (9, -1),
            (10, 0),
            (11, 0),
            (12, 1),
            (14, 2),
            (15, 2),
            (18, 4),
            (20, 5),
            (30, 10),
        ],
    )
    def test_modifier_table(self, score: int, expected: int) -> None:
        assert compute_modifier(score) == expected


# ---------------------------------------------------------------------------
# compute_proficiency_bonus
# ---------------------------------------------------------------------------


class TestComputeProficiencyBonus:
    """Proficiency bonus scales with level per SRD table."""

    @pytest.mark.parametrize("level", [1, 2, 3, 4])
    def test_levels_1_to_4_give_plus_2(self, level: int) -> None:
        assert compute_proficiency_bonus(level) == 2

    @pytest.mark.parametrize("level", [5, 6, 7, 8])
    def test_levels_5_to_8_give_plus_3(self, level: int) -> None:
        assert compute_proficiency_bonus(level) == 3

    @pytest.mark.parametrize("level", [9, 10, 11, 12])
    def test_levels_9_to_12_give_plus_4(self, level: int) -> None:
        assert compute_proficiency_bonus(level) == 4

    @pytest.mark.parametrize("level", [13, 14, 15, 16])
    def test_levels_13_to_16_give_plus_5(self, level: int) -> None:
        assert compute_proficiency_bonus(level) == 5

    @pytest.mark.parametrize("level", [17, 18, 19, 20])
    def test_levels_17_to_20_give_plus_6(self, level: int) -> None:
        assert compute_proficiency_bonus(level) == 6

    @pytest.mark.parametrize("level", [0, -1, 21])
    def test_invalid_level_raises(self, level: int) -> None:
        with pytest.raises(ValueError, match="Level must be 1-20"):
            compute_proficiency_bonus(level)


# ---------------------------------------------------------------------------
# compute_max_hp
# ---------------------------------------------------------------------------


class TestComputeMaxHP:
    """Max HP = hit die max + CON mod at level 1, average + CON mod per level after."""

    def test_fighter_level_1(self) -> None:
        # d10, CON 14 (+2) → 10 + 2 = 12
        assert compute_max_hp(CharacterClass.FIGHTER, 1, 2) == 12

    def test_wizard_level_1(self) -> None:
        # d6, CON 10 (+0) → 6 + 0 = 6
        assert compute_max_hp(CharacterClass.WIZARD, 1, 0) == 6

    def test_barbarian_level_1(self) -> None:
        # d12, CON 16 (+3) → 12 + 3 = 15
        assert compute_max_hp(CharacterClass.BARBARIAN, 1, 3) == 15

    def test_fighter_level_5(self) -> None:
        # d10, CON 14 (+2)
        # Level 1: 10 + 2 = 12
        # Levels 2-5: 4 * (6 + 2) = 32  (average of d10 = 5.5 → 6)
        assert compute_max_hp(CharacterClass.FIGHTER, 5, 2) == 44

    def test_rogue_level_3(self) -> None:
        # d8, CON 12 (+1)
        # Level 1: 8 + 1 = 9
        # Levels 2-3: 2 * (5 + 1) = 12  (average of d8 = 4.5 → 5)
        assert compute_max_hp(CharacterClass.ROGUE, 3, 1) == 21

    def test_minimum_1_hp_per_level(self) -> None:
        # d6, CON 3 (-4). Level 1: max(1, 6 + (-4)) = 2
        # Level 2: max(1, 4 + (-4)) = 1
        assert compute_max_hp(CharacterClass.WIZARD, 1, -4) == 2
        assert compute_max_hp(CharacterClass.WIZARD, 2, -4) == 3

    @pytest.mark.parametrize(
        "char_class,expected_level1",
        [
            (CharacterClass.BARBARIAN, 12),   # d12 + 0
            (CharacterClass.FIGHTER, 10),      # d10 + 0
            (CharacterClass.RANGER, 10),       # d10 + 0
            (CharacterClass.CLERIC, 8),        # d8 + 0
            (CharacterClass.ROGUE, 8),         # d8 + 0
            (CharacterClass.WIZARD, 6),        # d6 + 0
        ],
    )
    def test_all_classes_level_1_no_con(
        self, char_class: CharacterClass, expected_level1: int
    ) -> None:
        assert compute_max_hp(char_class, 1, 0) == expected_level1


# ---------------------------------------------------------------------------
# apply_racial_bonuses
# ---------------------------------------------------------------------------


class TestApplyRacialBonuses:
    """Racial ability score adjustments."""

    def test_human_gets_plus_1_all(self) -> None:
        base = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
        result = apply_racial_bonuses(base, Race.HUMAN)
        assert result.STR == 11
        assert result.DEX == 11
        assert result.CON == 11
        assert result.INT == 11
        assert result.WIS == 11
        assert result.CHA == 11

    def test_elf_gets_plus_2_dex(self) -> None:
        base = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
        result = apply_racial_bonuses(base, Race.ELF)
        assert result.DEX == 12
        assert result.STR == 10  # unchanged

    def test_half_orc_gets_str_and_con(self) -> None:
        base = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
        result = apply_racial_bonuses(base, Race.HALF_ORC)
        assert result.STR == 12
        assert result.CON == 11

    def test_returns_new_instance(self, sample_scores: AbilityScores) -> None:
        result = apply_racial_bonuses(sample_scores, Race.HUMAN)
        assert result is not sample_scores
        # Original unchanged
        assert sample_scores.STR == 15


# ---------------------------------------------------------------------------
# roll_ability_scores
# ---------------------------------------------------------------------------


class TestRollAbilityScores:
    """4d6-drop-lowest ability score generation."""

    def test_returns_ability_scores_model(self) -> None:
        result = roll_ability_scores()
        assert isinstance(result, AbilityScores)

    def test_all_scores_in_valid_range(self) -> None:
        """Each score should be 3-18 (4d6 drop lowest)."""
        for _ in range(20):
            scores = roll_ability_scores()
            for ability in Ability:
                score = scores.get(ability)
                assert 3 <= score <= 18, f"{ability} = {score}, expected 3-18"

    def test_uses_4d6_drop_lowest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the drop-lowest logic with controlled rolls."""
        # 4d6 rolls: [3, 5, 2, 6] → drop 2 → 3+5+6 = 14
        fake_result = DiceResult(expression="4d6", rolls=[3, 5, 2, 6], modifier=0, total=16)
        call_count = 0

        def fake_roll(expr: str) -> DiceResult:
            nonlocal call_count
            assert expr == "4d6"
            call_count += 1
            return fake_result

        monkeypatch.setattr("engine.character.roll", fake_roll)
        scores = roll_ability_scores()
        assert call_count == 6
        # All scores should be 14 (3+5+6, dropping 2)
        for ability in Ability:
            assert scores.get(ability) == 14


# ---------------------------------------------------------------------------
# create_character
# ---------------------------------------------------------------------------


class TestCreateCharacter:
    """Character creation with computed derived stats."""

    def test_creates_level_1(self, sample_fighter: Character) -> None:
        assert sample_fighter.level == 1
        assert sample_fighter.xp == 0

    def test_hp_equals_max_hp(self, sample_fighter: Character) -> None:
        assert sample_fighter.hp == sample_fighter.max_hp
        assert sample_fighter.hp > 0

    def test_proficiency_bonus_is_2(self, sample_fighter: Character) -> None:
        assert sample_fighter.proficiency_bonus == 2

    def test_fighter_has_d10_hit_die(self, sample_fighter: Character) -> None:
        assert sample_fighter.hit_die == "1d10"

    def test_wizard_has_d6_hit_die(self, sample_wizard: Character) -> None:
        assert sample_wizard.hit_die == "1d6"

    def test_ac_computed_from_dex(self, sample_fighter: Character) -> None:
        dex_mod = compute_modifier(sample_fighter.ability_scores.get(Ability.DEX))
        assert sample_fighter.ac == 10 + dex_mod

    def test_default_alignment(self, sample_fighter: Character) -> None:
        assert sample_fighter.alignment == Alignment.TRUE_NEUTRAL

    def test_custom_alignment(self, sample_scores: AbilityScores) -> None:
        scores = apply_racial_bonuses(sample_scores, Race.HUMAN)
        char = create_character(
            "Test", Race.HUMAN, CharacterClass.FIGHTER, scores,
            alignment=Alignment.CHAOTIC_GOOD,
        )
        assert char.alignment == Alignment.CHAOTIC_GOOD

    @pytest.mark.parametrize("char_class", list(CharacterClass))
    def test_correct_saving_throws_by_class(
        self, char_class: CharacterClass, sample_scores: AbilityScores
    ) -> None:
        scores = apply_racial_bonuses(sample_scores, Race.HUMAN)
        char = create_character("Test", Race.HUMAN, char_class, scores)
        assert char.saving_throw_proficiencies == CLASS_SAVING_THROWS[char_class]

    @pytest.mark.parametrize("race", list(Race))
    def test_correct_size_by_race(
        self, race: Race, sample_scores: AbilityScores
    ) -> None:
        scores = apply_racial_bonuses(sample_scores, race)
        char = create_character("Test", race, CharacterClass.FIGHTER, scores)
        assert char.size == RACIAL_SIZE[race]

    @pytest.mark.parametrize("race", list(Race))
    def test_correct_speed_by_race(
        self, race: Race, sample_scores: AbilityScores
    ) -> None:
        scores = apply_racial_bonuses(sample_scores, race)
        char = create_character("Test", race, CharacterClass.FIGHTER, scores)
        assert char.speed == RACIAL_SPEED[race]

    @pytest.mark.parametrize("char_class", list(CharacterClass))
    def test_correct_hit_die_by_class(
        self, char_class: CharacterClass, sample_scores: AbilityScores
    ) -> None:
        scores = apply_racial_bonuses(sample_scores, Race.HUMAN)
        char = create_character("Test", Race.HUMAN, char_class, scores)
        assert char.hit_die == CLASS_HIT_DIE[char_class]

    def test_empty_name_raises(self, sample_scores: AbilityScores) -> None:
        scores = apply_racial_bonuses(sample_scores, Race.HUMAN)
        with pytest.raises(ValueError):
            create_character("", Race.HUMAN, CharacterClass.FIGHTER, scores)


# ---------------------------------------------------------------------------
# Level-up and XP
# ---------------------------------------------------------------------------


class TestAddXP:
    """XP management."""

    def test_adds_xp(self, sample_fighter: Character) -> None:
        add_xp(sample_fighter, 200)
        assert sample_fighter.xp == 200

    def test_does_not_auto_level(self, sample_fighter: Character) -> None:
        add_xp(sample_fighter, 999_999)
        assert sample_fighter.level == 1

    def test_negative_amount_raises(self, sample_fighter: Character) -> None:
        with pytest.raises(ValueError, match="positive"):
            add_xp(sample_fighter, -10)


class TestCheckLevelUp:
    """Level-up eligibility check."""

    def test_returns_true_when_enough_xp(self, sample_fighter: Character) -> None:
        sample_fighter.xp = XP_THRESHOLDS[2]  # 300 XP for level 2
        assert check_level_up(sample_fighter) is True

    def test_returns_false_when_not_enough_xp(self, sample_fighter: Character) -> None:
        sample_fighter.xp = 0
        assert check_level_up(sample_fighter) is False

    def test_returns_false_at_level_20(self, sample_fighter: Character) -> None:
        sample_fighter.level = 20
        sample_fighter.xp = 999_999
        assert check_level_up(sample_fighter) is False


class TestLevelUp:
    """Character level progression."""

    def test_increments_level(self, sample_fighter: Character) -> None:
        sample_fighter.xp = XP_THRESHOLDS[2]
        level_up(sample_fighter)
        assert sample_fighter.level == 2

    def test_increases_max_hp(self, sample_fighter: Character) -> None:
        old_max_hp = sample_fighter.max_hp
        sample_fighter.xp = XP_THRESHOLDS[2]
        level_up(sample_fighter)
        assert sample_fighter.max_hp > old_max_hp

    def test_increases_current_hp(self, sample_fighter: Character) -> None:
        old_hp = sample_fighter.hp
        sample_fighter.xp = XP_THRESHOLDS[2]
        level_up(sample_fighter)
        assert sample_fighter.hp > old_hp

    def test_updates_proficiency_at_level_5(self, sample_fighter: Character) -> None:
        # Level up from 4 to 5
        sample_fighter.level = 4
        sample_fighter.xp = XP_THRESHOLDS[5]
        level_up(sample_fighter)
        assert sample_fighter.level == 5
        assert sample_fighter.proficiency_bonus == 3

    def test_max_level_raises(self, sample_fighter: Character) -> None:
        sample_fighter.level = 20
        sample_fighter.xp = 999_999
        with pytest.raises(ValueError, match="already level 20"):
            level_up(sample_fighter)

    def test_not_enough_xp_raises(self, sample_fighter: Character) -> None:
        sample_fighter.xp = 0
        with pytest.raises(ValueError, match="Not enough XP"):
            level_up(sample_fighter)

    def test_mutates_in_place(self, sample_fighter: Character) -> None:
        sample_fighter.xp = XP_THRESHOLDS[2]
        returned = level_up(sample_fighter)
        assert returned is sample_fighter


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestCharacterSerialization:
    """Pydantic model serialization roundtrip."""

    def test_model_dump_roundtrip(self, sample_fighter: Character) -> None:
        data = sample_fighter.model_dump()
        restored = Character(**data)
        assert restored == sample_fighter

    def test_model_json_roundtrip(self, sample_fighter: Character) -> None:
        json_str = sample_fighter.model_dump_json()
        restored = Character.model_validate_json(json_str)
        assert restored == sample_fighter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_character(dex: int = 10) -> Character:
    """Create a minimal character for testing specific stats."""
    scores = AbilityScores(STR=10, DEX=dex, CON=10, INT=10, WIS=10, CHA=10)
    return create_character("Test", Race.HUMAN, CharacterClass.FIGHTER, scores)
