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
    ActionBudget,
    CombatSide,
    Combatant,
    TrivialResolveResult,
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
    trivial_resolve,
)
from world.npc import NPC, NPCDisposition
from engine.conditions import ActiveCondition, ConditionType, has_condition
from engine.dice import D20CheckResult, DiceResult, RollOutcome, _compute_outcome
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


def _mock_roll_check(natural_roll: int):
    """Return a mock roll_check that always uses the given natural d20 value."""

    def _inner(expr: str, dc: int) -> D20CheckResult:
        cleaned = expr.replace(" ", "")
        mod_str = cleaned.replace("1d20", "")
        modifier = int(mod_str) if mod_str else 0
        total = natural_roll + modifier
        margin = total - dc
        outcome = _compute_outcome(natural_roll, margin)
        return D20CheckResult(
            expression=cleaned,
            rolls=[natural_roll],
            modifier=modifier,
            total=total,
            dc=dc,
            outcome=outcome,
            margin=margin,
        )

    return _inner


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
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(15))
        monkeypatch.setattr(
            "engine.combat.roll",
            lambda expr: DiceResult(expression=expr, rolls=[6], total=6),
        )
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        result = resolve_attack(fighter, goblin, longsword)
        assert result.hit is True
        assert result.critical is False
        assert result.damage > 0
        assert result.outcome in (RollOutcome.NEAR_SUCCESS, RollOutcome.SUCCESS)

    def test_miss(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(2))
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        # Set goblin AC high enough to guarantee miss
        goblin.character.ac = 25
        result = resolve_attack(fighter, goblin, longsword)
        assert result.hit is False
        assert result.damage == 0
        assert result.outcome in (RollOutcome.FAILURE, RollOutcome.NEAR_FAILURE)

    def test_critical_hit(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(20))

        def mock_roll(expr: str) -> DiceResult:
            if "2d8" in expr:
                return DiceResult(expression=expr, rolls=[6, 5], total=11)
            return DiceResult(expression=expr, rolls=[5], total=5)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        result = resolve_attack(fighter, goblin, longsword)
        assert result.hit is True
        assert result.critical is True
        assert result.outcome == RollOutcome.CRITICAL_SUCCESS
        # Damage = 11 (dice) + 3 (STR mod) = 14
        assert result.damage == 14

    def test_nat_1_auto_miss(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(1))
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        goblin.character.ac = 0  # Even AC 0, nat 1 auto-misses
        result = resolve_attack(fighter, goblin, longsword)
        assert result.hit is False
        assert result.damage == 0
        assert result.outcome == RollOutcome.CRITICAL_FAILURE

    def test_auto_crit_on_unconscious(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.conditions import apply_condition as cond_apply

        cond_apply(
            goblin.conditions,
            ActiveCondition(condition_type=ConditionType.UNCONSCIOUS, source="test"),
        )

        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(15))

        def mock_roll(expr: str) -> DiceResult:
            if "2d8" in expr:
                return DiceResult(expression=expr, rolls=[4, 4], total=8)
            return DiceResult(expression=expr, rolls=[5], total=5)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        longsword: Weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]  # type: ignore[assignment]
        result = resolve_attack(fighter, goblin, longsword)
        assert result.hit is True
        assert result.critical is True
        assert result.outcome == RollOutcome.CRITICAL_SUCCESS


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
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(2))
        monkeypatch.setattr(
            "engine.combat.roll",
            lambda expr: DiceResult(expression=expr, rolls=[4, 3, 5], total=12)
            if "d6" in expr
            else _make_roll(10),
        )
        burning_hands = SPELL_CATALOG["Burning Hands"]
        result = resolve_spell(wizard, burning_hands, target=goblin, slot_level=1)
        assert result.target_failed_save is True  # Target failed save
        assert result.damage == 12

    def test_save_spell_target_saves(
        self, wizard: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(19))
        monkeypatch.setattr(
            "engine.combat.roll",
            lambda expr: DiceResult(expression=expr, rolls=[4, 3, 5], total=12)
            if "d6" in expr
            else _make_roll(10),
        )
        burning_hands = SPELL_CATALOG["Burning Hands"]
        result = resolve_spell(wizard, burning_hands, target=goblin, slot_level=1)
        assert result.target_failed_save is False  # Target saved
        assert result.damage == 6  # Halved from 12

    def test_condition_spell(
        self, wizard: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Hold Person: target fails WIS save -> Paralyzed
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(2))
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(2))
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
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(12))
        result = resolve_death_save(c)
        assert result.success is True
        assert result.outcome == RollOutcome.NEAR_SUCCESS
        assert c.death_saves.successes == 1
        assert c.death_saves.failures == 0

    def test_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = self._make_downed_player()
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(5))
        result = resolve_death_save(c)
        assert result.success is False
        assert result.outcome == RollOutcome.FAILURE
        assert c.death_saves.failures == 1

    def test_nat_20_revive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = self._make_downed_player()
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(20))
        result = resolve_death_save(c)
        assert result.revived is True
        assert result.outcome == RollOutcome.CRITICAL_SUCCESS
        assert c.character.hp == 1
        assert not has_condition(c.conditions, ConditionType.UNCONSCIOUS)
        assert c.death_saves.successes == 0  # Reset
        assert c.death_saves.failures == 0

    def test_nat_1_double_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = self._make_downed_player()
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(1))
        result = resolve_death_save(c)
        assert result.success is False
        assert result.outcome == RollOutcome.CRITICAL_FAILURE
        assert c.death_saves.failures == 2

    def test_stabilize_at_3_successes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = self._make_downed_player()
        c.death_saves.successes = 2
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(15))
        result = resolve_death_save(c)
        assert result.stabilized is True
        assert c.death_saves.successes == 3

    def test_die_at_3_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = self._make_downed_player()
        c.death_saves.failures = 2
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(5))
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


