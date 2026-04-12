"""Tests for engine/spells.py — spell system.

Covers enums, models, spell DC/attack, cantrip scaling, slots,
casting logic, spellcaster state, and the spell catalog.
"""

import pytest

from engine.character import Ability, CharacterClass
from engine.inventory import DamageType
from engine.spells import (
    FULL_CASTER_SLOTS,
    HALF_CASTER_SLOTS,
    SPELL_CATALOG,
    CastingTime,
    Spell,
    SpellcasterState,
    SpellRange,
    SpellSchool,
    can_cast_spell,
    cast_spell,
    compute_spell_attack_bonus,
    compute_spell_dc,
    create_spellcaster_state,
    get_cantrip_damage_dice,
    get_spell_slots,
    restore_spell_slots,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestSpellSchool:
    def test_all_schools_exist(self) -> None:
        expected = {
            "Abjuration", "Conjuration", "Divination", "Enchantment",
            "Evocation", "Illusion", "Necromancy", "Transmutation",
        }
        assert {s.value for s in SpellSchool} == expected

    def test_casting_times(self) -> None:
        assert CastingTime.ACTION == "Action"
        assert CastingTime.BONUS_ACTION == "Bonus Action"
        assert CastingTime.REACTION == "Reaction"

    def test_spell_ranges(self) -> None:
        assert SpellRange.SELF == "Self"
        assert SpellRange.FEET_120 == "120 feet"


# ---------------------------------------------------------------------------
# Spell model
# ---------------------------------------------------------------------------


class TestSpellModel:
    def test_cantrip_creation(self) -> None:
        spell = Spell(
            name="Test Cantrip",
            level=0,
            school=SpellSchool.EVOCATION,
            casting_time=CastingTime.ACTION,
            spell_range=SpellRange.FEET_60,
            components=["V", "S"],
            damage_dice="1d8",
            damage_type=DamageType.FIRE,
        )
        assert spell.level == 0
        assert spell.name == "Test Cantrip"
        assert spell.concentration is False
        assert spell.duration_rounds is None

    def test_leveled_spell_creation(self) -> None:
        spell = Spell(
            name="Fireball",
            level=3,
            school=SpellSchool.EVOCATION,
            casting_time=CastingTime.ACTION,
            spell_range=SpellRange.FEET_150,
            components=["V", "S", "M"],
            damage_dice="8d6",
            damage_type=DamageType.FIRE,
            saving_throw=Ability.DEX,
            higher_level_dice="1d6",
        )
        assert spell.level == 3
        assert spell.saving_throw == Ability.DEX
        assert spell.higher_level_dice == "1d6"

    def test_invalid_level(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            Spell(
                name="Bad",
                level=-1,
                school=SpellSchool.EVOCATION,
                casting_time=CastingTime.ACTION,
                spell_range=SpellRange.SELF,
            )

    def test_level_too_high(self) -> None:
        with pytest.raises(ValueError, match="less than or equal to 9"):
            Spell(
                name="Bad",
                level=10,
                school=SpellSchool.EVOCATION,
                casting_time=CastingTime.ACTION,
                spell_range=SpellRange.SELF,
            )

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="String should have at least 1 character"):
            Spell(
                name="",
                level=0,
                school=SpellSchool.EVOCATION,
                casting_time=CastingTime.ACTION,
                spell_range=SpellRange.SELF,
            )

    def test_roundtrip_serialization(self) -> None:
        spell = SPELL_CATALOG["Fireball"]
        data = spell.model_dump()
        restored = Spell(**data)
        assert restored == spell


# ---------------------------------------------------------------------------
# Spell DC and attack bonus
# ---------------------------------------------------------------------------


class TestSpellDC:
    @pytest.mark.parametrize(
        ("ability_score", "prof", "expected"),
        [
            (10, 2, 10),   # mod 0 + 2 + 8
            (16, 2, 13),   # mod 3 + 2 + 8
            (20, 3, 16),   # mod 5 + 3 + 8
            (8, 2, 9),     # mod -1 + 2 + 8
            (14, 4, 14),   # mod 2 + 4 + 8
        ],
    )
    def test_compute_spell_dc(self, ability_score: int, prof: int, expected: int) -> None:
        assert compute_spell_dc(ability_score, prof) == expected


class TestSpellAttackBonus:
    @pytest.mark.parametrize(
        ("ability_score", "prof", "expected"),
        [
            (10, 2, 2),   # mod 0 + 2
            (16, 2, 5),   # mod 3 + 2
            (20, 3, 8),   # mod 5 + 3
            (8, 2, 1),    # mod -1 + 2
            (14, 4, 6),   # mod 2 + 4
        ],
    )
    def test_compute_spell_attack_bonus(
        self, ability_score: int, prof: int, expected: int,
    ) -> None:
        assert compute_spell_attack_bonus(ability_score, prof) == expected


# ---------------------------------------------------------------------------
# Cantrip scaling
# ---------------------------------------------------------------------------


class TestCantripScaling:
    @pytest.mark.parametrize(
        ("level", "expected_dice"),
        [
            (1, "1d10"),
            (4, "1d10"),
            (5, "2d10"),
            (10, "2d10"),
            (11, "3d10"),
            (16, "3d10"),
            (17, "4d10"),
            (20, "4d10"),
        ],
    )
    def test_fire_bolt_scaling(self, level: int, expected_dice: str) -> None:
        fire_bolt = SPELL_CATALOG["Fire Bolt"]
        assert get_cantrip_damage_dice(fire_bolt, level) == expected_dice

    @pytest.mark.parametrize(
        ("level", "expected_dice"),
        [
            (1, "1d8"),
            (5, "2d8"),
            (11, "3d8"),
            (17, "4d8"),
        ],
    )
    def test_sacred_flame_scaling(self, level: int, expected_dice: str) -> None:
        sacred_flame = SPELL_CATALOG["Sacred Flame"]
        assert get_cantrip_damage_dice(sacred_flame, level) == expected_dice

    def test_non_cantrip_raises(self) -> None:
        fireball = SPELL_CATALOG["Fireball"]
        with pytest.raises(ValueError, match="not a cantrip"):
            get_cantrip_damage_dice(fireball, 5)

    def test_no_damage_dice_raises(self) -> None:
        light = SPELL_CATALOG["Light"]
        with pytest.raises(ValueError, match="no damage dice"):
            get_cantrip_damage_dice(light, 1)


# ---------------------------------------------------------------------------
# Spell slots
# ---------------------------------------------------------------------------


class TestSpellSlots:
    def test_wizard_level_1(self) -> None:
        slots = get_spell_slots(CharacterClass.WIZARD, 1)
        assert slots == {1: 2}

    def test_wizard_level_5(self) -> None:
        slots = get_spell_slots(CharacterClass.WIZARD, 5)
        assert slots == {1: 4, 2: 3, 3: 2}

    def test_wizard_level_20(self) -> None:
        slots = get_spell_slots(CharacterClass.WIZARD, 20)
        assert slots == {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1}

    def test_cleric_level_9(self) -> None:
        slots = get_spell_slots(CharacterClass.CLERIC, 9)
        assert slots == {1: 4, 2: 3, 3: 3, 4: 3, 5: 1}

    def test_ranger_level_1_no_slots(self) -> None:
        slots = get_spell_slots(CharacterClass.RANGER, 1)
        assert slots == {}

    def test_ranger_level_2_has_slots(self) -> None:
        slots = get_spell_slots(CharacterClass.RANGER, 2)
        assert slots == {1: 2}

    def test_ranger_level_5(self) -> None:
        slots = get_spell_slots(CharacterClass.RANGER, 5)
        assert slots == {1: 4, 2: 2}

    def test_fighter_no_slots(self) -> None:
        slots = get_spell_slots(CharacterClass.FIGHTER, 10)
        assert slots == {}

    def test_barbarian_no_slots(self) -> None:
        slots = get_spell_slots(CharacterClass.BARBARIAN, 20)
        assert slots == {}

    def test_rogue_no_slots(self) -> None:
        slots = get_spell_slots(CharacterClass.ROGUE, 5)
        assert slots == {}

    def test_invalid_level_raises(self) -> None:
        with pytest.raises(ValueError, match="Level must be 1-20"):
            get_spell_slots(CharacterClass.WIZARD, 0)

    def test_invalid_level_high_raises(self) -> None:
        with pytest.raises(ValueError, match="Level must be 1-20"):
            get_spell_slots(CharacterClass.WIZARD, 21)

    def test_returns_copy(self) -> None:
        """get_spell_slots returns a copy, not a reference to the table."""
        slots = get_spell_slots(CharacterClass.WIZARD, 1)
        slots[1] = 999
        assert FULL_CASTER_SLOTS[1][1] == 2


# ---------------------------------------------------------------------------
# Can cast / cast spell
# ---------------------------------------------------------------------------


class TestCanCastSpell:
    @pytest.fixture()
    def wizard_state(self) -> SpellcasterState:
        return SpellcasterState(
            spellcasting_ability=Ability.INT,
            spells_known=["Fire Bolt", "Magic Missile", "Fireball", "Guidance"],
            spell_slots_max={1: 4, 2: 3, 3: 2},
            spell_slots_remaining={1: 4, 2: 3, 3: 2},
        )

    def test_cantrip_always_if_known(self, wizard_state: SpellcasterState) -> None:
        assert can_cast_spell(wizard_state, SPELL_CATALOG["Fire Bolt"]) is True

    def test_cantrip_known_no_slots_irrelevant(self) -> None:
        """Cantrips can be cast even with zero spell slots."""
        state = SpellcasterState(
            spellcasting_ability=Ability.INT,
            spells_known=["Fire Bolt"],
            spell_slots_max={},
            spell_slots_remaining={},
        )
        assert can_cast_spell(state, SPELL_CATALOG["Fire Bolt"]) is True

    def test_has_slot(self, wizard_state: SpellcasterState) -> None:
        assert can_cast_spell(wizard_state, SPELL_CATALOG["Magic Missile"]) is True

    def test_no_slot(self, wizard_state: SpellcasterState) -> None:
        wizard_state.spell_slots_remaining = {1: 0, 2: 0, 3: 0}
        assert can_cast_spell(wizard_state, SPELL_CATALOG["Magic Missile"]) is False

    def test_unknown_spell(self, wizard_state: SpellcasterState) -> None:
        assert can_cast_spell(wizard_state, SPELL_CATALOG["Cure Wounds"]) is False

    def test_higher_slot_available(self, wizard_state: SpellcasterState) -> None:
        """Can cast a level-1 spell using a higher-level slot."""
        wizard_state.spell_slots_remaining = {1: 0, 2: 0, 3: 1}
        assert can_cast_spell(wizard_state, SPELL_CATALOG["Magic Missile"]) is True


class TestCastSpell:
    @pytest.fixture()
    def wizard_state(self) -> SpellcasterState:
        return SpellcasterState(
            spellcasting_ability=Ability.INT,
            spells_known=[
                "Fire Bolt", "Magic Missile", "Fireball",
                "Guidance", "Hold Person", "Bless",
            ],
            spell_slots_max={1: 4, 2: 3, 3: 2},
            spell_slots_remaining={1: 4, 2: 3, 3: 2},
        )

    def test_consume_slot(self, wizard_state: SpellcasterState) -> None:
        cast_spell(wizard_state, SPELL_CATALOG["Magic Missile"])
        assert wizard_state.spell_slots_remaining[1] == 3

    def test_cantrip_no_slot_consumed(self, wizard_state: SpellcasterState) -> None:
        cast_spell(wizard_state, SPELL_CATALOG["Fire Bolt"])
        assert wizard_state.spell_slots_remaining == {1: 4, 2: 3, 3: 2}

    def test_upcast(self, wizard_state: SpellcasterState) -> None:
        """Casting a level-1 spell at level-2 consumes a level-2 slot."""
        cast_spell(wizard_state, SPELL_CATALOG["Magic Missile"], slot_level=2)
        assert wizard_state.spell_slots_remaining[1] == 4
        assert wizard_state.spell_slots_remaining[2] == 2

    def test_concentration_replaces(self, wizard_state: SpellcasterState) -> None:
        cast_spell(wizard_state, SPELL_CATALOG["Bless"])
        assert wizard_state.concentration_spell == "Bless"
        cast_spell(wizard_state, SPELL_CATALOG["Hold Person"])
        assert wizard_state.concentration_spell == "Hold Person"

    def test_concentration_replace_logs_break(
        self, wizard_state: SpellcasterState, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Replacing a concentration spell should log that the old one was broken."""
        import logging

        cast_spell(wizard_state, SPELL_CATALOG["Bless"])
        with caplog.at_level(logging.INFO, logger="engine.spells"):
            cast_spell(wizard_state, SPELL_CATALOG["Hold Person"])
        assert "Bless" in caplog.text
        assert "Hold Person" in caplog.text
        assert wizard_state.concentration_spell == "Hold Person"

    def test_concentration_cantrip(self, wizard_state: SpellcasterState) -> None:
        cast_spell(wizard_state, SPELL_CATALOG["Guidance"])
        assert wizard_state.concentration_spell == "Guidance"

    def test_non_concentration_preserves(self, wizard_state: SpellcasterState) -> None:
        cast_spell(wizard_state, SPELL_CATALOG["Bless"])
        assert wizard_state.concentration_spell == "Bless"
        cast_spell(wizard_state, SPELL_CATALOG["Magic Missile"])
        assert wizard_state.concentration_spell == "Bless"

    def test_unknown_spell_raises(self, wizard_state: SpellcasterState) -> None:
        with pytest.raises(ValueError, match="not known"):
            cast_spell(wizard_state, SPELL_CATALOG["Cure Wounds"])

    def test_no_slot_raises(self, wizard_state: SpellcasterState) -> None:
        wizard_state.spell_slots_remaining = {1: 0, 2: 0, 3: 0}
        with pytest.raises(ValueError, match="No spell slots remaining"):
            cast_spell(wizard_state, SPELL_CATALOG["Magic Missile"])

    def test_slot_level_too_low_raises(self, wizard_state: SpellcasterState) -> None:
        with pytest.raises(ValueError, match="below spell level"):
            cast_spell(wizard_state, SPELL_CATALOG["Fireball"], slot_level=2)


# ---------------------------------------------------------------------------
# Spellcaster state creation
# ---------------------------------------------------------------------------


class TestCreateSpellcasterState:
    def test_wizard(self) -> None:
        state = create_spellcaster_state(CharacterClass.WIZARD, 5)
        assert state is not None
        assert state.spellcasting_ability == Ability.INT
        assert state.spell_slots_max == {1: 4, 2: 3, 3: 2}
        assert state.spell_slots_remaining == {1: 4, 2: 3, 3: 2}

    def test_cleric(self) -> None:
        state = create_spellcaster_state(CharacterClass.CLERIC, 1)
        assert state is not None
        assert state.spellcasting_ability == Ability.WIS
        assert state.spell_slots_max == {1: 2}

    def test_ranger(self) -> None:
        state = create_spellcaster_state(CharacterClass.RANGER, 5)
        assert state is not None
        assert state.spellcasting_ability == Ability.WIS
        assert state.spell_slots_max == {1: 4, 2: 2}

    def test_fighter_returns_none(self) -> None:
        assert create_spellcaster_state(CharacterClass.FIGHTER, 10) is None

    def test_rogue_returns_none(self) -> None:
        assert create_spellcaster_state(CharacterClass.ROGUE, 5) is None

    def test_barbarian_returns_none(self) -> None:
        assert create_spellcaster_state(CharacterClass.BARBARIAN, 1) is None

    def test_ranger_level_1_no_slots(self) -> None:
        state = create_spellcaster_state(CharacterClass.RANGER, 1)
        assert state is not None
        assert state.spell_slots_max == {}
        assert state.spell_slots_remaining == {}

    def test_spells_known_empty_by_default(self) -> None:
        state = create_spellcaster_state(CharacterClass.WIZARD, 1)
        assert state is not None
        assert state.spells_known == []

    def test_no_concentration_by_default(self) -> None:
        state = create_spellcaster_state(CharacterClass.WIZARD, 1)
        assert state is not None
        assert state.concentration_spell is None


# ---------------------------------------------------------------------------
# Restore spell slots
# ---------------------------------------------------------------------------


class TestRestoreSpellSlots:
    def test_restore_to_max(self) -> None:
        state = SpellcasterState(
            spellcasting_ability=Ability.INT,
            spell_slots_max={1: 4, 2: 3, 3: 2},
            spell_slots_remaining={1: 1, 2: 0, 3: 0},
            concentration_spell="Hold Person",
        )
        restore_spell_slots(state)
        assert state.spell_slots_remaining == {1: 4, 2: 3, 3: 2}
        assert state.concentration_spell is None

    def test_already_full(self) -> None:
        state = SpellcasterState(
            spellcasting_ability=Ability.WIS,
            spell_slots_max={1: 2},
            spell_slots_remaining={1: 2},
        )
        restore_spell_slots(state)
        assert state.spell_slots_remaining == {1: 2}


# ---------------------------------------------------------------------------
# Spell catalog
# ---------------------------------------------------------------------------


class TestSpellCatalog:
    def test_all_spells_valid(self) -> None:
        """Every spell in the catalog is a valid Spell instance."""
        assert len(SPELL_CATALOG) >= 20
        for name, spell in SPELL_CATALOG.items():
            assert isinstance(spell, Spell)
            assert spell.name == name

    def test_damage_spells_have_type(self) -> None:
        """Every spell with damage_dice also has a damage_type."""
        for name, spell in SPELL_CATALOG.items():
            if spell.damage_dice is not None:
                assert spell.damage_type is not None, (
                    f"'{name}' has damage_dice but no damage_type"
                )

    def test_healing_spells_have_dice(self) -> None:
        """Healing spells in the catalog have healing_dice set."""
        healing_names = {"Cure Wounds", "Healing Word"}
        for name in healing_names:
            spell = SPELL_CATALOG[name]
            assert spell.healing_dice is not None, f"'{name}' missing healing_dice"

    def test_cantrips_are_level_0(self) -> None:
        cantrip_names = {"Fire Bolt", "Sacred Flame", "Ray of Frost", "Light", "Guidance"}
        for name in cantrip_names:
            assert SPELL_CATALOG[name].level == 0

    def test_concentration_spells(self) -> None:
        conc_names = {"Guidance", "Hunter's Mark", "Bless", "Hold Person"}
        for name in conc_names:
            assert SPELL_CATALOG[name].concentration is True, (
                f"'{name}' should require concentration"
            )

    def test_non_concentration_spells(self) -> None:
        non_conc = {"Fire Bolt", "Magic Missile", "Fireball", "Cure Wounds"}
        for name in non_conc:
            assert SPELL_CATALOG[name].concentration is False

    def test_higher_level_dice_present(self) -> None:
        """Spells with higher_level_dice are documented in the catalog."""
        expected = {"Magic Missile", "Cure Wounds", "Healing Word", "Fireball", "Lightning Bolt"}
        for name in expected:
            assert SPELL_CATALOG[name].higher_level_dice is not None, (
                f"'{name}' missing higher_level_dice"
            )

    def test_spell_catalog_count(self) -> None:
        assert len(SPELL_CATALOG) == 21


# ---------------------------------------------------------------------------
# Slot table integrity
# ---------------------------------------------------------------------------


class TestSlotTables:
    def test_full_caster_covers_all_levels(self) -> None:
        for level in range(1, 21):
            assert level in FULL_CASTER_SLOTS

    def test_half_caster_covers_all_levels(self) -> None:
        for level in range(1, 21):
            assert level in HALF_CASTER_SLOTS

    def test_full_caster_level_7_has_4th_level_slots(self) -> None:
        assert 4 in FULL_CASTER_SLOTS[7]
        assert FULL_CASTER_SLOTS[7][4] == 1

    def test_full_caster_level_8_has_4th_level_slots(self) -> None:
        assert FULL_CASTER_SLOTS[8][4] == 2

    def test_half_caster_level_1_empty(self) -> None:
        assert HALF_CASTER_SLOTS[1] == {}

    def test_slot_counts_never_decrease(self) -> None:
        """Slot counts should be monotonically non-decreasing across levels."""
        for table in (FULL_CASTER_SLOTS, HALF_CASTER_SLOTS):
            for level in range(2, 21):
                for spell_level, count in table[level].items():
                    prev = table[level - 1].get(spell_level, 0)
                    assert count >= prev, (
                        f"Level {level}, spell level {spell_level}: "
                        f"{count} < previous {prev}"
                    )
