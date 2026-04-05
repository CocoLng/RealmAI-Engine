"""Tests for engine/combat.py — combat system.

Covers initiative, turn management, attack resolution, spell resolution,
death saves, damage, and healing.
"""

import pytest

from engine.character import (
    Ability,
    AbilityScores,
    CharacterClass,
    Race,
    apply_racial_bonuses,
    create_character,
)
from engine.combat import (
    CombatSide,
    Combatant,
    advance_turn,
    apply_damage,
    apply_healing,
    compute_attack_modifier,
    compute_damage_modifier,
    get_current_combatant,
    is_combat_over,
    resolve_attack,
    resolve_death_save,
    resolve_spell,
    roll_initiative,
    start_combat,
)
from engine.conditions import ActiveCondition, ConditionType, has_condition
from engine.dice import DiceResult
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Weapon,
    WeaponCategory,
    WeaponProperty,
    add_item,
    create_inventory,
    equip_item,
    ITEM_CATALOG,
)
from engine.spells import (
    SpellcasterState,
    SPELL_CATALOG,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_scores() -> AbilityScores:
    return AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8)


@pytest.fixture()
def fighter(sample_scores: AbilityScores) -> Combatant:
    scores = apply_racial_bonuses(sample_scores, Race.HUMAN)
    char = create_character("Arden", Race.HUMAN, CharacterClass.FIGHTER, scores)
    inv = create_inventory()
    longsword = ITEM_CATALOG["Longsword"]
    inv = add_item(inv, longsword)
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
    return Combatant(name="Arden", side=CombatSide.PLAYER, character=char, inventory=inv)


@pytest.fixture()
def goblin() -> Combatant:
    scores = AbilityScores(STR=8, DEX=14, CON=10, INT=10, WIS=8, CHA=8)
    scores = apply_racial_bonuses(scores, Race.HALFLING)
    char = create_character("Goblin", Race.HALFLING, CharacterClass.ROGUE, scores)
    inv = create_inventory()
    scimitar = Weapon(
        name="Scimitar",
        damage_dice="1d6",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        weight=3.0,
        properties=[WeaponProperty.FINESSE, WeaponProperty.LIGHT],
    )
    inv = add_item(inv, scimitar)
    inv = equip_item(inv, "Scimitar", EquipmentSlot.MAIN_HAND)
    return Combatant(name="Goblin", side=CombatSide.ENEMY, character=char, inventory=inv)


@pytest.fixture()
def wizard() -> Combatant:
    scores = AbilityScores(STR=8, DEX=14, CON=12, INT=16, WIS=12, CHA=10)
    scores = apply_racial_bonuses(scores, Race.HUMAN)
    char = create_character("Elara", Race.HUMAN, CharacterClass.WIZARD, scores)
    inv = create_inventory()
    state = SpellcasterState(
        spellcasting_ability=Ability.INT,
        spells_known=["Fire Bolt", "Magic Missile", "Cure Wounds", "Burning Hands", "Hold Person"],
        spell_slots_max={1: 2, 2: 1},
        spell_slots_remaining={1: 2, 2: 1},
    )
    return Combatant(
        name="Elara",
        side=CombatSide.PLAYER,
        character=char,
        inventory=inv,
        spellcaster=state,
    )


def _make_roll(total: int) -> DiceResult:
    """Helper to create a DiceResult with a given total."""
    return DiceResult(expression="1d20", rolls=[total], total=total)


# ---------------------------------------------------------------------------
# TestRollInitiative
# ---------------------------------------------------------------------------


class TestRollInitiative:
    def test_sets_initiative(self, fighter: Combatant, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(15))
        roll_initiative(fighter)
        # DEX is 15 (14 base +1 human), mod = +2
        assert fighter.initiative == 15 + 2

    def test_low_roll(self, fighter: Combatant, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(1))
        roll_initiative(fighter)
        assert fighter.initiative == 1 + 2


# ---------------------------------------------------------------------------
# TestStartCombat
# ---------------------------------------------------------------------------


