"""Tests for engine/character/features.py — Feature system and racial/class catalogs."""

import pytest

from engine.character import (
    AbilityScores,
    Character,
    CharacterClass,
    Feature,
    FeatureSource,
    MechanicalEffect,
    Race,
    RACIAL_FEATURES,
    CLASS_FEATURES,
    apply_racial_bonuses,
    create_character,
    get_damage_resistances,
    get_feature_effects,
    has_darkvision,
    has_feature,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def base_scores() -> AbilityScores:
    return AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)


def _make_char_with_features(
    race: Race, features: list[Feature], scores: AbilityScores | None = None,
) -> Character:
    """Helper to create a character with explicit features."""
    if scores is None:
        scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    scores = apply_racial_bonuses(scores, race)
    char = create_character("Test", race, CharacterClass.FIGHTER, scores)
    char.features = list(features)
    return char


# ---------------------------------------------------------------------------
# Model creation and validation
# ---------------------------------------------------------------------------


class TestFeatureModel:
    """Feature Pydantic model creation and validation."""

    def test_create_feature(self) -> None:
        feat = Feature(
            name="Darkvision",
            source=FeatureSource.RACE,
            source_name="Elf",
            description="See in dim light within 60 feet.",
            effects=[MechanicalEffect(effect_type="darkvision", value=60)],
        )
        assert feat.name == "Darkvision"
        assert feat.source == FeatureSource.RACE
        assert feat.source_name == "Elf"
        assert feat.level_requirement == 1

    def test_feature_with_level_requirement(self) -> None:
        feat = Feature(
            name="Extra Attack",
            source=FeatureSource.CLASS,
            source_name="Fighter",
            description="Attack twice.",
            effects=[],
            level_requirement=5,
        )
        assert feat.level_requirement == 5

    def test_mechanical_effect_int_value(self) -> None:
        eff = MechanicalEffect(effect_type="darkvision", value=60)
        assert eff.effect_type == "darkvision"
        assert eff.value == 60

    def test_mechanical_effect_str_value(self) -> None:
        eff = MechanicalEffect(effect_type="damage_resistance", value="fire")
        assert eff.value == "fire"

    def test_mechanical_effect_list_value(self) -> None:
        eff = MechanicalEffect(
            effect_type="damage_resistance",
            value=["bludgeoning", "piercing", "slashing"],
        )
        assert isinstance(eff.value, list)
        assert len(eff.value) == 3


class TestFeatureSourceEnum:
    """FeatureSource has the expected members."""

    def test_all_sources(self) -> None:
        assert FeatureSource.RACE == "race"
        assert FeatureSource.CLASS == "class"
        assert FeatureSource.BACKGROUND == "background"
        assert FeatureSource.FEAT == "feat"


# ---------------------------------------------------------------------------
# RACIAL_FEATURES catalog
# ---------------------------------------------------------------------------


class TestRacialFeatures:
    """RACIAL_FEATURES catalog covers all races with correct traits."""

    def test_all_seven_races_have_entries(self) -> None:
        for race in Race:
            assert race in RACIAL_FEATURES, f"Missing RACIAL_FEATURES for {race}"

    def test_human_has_no_features(self) -> None:
        assert RACIAL_FEATURES[Race.HUMAN] == []

    @pytest.mark.parametrize(
        "race",
        [Race.ELF, Race.DWARF, Race.HALF_ORC, Race.GNOME, Race.TIEFLING],
    )
    def test_darkvision_races(self, race: Race) -> None:
        names = [f.name for f in RACIAL_FEATURES[race]]
        assert "Darkvision" in names

    def test_halfling_no_darkvision(self) -> None:
        names = [f.name for f in RACIAL_FEATURES[Race.HALFLING]]
        assert "Darkvision" not in names

    def test_elf_features(self) -> None:
        names = {f.name for f in RACIAL_FEATURES[Race.ELF]}
        assert names == {"Darkvision", "Keen Senses", "Fey Ancestry"}

    def test_dwarf_features(self) -> None:
        names = {f.name for f in RACIAL_FEATURES[Race.DWARF]}
        assert names == {"Darkvision", "Dwarven Resilience", "Stonecunning"}

    def test_halfling_features(self) -> None:
        names = {f.name for f in RACIAL_FEATURES[Race.HALFLING]}
        assert names == {"Lucky", "Brave", "Halfling Nimbleness"}

    def test_half_orc_features(self) -> None:
        names = {f.name for f in RACIAL_FEATURES[Race.HALF_ORC]}
        assert names == {"Darkvision", "Relentless Endurance", "Savage Attacks"}

    def test_gnome_features(self) -> None:
        names = {f.name for f in RACIAL_FEATURES[Race.GNOME]}
        assert names == {"Darkvision", "Gnome Cunning"}

    def test_tiefling_features(self) -> None:
        names = {f.name for f in RACIAL_FEATURES[Race.TIEFLING]}
        assert names == {"Darkvision", "Hellish Resistance", "Infernal Legacy"}

    def test_all_features_are_race_source(self) -> None:
        for race, feats in RACIAL_FEATURES.items():
            for f in feats:
                assert f.source == FeatureSource.RACE, (
                    f"{f.name} has source {f.source}, expected RACE"
                )