# ---------------------------------------------------------------------------
# Trivial NPC resolution (Lot E)
# ---------------------------------------------------------------------------


def _make_commoner(name: str = "Jeanne", hp: int = 4) -> NPC:
    return NPC(
        name=name,
        race=Race.HUMAN,
        char_class=None,
        level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=hp,
        max_hp=hp,
        ac=10,
        disposition=NPCDisposition.FRIENDLY,
    )


class TestTrivialResolve:
    def test_hit_kills_low_hp_target(self, fighter: Combatant, monkeypatch) -> None:
        npc = _make_commoner(hp=4)
        # Force a guaranteed hit (nat 15) and damage roll of 6.
        from engine import combat as combat_mod

        def fake_roll_check(expr: str, dc: int):
            return D20CheckResult(
                expression=expr, rolls=[15], modifier=0, total=15 + 5,
                dc=dc, outcome=RollOutcome.SUCCESS, margin=20 - dc,
            )

        def fake_roll(expr: str):
            return DiceResult(expression=expr, rolls=[6], modifier=0, total=6)

        monkeypatch.setattr(combat_mod, "roll_check", fake_roll_check)
        monkeypatch.setattr(combat_mod, "roll", fake_roll)

        weapon = ITEM_CATALOG["Longsword"]
        assert isinstance(weapon, Weapon)
        result = trivial_resolve(fighter.character, npc, weapon=weapon)

        assert isinstance(result, TrivialResolveResult)
        assert result.hit is True
        assert result.target_killed is True
        assert npc.is_alive is False
        assert npc.hp == 0
        assert "décisif" in result.description.lower() or "mort" in result.description.lower()

    def test_hit_survives_when_hp_higher(self, fighter: Combatant, monkeypatch) -> None:
        npc = _make_commoner(hp=20)
        from engine import combat as combat_mod

        monkeypatch.setattr(
            combat_mod, "roll_check",
            lambda expr, dc: D20CheckResult(
                expression=expr, rolls=[15], modifier=0, total=20,
                dc=dc, outcome=RollOutcome.SUCCESS, margin=20 - dc,
            ),
        )
        monkeypatch.setattr(
            combat_mod, "roll",
            lambda expr: DiceResult(expression=expr, rolls=[3], modifier=0, total=3),
        )

        weapon = ITEM_CATALOG["Longsword"]
        assert isinstance(weapon, Weapon)
        result = trivial_resolve(fighter.character, npc, weapon=weapon)
        assert result.hit is True
        assert result.target_killed is False
        assert npc.is_alive is True
        assert npc.hp < npc.max_hp

    def test_miss_on_nat_one(self, fighter: Combatant, monkeypatch) -> None:
        npc = _make_commoner(hp=4)
        from engine import combat as combat_mod

        monkeypatch.setattr(
            combat_mod, "roll_check",
            lambda expr, dc: D20CheckResult(
                expression=expr, rolls=[1], modifier=0, total=1,
                dc=dc, outcome=RollOutcome.CRITICAL_FAILURE, margin=1 - dc,
            ),
        )

        weapon = ITEM_CATALOG["Longsword"]
        assert isinstance(weapon, Weapon)
        result = trivial_resolve(fighter.character, npc, weapon=weapon)
        assert result.hit is False
        assert result.damage == 0
        assert result.target_killed is False
        assert npc.is_alive is True
        assert npc.hp == 4

    def test_npc_kill_helper_is_idempotent(self) -> None:
        npc = _make_commoner(hp=4)
        npc.kill()
        npc.kill()
        assert npc.is_alive is False
        assert npc.hp == 0


