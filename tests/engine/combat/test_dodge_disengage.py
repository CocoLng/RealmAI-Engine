"""Tests for the Dodge (DEFEND) condition and its combat wiring (audit C2).

The DEFEND action applies the DODGING condition: until the start of the
dodger's next turn, attack rolls against them have disadvantage (SRD 5e
Dodge action). Covers:

1. The ``ConditionType.DODGING`` member and the
   ``imposes_disadvantage_on_attackers`` lookup helper.
2. ``resolve_attack`` / ``resolve_npc_attack`` rolling with disadvantage
   against a dodging defender.
3. ``advance_turn`` clearing DODGING at the start of the dodger's next
   turn (not before — the whole point is surviving the enemy turns).
"""

from __future__ import annotations

import pytest

from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    apply_racial_bonuses,
    create_character,
)
from engine.combat import (
    CombatSide,
    CombatState,
    Combatant,
    advance_turn,
    resolve_attack,
    resolve_npc_attack,
)
from engine.conditions import (
    ActiveCondition,
    ConditionType,
    has_condition,
    imposes_disadvantage_on_attackers,
)
from engine.dice import D20CheckResult, DiceResult, _compute_outcome
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Weapon,
    WeaponCategory,
    add_item,
    create_inventory,
    equip_item,
    ITEM_CATALOG,
)
from engine.npc_stat_block import NPCAttack


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def fighter() -> Combatant:
    scores = apply_racial_bonuses(
        AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8),
        Race.HUMAN,
    )
    char = create_character("Arden", Race.HUMAN, CharacterClass.FIGHTER, scores)
    inv = create_inventory()
    inv = add_item(inv, ITEM_CATALOG["Longsword"])
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
    return Combatant(
        name="Arden", side=CombatSide.PLAYER, character=char, inventory=inv,
    )


@pytest.fixture()
def goblin() -> Combatant:
    scores = apply_racial_bonuses(
        AbilityScores(STR=8, DEX=14, CON=10, INT=10, WIS=8, CHA=8),
        Race.HALFLING,
    )
    char = create_character("Goblin", Race.HALFLING, CharacterClass.ROGUE, scores)
    inv = create_inventory()
    scimitar = Weapon(
        name="Scimitar",
        damage_dice="1d6",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        weight=3.0,
    )
    inv = add_item(inv, scimitar)
    inv = equip_item(inv, "Scimitar", EquipmentSlot.MAIN_HAND)
    return Combatant(
        name="Goblin", side=CombatSide.ENEMY, character=char, inventory=inv,
    )


def _dodging() -> ActiveCondition:
    return ActiveCondition(condition_type=ConditionType.DODGING, source="defend")


def _sequenced_roll_check(naturals: list[int]):
    """Mock roll_check returning successive natural d20 values."""
    rolls = iter(naturals)
    calls: list[int] = []

    def _inner(expr: str, dc: int) -> D20CheckResult:
        natural = next(rolls)
        calls.append(natural)
        cleaned = expr.replace(" ", "")
        mod_str = cleaned.replace("1d20", "")
        modifier = int(mod_str) if mod_str else 0
        total = natural + modifier
        margin = total - dc
        return D20CheckResult(
            expression=cleaned,
            rolls=[natural],
            modifier=modifier,
            total=total,
            dc=dc,
            outcome=_compute_outcome(natural, margin),
            margin=margin,
        )

    return _inner, calls


# ---------------------------------------------------------------------------
# Condition + helper
# ---------------------------------------------------------------------------


class TestDodgingCondition:
    def test_dodging_member_exists(self) -> None:
        assert ConditionType.DODGING.value == "Dodging"

    def test_helper_true_when_dodging(self) -> None:
        assert imposes_disadvantage_on_attackers([_dodging()]) is True

    def test_helper_false_without_dodging(self) -> None:
        poisoned = ActiveCondition(condition_type=ConditionType.POISONED)
        assert imposes_disadvantage_on_attackers([]) is False
        assert imposes_disadvantage_on_attackers([poisoned]) is False


# ---------------------------------------------------------------------------
# Attack-resolution wiring
# ---------------------------------------------------------------------------


class TestDodgingImposesDisadvantage:
    def test_pc_attack_against_dodging_defender_takes_worst_roll(
        self, fighter: Combatant, goblin: Combatant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake, calls = _sequenced_roll_check([18, 5])
        monkeypatch.setattr("engine.combat.roll_check", fake)
        monkeypatch.setattr(
            "engine.combat.roll",
            lambda _e: DiceResult(expression="1d8", rolls=[4], total=4),
        )
        goblin.conditions.append(_dodging())
        weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]

        result = resolve_attack(fighter, goblin, weapon)

        assert len(calls) == 2  # disadvantage = two d20s
        assert result.attack_roll == 5  # worst of (18, 5)
        assert result.hit is False

    def test_pc_attack_without_dodging_rolls_once(
        self, fighter: Combatant, goblin: Combatant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake, calls = _sequenced_roll_check([18, 5])
        monkeypatch.setattr("engine.combat.roll_check", fake)
        monkeypatch.setattr(
            "engine.combat.roll",
            lambda _e: DiceResult(expression="1d8", rolls=[4], total=4),
        )
        weapon = fighter.inventory.equipped[EquipmentSlot.MAIN_HAND]

        result = resolve_attack(fighter, goblin, weapon)

        assert len(calls) == 1
        assert result.attack_roll == 18
        assert result.hit is True

    def test_npc_attack_against_dodging_defender_takes_worst_roll(
        self, fighter: Combatant, goblin: Combatant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake, calls = _sequenced_roll_check([18, 5])
        monkeypatch.setattr("engine.combat.roll_check", fake)
        monkeypatch.setattr(
            "engine.combat.roll",
            lambda _e: DiceResult(expression="1d6+2", rolls=[3], total=5),
        )
        fighter.conditions.append(_dodging())
        npc_attack = NPCAttack(
            name="Griffe",
            to_hit_bonus=4,
            damage_dice="1d6+2",
            damage_type=DamageType.SLASHING,
            range_type="melee",
        )

        result = resolve_npc_attack(goblin, fighter, npc_attack)

        assert len(calls) == 2
        assert result.attack_roll == 5


# ---------------------------------------------------------------------------
# Expiry — start of the dodger's next turn
# ---------------------------------------------------------------------------


class TestDodgingExpiry:
    def test_dodging_survives_enemy_turn_then_clears_at_own_turn_start(
        self, fighter: Combatant, goblin: Combatant,
    ) -> None:
        state = CombatState(
            combatants=[fighter, goblin],
            round_number=1,
            current_turn_index=0,
        )
        fighter.conditions.append(_dodging())

        # Fighter's turn ends → goblin acts. Dodge must still protect.
        advance_turn(state)
        assert state.combatants[state.current_turn_index].name == "Goblin"
        assert has_condition(fighter.conditions, ConditionType.DODGING)

        # Goblin's turn ends → fighter's next turn starts. Dodge expires.
        advance_turn(state)
        assert state.combatants[state.current_turn_index].name == "Arden"
        assert not has_condition(fighter.conditions, ConditionType.DODGING)