# ---------------------------------------------------------------------------
# CLASS_FEATURES catalog
# ---------------------------------------------------------------------------


class TestClassFeatures:
    """CLASS_FEATURES catalog covers all classes with correct level-1 features."""

    def test_all_six_classes_have_entries(self) -> None:
        for cls in CharacterClass:
            assert cls in CLASS_FEATURES, f"Missing CLASS_FEATURES for {cls}"

    def test_fighter_features(self) -> None:
        names = {f.name for f in CLASS_FEATURES[CharacterClass.FIGHTER]}
        assert names == {"Fighting Style", "Second Wind"}

    def test_wizard_features(self) -> None:
        names = {f.name for f in CLASS_FEATURES[CharacterClass.WIZARD]}
        assert names == {"Arcane Recovery", "Spellcasting"}

    def test_rogue_features(self) -> None:
        names = {f.name for f in CLASS_FEATURES[CharacterClass.ROGUE]}
        assert names == {"Sneak Attack", "Expertise", "Thieves' Cant"}

    def test_cleric_features(self) -> None:
        names = {f.name for f in CLASS_FEATURES[CharacterClass.CLERIC]}
        assert names == {"Spellcasting", "Divine Domain"}

    def test_ranger_features(self) -> None:
        names = {f.name for f in CLASS_FEATURES[CharacterClass.RANGER]}
        assert names == {"Favored Enemy", "Natural Explorer"}

    def test_barbarian_features(self) -> None:
        names = {f.name for f in CLASS_FEATURES[CharacterClass.BARBARIAN]}
        assert names == {"Rage", "Unarmored Defense"}

    def test_all_features_are_class_source(self) -> None:
        for cls, feats in CLASS_FEATURES.items():
            for f in feats:
                assert f.source == FeatureSource.CLASS, (
                    f"{f.name} has source {f.source}, expected CLASS"
                )

    def test_all_level_1(self) -> None:
        for cls, feats in CLASS_FEATURES.items():
            for f in feats:
                assert f.level_requirement == 1, (
                    f"{f.name} has level_requirement {f.level_requirement}, expected 1"
                )


# ---------------------------------------------------------------------------
# Feature helper functions
# ---------------------------------------------------------------------------


class TestHasFeature:
    """has_feature() checks by name."""

    def test_has_feature_present(self) -> None:
        char = _make_char_with_features(Race.ELF, RACIAL_FEATURES[Race.ELF])
        assert has_feature(char, "Darkvision") is True

    def test_has_feature_absent(self) -> None:
        char = _make_char_with_features(Race.HUMAN, [])
        assert has_feature(char, "Darkvision") is False


class TestHasDarkvision:
    """has_darkvision() returns range or None."""

    @pytest.mark.parametrize(
        "race",
        [Race.ELF, Race.DWARF, Race.HALF_ORC, Race.GNOME, Race.TIEFLING],
    )
    def test_darkvision_races_return_60(self, race: Race) -> None:
        char = _make_char_with_features(race, RACIAL_FEATURES[race])
        assert has_darkvision(char) == 60

    @pytest.mark.parametrize("race", [Race.HUMAN, Race.HALFLING])
    def test_no_darkvision_returns_none(self, race: Race) -> None:
        char = _make_char_with_features(race, RACIAL_FEATURES[race])
        assert has_darkvision(char) is None


class TestGetDamageResistances:
    """get_damage_resistances() returns damage type list."""

    def test_dwarf_has_poison_resistance(self) -> None:
        char = _make_char_with_features(Race.DWARF, RACIAL_FEATURES[Race.DWARF])
        resistances = get_damage_resistances(char)
        assert "poison" in resistances

    def test_tiefling_has_fire_resistance(self) -> None:
        char = _make_char_with_features(
            Race.TIEFLING, RACIAL_FEATURES[Race.TIEFLING]
        )
        resistances = get_damage_resistances(char)
        assert "fire" in resistances

    def test_human_has_no_resistances(self) -> None:
        char = _make_char_with_features(Race.HUMAN, [])
        assert get_damage_resistances(char) == []

    def test_barbarian_rage_resistances(self) -> None:
        char = _make_char_with_features(Race.HUMAN, CLASS_FEATURES[CharacterClass.BARBARIAN])
        resistances = get_damage_resistances(char)
        assert set(resistances) == {"bludgeoning", "piercing", "slashing"}


class TestGetFeatureEffects:
    """get_feature_effects() filters by effect type."""

    def test_get_spellcasting_effects(self) -> None:
        char = _make_char_with_features(
            Race.HUMAN, CLASS_FEATURES[CharacterClass.WIZARD]
        )
        effects = get_feature_effects(char, "spellcasting")
        assert len(effects) == 1
        assert effects[0].value == "INT"

    def test_get_nonexistent_effect_type(self) -> None:
        char = _make_char_with_features(Race.HUMAN, [])
        assert get_feature_effects(char, "nonexistent") == []