# ---------------------------------------------------------------------------
# advance_turn skip fled + check_combat_end FLED
# ---------------------------------------------------------------------------


class TestFledCombatant:
    """Tests for fled-combatant handling in advance_turn and check_combat_end."""

    def test_advance_turn_skips_fled_combatant(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """advance_turn skips combatants that have fled."""
        from engine.combat import CombatState

        scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
        scores = apply_racial_bonuses(scores, Race.ELF)
        char3 = create_character("Rôdeur", Race.ELF, CharacterClass.ROGUE, scores)
        ranger = Combatant(
            name="Rôdeur",
            side=CombatSide.PLAYER,
            character=char3,
            inventory=create_inventory(),
        )
        ranger.fled = True

        # Build state manually: fighter(0) → ranger(1, fled) → goblin(2)
        state = CombatState(
            combatants=[fighter, ranger, goblin],
            current_turn_index=0,
            is_active=True,
        )
        new_state = advance_turn(state)
        # Should skip fled ranger (index 1) and land on goblin (index 2)
        assert new_state.combatants[new_state.current_turn_index].name == goblin.name

    def test_check_combat_end_returns_fled_when_all_pcs_fled(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FLED returned when all PCs have fled (alive) and enemies still standing."""
        from engine.combat import CombatEndReason, CombatState, check_combat_end

        fighter.fled = True
        state = CombatState(
            combatants=[fighter, goblin],
            current_turn_index=0,
            is_active=True,
        )
        assert check_combat_end(state) == CombatEndReason.FLED

    def test_check_combat_end_returns_defeat_when_all_pcs_dead_none_fled(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEFEAT returned when all PCs are dead (not fled) and enemies still standing."""
        from engine.combat import CombatEndReason, CombatState, check_combat_end

        fighter.is_alive = False
        state = CombatState(
            combatants=[fighter, goblin],
            current_turn_index=0,
            is_active=True,
        )
        assert check_combat_end(state) == CombatEndReason.DEFEAT

    def test_check_combat_end_returns_fled_when_one_pc_died_other_fled(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FLED returned when one PC died and the other fled — not DEFEAT.

        Regression: all(c.fled for c in players) was wrong because dead PCs
        have fled=False. Must check only alive PCs.
        """
        from engine.combat import CombatEndReason, CombatState, check_combat_end

        scores = AbilityScores(STR=10, DEX=14, CON=10, INT=10, WIS=10, CHA=10)
        scores = apply_racial_bonuses(scores, Race.ELF)
        char2 = create_character("Rôdeur", Race.ELF, CharacterClass.ROGUE, scores)
        ranger = Combatant(
            name="Rôdeur",
            side=CombatSide.PLAYER,
            character=char2,
            inventory=create_inventory(),
        )
        fighter.is_alive = False  # PC 1 died
        ranger.fled = True        # PC 2 successfully fled
        state = CombatState(
            combatants=[fighter, ranger, goblin],
            current_turn_index=0,
            is_active=True,
        )
        assert check_combat_end(state) == CombatEndReason.FLED


# ---------------------------------------------------------------------------
# ActionBudget — weapon_swapped_this_turn
# ---------------------------------------------------------------------------


class TestActionBudgetWeaponSwap:
    """weapon_swapped_this_turn field on ActionBudget."""

    def test_defaults_false(self) -> None:
        budget = ActionBudget()
        assert budget.weapon_swapped_this_turn is False

    def test_reset_clears_flag(self) -> None:
        budget = ActionBudget(weapon_swapped_this_turn=True)
        budget.reset_for_new_turn(30)
        assert budget.weapon_swapped_this_turn is False