class TestStartCombat:
    def test_sorts_by_initiative(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = 0

        def mock_roll(_expr: str) -> DiceResult:
            nonlocal call_count
            call_count += 1
            # Fighter rolls 10, Goblin rolls 18
            if call_count == 1:
                return _make_roll(10)
            return _make_roll(18)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        state = start_combat([fighter, goblin])
        # Goblin should be first (higher initiative)
        assert state.combatants[0].name == "Goblin"
        assert state.combatants[1].name == "Arden"

    def test_round_starts_at_1(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        assert state.round_number == 1
        assert state.current_turn_index == 0
        assert state.is_active is True


# ---------------------------------------------------------------------------
# TestGetCurrentCombatant
# ---------------------------------------------------------------------------


class TestGetCurrentCombatant:
    def test_returns_first(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        current = get_current_combatant(state)
        assert current.name == state.combatants[0].name


# ---------------------------------------------------------------------------
# TestAdvanceTurn
# ---------------------------------------------------------------------------


class TestAdvanceTurn:
    def test_advances_index(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        assert state.current_turn_index == 0
        advance_turn(state)
        assert state.current_turn_index == 1

    def test_skips_dead(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Create a 3-combatant fight: fighter, goblin, another enemy
        scores2 = AbilityScores(STR=8, DEX=12, CON=10, INT=10, WIS=8, CHA=8)
        scores2 = apply_racial_bonuses(scores2, Race.HALFLING)
        char2 = create_character("Goblin2", Race.HALFLING, CharacterClass.ROGUE, scores2)
        goblin2 = Combatant(
            name="Goblin2", side=CombatSide.ENEMY, character=char2, inventory=create_inventory()
        )

        call_count = 0

        def mock_roll(_expr: str) -> DiceResult:
            nonlocal call_count
            call_count += 1
            # Force order: fighter(20), goblin(15), goblin2(10)
            if call_count == 1:
                return _make_roll(20)
            if call_count == 2:
                return _make_roll(15)
            return _make_roll(10)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        state = start_combat([fighter, goblin, goblin2])

        # Kill the middle combatant (goblin at index 1)
        state.combatants[1].is_alive = False

        # Advance from fighter (index 0) — should skip dead goblin, go to goblin2 (index 2)
        advance_turn(state)
        assert state.current_turn_index == 2
        assert get_current_combatant(state).name == "Goblin2"

    def test_wraps_and_increments_round(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        assert state.round_number == 1

        advance_turn(state)  # index 0 -> 1
        assert state.round_number == 1

        advance_turn(state)  # index 1 -> 0 (wrap)
        assert state.round_number == 2


# ---------------------------------------------------------------------------
# TestIsCombatOver
# ---------------------------------------------------------------------------


class TestIsCombatOver:
    def test_all_enemies_dead(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        goblin.is_alive = False
        assert is_combat_over(state) is True

    def test_all_players_dead(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        fighter.is_alive = False
        assert is_combat_over(state) is True

    def test_not_over(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        assert is_combat_over(state) is False


# ---------------------------------------------------------------------------
# TestComputeAttackModifier
# ---------------------------------------------------------------------------


class TestComputeAttackModifier:
    def test_melee_uses_str(self, fighter: Combatant) -> None:
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        mod = compute_attack_modifier(fighter, longsword)
        # STR 17 (16+1 human) -> +3, prof +2 = 5
        assert mod == 5

    def test_ranged_uses_dex(self, fighter: Combatant) -> None:
        longbow = ITEM_CATALOG["Longbow"]
        assert isinstance(longbow, Weapon)
        mod = compute_attack_modifier(fighter, longbow)
        # DEX 15 (14+1 human) -> +2, prof +2 = 4
        assert mod == 4

    def test_finesse_uses_higher(self, goblin: Combatant) -> None:
        scimitar: Weapon = goblin.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        mod = compute_attack_modifier(goblin, scimitar)
        # Goblin: STR 8 -> -1, DEX 16 (14+2 halfling) -> +3. Finesse uses DEX. Prof +2 = 5
        assert mod == 5


# ---------------------------------------------------------------------------
# TestComputeDamageModifier
# ---------------------------------------------------------------------------


class TestComputeDamageModifier:
    def test_melee_uses_str(self, fighter: Combatant) -> None:
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        mod = compute_damage_modifier(fighter, longsword)
        # STR 17 -> +3
        assert mod == 3

    def test_finesse_uses_higher(self, goblin: Combatant) -> None:
        scimitar: Weapon = goblin.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        mod = compute_damage_modifier(goblin, scimitar)
        # DEX 16 -> +3
        assert mod == 3


# ---------------------------------------------------------------------------
# TestResolveAttack
# ---------------------------------------------------------------------------


class TestResolveAttack:
    def test_hit(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = 0

        def mock_roll(expr: str) -> DiceResult:
            nonlocal call_count
            call_count += 1
            if expr == "1d20":
                return _make_roll(15)
            # Damage roll: 1d8
            return DiceResult(expression=expr, rolls=[6], total=6)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        result = resolve_attack(fighter, goblin, longsword)
        assert result.hit is True
        assert result.critical is False
        assert result.damage > 0

    def test_miss(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(2))
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        # Set goblin AC high enough to guarantee miss
        goblin.character.ac = 25
        result = resolve_attack(fighter, goblin, longsword)
        assert result.hit is False
        assert result.damage == 0

    def test_critical_hit(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = 0

        def mock_roll(expr: str) -> DiceResult:
            nonlocal call_count
            call_count += 1
            if expr == "1d20":
                return _make_roll(20)
            # Crit damage: 2d8 (doubled dice)
            if expr == "2d8":
                return DiceResult(expression=expr, rolls=[6, 5], total=11)
            return _make_roll(5)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        result = resolve_attack(fighter, goblin, longsword)
        assert result.hit is True
        assert result.critical is True
        # Damage = 11 (dice) + 3 (STR mod) = 14
        assert result.damage == 14

    def test_nat_1_auto_miss(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(1))
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        goblin.character.ac = 0  # Even AC 0, nat 1 auto-misses
        result = resolve_attack(fighter, goblin, longsword)
        assert result.hit is False
        assert result.damage == 0

    def test_auto_crit_on_unconscious(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.conditions import apply_condition as cond_apply

        cond_apply(
            goblin.conditions,
            ActiveCondition(condition_type=ConditionType.UNCONSCIOUS, source="test"),
        )

        call_count = 0

        def mock_roll(expr: str) -> DiceResult:
            nonlocal call_count
            call_count += 1
            if expr == "1d20":
                return _make_roll(15)  # Hits
            if expr == "2d8":  # Doubled dice from auto-crit
                return DiceResult(expression=expr, rolls=[4, 4], total=8)
            return _make_roll(5)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        result = resolve_attack(fighter, goblin, longsword)
        assert result.hit is True
        assert result.critical is True


# ---------------------------------------------------------------------------
# TestResolveSpell
# ---------------------------------------------------------------------------


class TestResolveSpell:
    def test_damage_spell(
        self, wizard: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = 0

        def mock_roll(expr: str) -> DiceResult:
            nonlocal call_count
            call_count += 1
            # Fire Bolt cantrip: 1d10 at level 1
            if "d10" in expr:
                return DiceResult(expression=expr, rolls=[7], total=7)
            return _make_roll(10)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        fire_bolt = SPELL_CATALOG["Fire Bolt"]
        hp_before = goblin.character.hp
        result = resolve_spell(wizard, fire_bolt, target=goblin)
        assert result.damage == 7
        assert result.spell_name == "Fire Bolt"
        assert goblin.character.hp == hp_before - 7

    def test_healing_spell(
        self, wizard: Combatant, fighter: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Add Cure Wounds to wizard's known spells (already there)
        fighter.character.hp = 5  # Wounded

        def mock_roll(expr: str) -> DiceResult:
            if "d8" in expr:
                return DiceResult(expression=expr, rolls=[6], total=6)
            return _make_roll(10)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        cure_wounds = SPELL_CATALOG["Cure Wounds"]
        result = resolve_spell(wizard, cure_wounds, target=fighter, slot_level=1)
        assert result.healing == 6
        assert result.slot_used == 1
        assert fighter.character.hp == 11

    def test_save_spell_target_fails(
        self, wizard: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = 0

        def mock_roll(expr: str) -> DiceResult:
            nonlocal call_count
            call_count += 1
            if "d6" in expr:
                return DiceResult(expression=expr, rolls=[4, 3, 5], total=12)
            # Save roll: low roll, will fail
            return _make_roll(2)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        burning_hands = SPELL_CATALOG["Burning Hands"]
        result = resolve_spell(wizard, burning_hands, target=goblin, slot_level=1)
        assert result.target_failed_save is True  # Target failed save
        assert result.damage == 12

    def test_save_spell_target_saves(
        self, wizard: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = 0

        def mock_roll(expr: str) -> DiceResult:
            nonlocal call_count
            call_count += 1
            if "d6" in expr:
                return DiceResult(expression=expr, rolls=[4, 3, 5], total=12)
            # Save roll: high roll
            return _make_roll(19)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        burning_hands = SPELL_CATALOG["Burning Hands"]
        result = resolve_spell(wizard, burning_hands, target=goblin, slot_level=1)
        assert result.target_failed_save is False  # Target saved
        assert result.damage == 6  # Halved from 12

    def test_condition_spell(
        self, wizard: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Hold Person: target fails WIS save -> Paralyzed
        def mock_roll(_expr: str) -> DiceResult:
            return _make_roll(2)  # Low roll for save

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        hold_person = SPELL_CATALOG["Hold Person"]
        result = resolve_spell(wizard, hold_person, target=goblin, slot_level=2)
        assert result.condition_applied == "Paralyzed"
        assert has_condition(goblin.conditions, ConditionType.PARALYZED)


# ---------------------------------------------------------------------------
# TestDeathSave
# ---------------------------------------------------------------------------


class TestDeathSave:
    def _make_downed_player(self) -> Combatant:
        scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
        scores = apply_racial_bonuses(scores, Race.HUMAN)
        char = create_character("Downed", Race.HUMAN, CharacterClass.FIGHTER, scores)
        char.hp = 0
        c = Combatant(name="Downed", side=CombatSide.PLAYER, character=char, inventory=create_inventory())
        from engine.conditions import apply_condition as cond_apply

        cond_apply(
            c.conditions,
            ActiveCondition(condition_type=ConditionType.UNCONSCIOUS, source="damage"),
        )
        return c

    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = self._make_downed_player()
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(12))
        result = resolve_death_save(c)
        assert result.success is True
        assert c.death_saves.successes == 1
        assert c.death_saves.failures == 0

    def test_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = self._make_downed_player()
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(5))
        result = resolve_death_save(c)
        assert result.success is False
        assert c.death_saves.failures == 1

    def test_nat_20_revive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = self._make_downed_player()
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(20))
        result = resolve_death_save(c)
        assert result.revived is True
        assert c.character.hp == 1
        assert not has_condition(c.conditions, ConditionType.UNCONSCIOUS)
        assert c.death_saves.successes == 0  # Reset
        assert c.death_saves.failures == 0

    def test_nat_1_double_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = self._make_downed_player()
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(1))
        result = resolve_death_save(c)
        assert result.success is False
        assert c.death_saves.failures == 2

    def test_stabilize_at_3_successes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = self._make_downed_player()
        c.death_saves.successes = 2
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(15))
        result = resolve_death_save(c)
        assert result.stabilized is True
        assert c.death_saves.successes == 3

    def test_die_at_3_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = self._make_downed_player()
        c.death_saves.failures = 2
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(5))
        result = resolve_death_save(c)
        assert result.died is True
        assert c.is_alive is False


# ---------------------------------------------------------------------------
# TestApplyDamage
# ---------------------------------------------------------------------------


class TestApplyDamage:
    def test_reduces_hp(self, fighter: Combatant) -> None:
        hp_before = fighter.character.hp
        apply_damage(fighter, 5)
        assert fighter.character.hp == hp_before - 5

    def test_clamp_at_zero(self, fighter: Combatant) -> None:
        apply_damage(fighter, 9999)
        assert fighter.character.hp == 0

    def test_player_unconscious_at_zero(self, fighter: Combatant) -> None:
        apply_damage(fighter, 9999)
        assert fighter.character.hp == 0
        assert has_condition(fighter.conditions, ConditionType.UNCONSCIOUS)
        assert fighter.is_alive is True  # Players don't die at 0

    def test_enemy_dies_at_zero(self, goblin: Combatant) -> None:
        apply_damage(goblin, 9999)
        assert goblin.character.hp == 0
        assert goblin.is_alive is False


# ---------------------------------------------------------------------------
# TestApplyHealing
# ---------------------------------------------------------------------------


class TestApplyHealing:
    def test_heals_hp(self, fighter: Combatant) -> None:
        fighter.character.hp = 5
        apply_healing(fighter, 3)
        assert fighter.character.hp == 8

    def test_caps_at_max(self, fighter: Combatant) -> None:
        max_hp = fighter.character.max_hp
        fighter.character.hp = max_hp - 1
        apply_healing(fighter, 100)
        assert fighter.character.hp == max_hp

    def test_removes_unconscious(self, fighter: Combatant) -> None:
        # Bring to 0 HP (sets unconscious)
        apply_damage(fighter, 9999)
        assert has_condition(fighter.conditions, ConditionType.UNCONSCIOUS)
        assert fighter.character.hp == 0

        # Heal
        apply_healing(fighter, 5)
        assert fighter.character.hp == 5
        assert not has_condition(fighter.conditions, ConditionType.UNCONSCIOUS)
        assert fighter.death_saves.successes == 0
        assert fighter.death_saves.failures == 0
