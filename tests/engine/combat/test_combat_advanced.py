"""Tests for the multi-enemy combat engine primitives.

Covers:
- 3-case initiative & surprise via ``start_combat(..., trigger=)``.
- ``CombatState.combat_id``, ``end_reason``, multi-enemy turn
  management, ``check_combat_end``, concentration hook, ``resolve_npc_attack``.
- ``ActionBudget`` + consume helpers + reset semantics.
- Zone movement, opportunity attacks, Disengage.
"""

from __future__ import annotations

from typing import Any

import pytest

from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    apply_racial_bonuses,
    create_character,
)
from engine.combat import (
    ActionBudget,
    AttackResult,
    CombatEndReason,
    CombatSide,
    CombatState,
    Combatant,
    PhaseTransitionEvent,
    advance_turn,
    apply_damage,
    check_combat_end,
    consume_action,
    consume_bonus_action,
    consume_movement,
    consume_reaction,
    disengage,
    is_combat_over,
    move_combatant_to_zone,
    resolve_npc_attack,
    start_combat,
)
from engine.combat_trigger import (
    CombatTrigger,
    CombatTriggerKind,
    InitiativeSide,
)
from engine.conditions import (
    ActiveCondition,
    ConditionType,
    apply_condition,
    has_condition,
    is_surprised,
)
from engine.dice import D20CheckResult, DiceResult, RollOutcome, _compute_outcome
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    ITEM_CATALOG,
    Weapon,
    WeaponCategory,
    WeaponProperty,
    add_item,
    create_inventory,
    equip_item,
)
from engine.npc_stat_block import (
    NPCAttack,
    NPCStatBlock,
    NPCTier,
)
from world.combat_zone import Zone, ZoneTag
from world.location import Location


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_roll(total: int, expr: str = "1d20") -> DiceResult:
    return DiceResult(expression=expr, rolls=[total], total=total)


def _mock_roll_check(natural_roll: int):
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


@pytest.fixture()
def fighter() -> Combatant:
    scores = AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8)
    scores = apply_racial_bonuses(scores, Race.HUMAN)
    char = create_character("Arden", Race.HUMAN, CharacterClass.FIGHTER, scores)
    inv = create_inventory()
    longsword = ITEM_CATALOG["Longsword"]
    inv = add_item(inv, longsword)
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
    return Combatant(name="Arden", side=CombatSide.PLAYER, character=char, inventory=inv)


@pytest.fixture()
def fighter2() -> Combatant:
    scores = AbilityScores(STR=14, DEX=12, CON=14, INT=10, WIS=10, CHA=10)
    scores = apply_racial_bonuses(scores, Race.HUMAN)
    char = create_character("Bren", Race.HUMAN, CharacterClass.FIGHTER, scores)
    inv = create_inventory()
    longsword = ITEM_CATALOG["Longsword"]
    inv = add_item(inv, longsword)
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
    return Combatant(name="Bren", side=CombatSide.PLAYER, character=char, inventory=inv)


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
def orc() -> Combatant:
    scores = AbilityScores(STR=14, DEX=12, CON=12, INT=8, WIS=10, CHA=8)
    scores = apply_racial_bonuses(scores, Race.HALF_ORC)
    char = create_character("Orc", Race.HALF_ORC, CharacterClass.BARBARIAN, scores)
    inv = create_inventory()
    greataxe = ITEM_CATALOG["Greataxe"]
    inv = add_item(inv, greataxe)
    inv = equip_item(inv, "Greataxe", EquipmentSlot.MAIN_HAND)
    return Combatant(
        name="Orc",
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
        stat_block=NPCStatBlock(
            tier=NPCTier.MINION,
            archetype="orc_brute",
            attacks=[
                NPCAttack(
                    name="Battleaxe",
                    damage_dice="1d10+2",
                    damage_type=DamageType.SLASHING,
                    to_hit_bonus=5,
                    range_type="melee",
                ),
            ],
        ),
    )


# ---------------------------------------------------------------------------
# TASK 21 — Initiative & Surprise
# ---------------------------------------------------------------------------


class TestStartCombatWithTrigger:
    def test_without_trigger_uses_standard_initiative(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No trigger — regression path: sort by initiative + DEX tiebreak."""
        call_count = 0

        def mock_roll(_expr: str) -> DiceResult:
            nonlocal call_count
            call_count += 1
            return _make_roll(10 if call_count == 1 else 18)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        state = start_combat([fighter, goblin])
        assert state.combatants[0].name == "Goblin"
        assert state.combatants[1].name == "Arden"
        assert state.round_number == 1

    def test_player_surprise_places_aggressor_first(
        self,
        fighter: Combatant,
        fighter2: Combatant,
        goblin: Combatant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        trigger = CombatTrigger(
            kind=CombatTriggerKind.PLAYER_ATTACK,
            aggressor_name="Arden",
            enemy_names=["Goblin"],
            surprise_side=InitiativeSide.PLAYERS,
        )
        state = start_combat([goblin, fighter2, fighter], trigger)
        assert state.combatants[0].name == "Arden"

    def test_player_surprise_applies_surprised_to_enemies(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        trigger = CombatTrigger(
            kind=CombatTriggerKind.PLAYER_ATTACK,
            aggressor_name="Arden",
            enemy_names=["Goblin"],
            surprise_side=InitiativeSide.PLAYERS,
        )
        start_combat([fighter, goblin], trigger)
        assert is_surprised(goblin.conditions)
        assert not is_surprised(fighter.conditions)

    def test_npc_surprise_places_ambushers_first(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        trigger = CombatTrigger(
            kind=CombatTriggerKind.AMBUSH,
            aggressor_name="Goblin",
            enemy_names=["Goblin"],
            surprise_side=InitiativeSide.NPCS,
        )
        state = start_combat([fighter, goblin], trigger)
        assert state.combatants[0].name == "Goblin"

    def test_npc_surprise_applies_surprised_to_all_pcs(
        self,
        fighter: Combatant,
        fighter2: Combatant,
        goblin: Combatant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        trigger = CombatTrigger(
            kind=CombatTriggerKind.AMBUSH,
            aggressor_name="Goblin",
            enemy_names=["Goblin"],
            surprise_side=InitiativeSide.NPCS,
        )
        start_combat([fighter, fighter2, goblin], trigger)
        assert is_surprised(fighter.conditions)
        assert is_surprised(fighter2.conditions)
        assert not is_surprised(goblin.conditions)

    def test_both_ready_sorts_by_initiative(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = 0

        def mock_roll(_expr: str) -> DiceResult:
            nonlocal call_count
            call_count += 1
            return _make_roll(10 if call_count == 1 else 18)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        trigger = CombatTrigger(
            kind=CombatTriggerKind.PLAYER_ATTACK,
            aggressor_name="Arden",
            enemy_names=["Goblin"],
            surprise_side=InitiativeSide.BOTH_READY,
        )
        state = start_combat([fighter, goblin], trigger)
        # Goblin won the roll, so goblin should be first.
        assert state.combatants[0].name == "Goblin"

    def test_initiative_values_preserved_on_combatants(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(12))
        state = start_combat([fighter, goblin])
        for c in state.combatants:
            assert c.initiative > 0

    def test_surprise_not_applied_on_ambushers(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        trigger = CombatTrigger(
            kind=CombatTriggerKind.AMBUSH,
            aggressor_name="Goblin",
            enemy_names=["Goblin"],
            surprise_side=InitiativeSide.NPCS,
        )
        start_combat([fighter, goblin], trigger)
        assert not is_surprised(goblin.conditions)


# ---------------------------------------------------------------------------
# TASK 22 — CombatState persistence, advance_turn, check_combat_end
# ---------------------------------------------------------------------------


class TestCombatStateExtensions:
    def test_combat_state_generates_unique_id(self) -> None:
        s1 = CombatState()
        s2 = CombatState()
        assert s1.combat_id != s2.combat_id
        assert len(s1.combat_id) > 0

    def test_combat_state_defaults(self) -> None:
        state = CombatState()
        assert state.end_reason is None
        assert state.pending_phase_narrations == []

    def test_combat_state_roundtrips_with_new_fields(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        state.pending_phase_narrations.append(
            PhaseTransitionEvent(combatant_name="Goblin", phase_index=0, narrative_cue="it snarls")
        )

        payload = state.model_dump_json()
        restored = CombatState.model_validate_json(payload)

        assert restored.combat_id == state.combat_id
        assert restored.end_reason is None
        assert len(restored.pending_phase_narrations) == 1
        assert restored.combatants[0].action_budget.movement_remaining_feet > 0


class TestAdvanceTurnPhase2:
    def test_skips_dead_combatants(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scores = AbilityScores(STR=8, DEX=12, CON=10, INT=10, WIS=8, CHA=8)
        scores = apply_racial_bonuses(scores, Race.HALFLING)
        char = create_character("Goblin2", Race.HALFLING, CharacterClass.ROGUE, scores)
        goblin2 = Combatant(
            name="Goblin2",
            side=CombatSide.ENEMY,
            character=char,
            inventory=create_inventory(),
        )

        call_count = 0

        def mock_roll(_expr: str) -> DiceResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_roll(20)
            if call_count == 2:
                return _make_roll(15)
            return _make_roll(10)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        state = start_combat([fighter, goblin, goblin2])
        state.combatants[1].is_alive = False

        advance_turn(state)
        assert state.current_turn_index == 2

    def test_increments_round_on_wrap(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        assert state.round_number == 1
        advance_turn(state)
        assert state.round_number == 1
        advance_turn(state)
        assert state.round_number == 2

    def test_consumes_surprise_at_end_of_first_turn(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        trigger = CombatTrigger(
            kind=CombatTriggerKind.PLAYER_ATTACK,
            aggressor_name="Arden",
            enemy_names=["Goblin"],
            surprise_side=InitiativeSide.PLAYERS,
        )
        state = start_combat([fighter, goblin], trigger)
        assert is_surprised(goblin.conditions)

        # Fighter finishes turn.
        advance_turn(state)
        # Now it's goblin's (no-op surprised) turn.
        assert state.current_turn_index == 1
        # Advance ends goblin's turn and consumes the surprise.
        advance_turn(state)
        assert not is_surprised(goblin.conditions)

    def test_turn_rotation_follows_initiative_order_over_two_rounds(
        self,
        fighter: Combatant,
        fighter2: Combatant,
        goblin: Combatant,
        orc: Combatant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """4 combatants: the advance_turn cycle visits them in initiative order
        for 2 full rounds. The order seen by the UI (state.combatants) matches
        the sequence get_current_combatant returns."""
        # Force distinct rolls so the ORDER is unambiguous:
        # Arden=20, Bren=10, Goblin=18, Orc=5  →  [Arden, Goblin, Bren, Orc]
        rolls = iter([20, 10, 18, 5])
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(next(rolls)))
        state = start_combat([fighter, fighter2, goblin, orc])

        expected_order = [c.name for c in state.combatants]
        assert expected_order[0] == "Arden"

        visited: list[str] = [state.combatants[state.current_turn_index].name]
        for _ in range(len(state.combatants) * 2 - 1):
            advance_turn(state)
            visited.append(state.combatants[state.current_turn_index].name)

        # Two full rounds of the same order, back-to-back.
        assert visited == expected_order + expected_order
        assert state.round_number == 2

    def test_ordered_list_matches_descending_initiative(
        self,
        fighter: Combatant,
        fighter2: Combatant,
        goblin: Combatant,
        orc: Combatant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """state.combatants is strictly sorted by initiative desc (no surprise)."""
        rolls = iter([20, 10, 18, 5])
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(next(rolls)))
        state = start_combat([fighter, fighter2, goblin, orc])

        initiatives = [c.initiative for c in state.combatants]
        assert initiatives == sorted(initiatives, reverse=True)

    def test_surprise_order_persists_into_round_two(
        self,
        fighter: Combatant,
        fighter2: Combatant,
        goblin: Combatant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Under PLAYERS surprise the aggressor keeps slot 0 through round 2."""
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        trigger = CombatTrigger(
            kind=CombatTriggerKind.PLAYER_ATTACK,
            aggressor_name="Arden",
            enemy_names=["Goblin"],
            surprise_side=InitiativeSide.PLAYERS,
        )
        state = start_combat([fighter, fighter2, goblin], trigger)
        order_round1 = [c.name for c in state.combatants]
        assert order_round1[0] == "Arden"  # aggressor

        # Full rotation through round 1 -> lands back at Arden for round 2.
        for _ in range(len(state.combatants)):
            advance_turn(state)
        assert state.round_number == 2
        assert state.current_turn_index == 0
        assert state.combatants[state.current_turn_index].name == "Arden"
        # The list itself wasn't shuffled between rounds.
        assert [c.name for c in state.combatants] == order_round1

    def test_sets_victory_when_all_enemies_dead(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        for c in state.combatants:
            if c.side == CombatSide.ENEMY:
                c.is_alive = False
        advance_turn(state)
        assert state.is_active is False
        assert state.end_reason == CombatEndReason.VICTORY

    def test_sets_defeat_when_all_pcs_dead(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        for c in state.combatants:
            if c.side == CombatSide.PLAYER:
                c.is_alive = False
        advance_turn(state)
        assert state.end_reason == CombatEndReason.DEFEAT

    def test_resets_action_budget_on_turn_start(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        # Drain fighter's budget manually
        first = state.combatants[0]
        first.action_budget.action_used = True
        first.action_budget.movement_remaining_feet = 0
        # Advance past fighter; the next combatant's budget should be fresh.
        advance_turn(state)
        nxt = state.combatants[state.current_turn_index]
        assert nxt.action_budget.action_used is False
        assert nxt.action_budget.movement_remaining_feet > 0

    def test_resets_reactions_on_round_wrap(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        for c in state.combatants:
            c.action_budget.reaction_used_this_round = True

        advance_turn(state)  # 0 -> 1
        assert state.combatants[0].action_budget.reaction_used_this_round is True  # not yet
        advance_turn(state)  # 1 -> 0 (wrap)
        for c in state.combatants:
            assert c.action_budget.reaction_used_this_round is False


class TestCheckCombatEnd:
    def test_returns_none_when_both_sides_alive(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        assert check_combat_end(state) is None

    def test_victory_when_enemies_fled(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        for c in state.combatants:
            if c.side == CombatSide.ENEMY:
                c.fled = True
        assert check_combat_end(state) == CombatEndReason.VICTORY

    def test_fled_when_all_pcs_fled(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        for c in state.combatants:
            if c.side == CombatSide.PLAYER:
                c.fled = True
        assert check_combat_end(state) == CombatEndReason.FLED

    def test_is_combat_over_mirrors_check(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        assert is_combat_over(state) is False
        goblin.is_alive = False
        assert is_combat_over(state) is True


class TestConcentrationHook:
    def test_save_triggered_on_damage(
        self, fighter: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        apply_condition(
            fighter.conditions,
            ActiveCondition(condition_type=ConditionType.CONCENTRATING, source="Bless"),
        )
        called: dict[str, Any] = {}

        def fake_save(combatant: Combatant, dmg: int) -> D20CheckResult:
            called["combatant"] = combatant.name
            called["damage"] = dmg
            return D20CheckResult(
                expression="1d20+2", rolls=[15], modifier=2, total=17,
                dc=10, outcome=RollOutcome.SUCCESS, margin=7,
            )

        monkeypatch.setattr("engine.combat.check_concentration_save", fake_save)
        apply_damage(fighter, 10)
        assert called == {"combatant": "Arden", "damage": 10}
        # Success — still concentrating.
        assert has_condition(fighter.conditions, ConditionType.CONCENTRATING)

    def test_dropped_on_failed_save(
        self, fighter: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        apply_condition(
            fighter.conditions,
            ActiveCondition(condition_type=ConditionType.CONCENTRATING, source="Bless"),
        )

        def fake_save(combatant: Combatant, dmg: int) -> D20CheckResult:
            return D20CheckResult(
                expression="1d20", rolls=[3], modifier=0, total=3,
                dc=10, outcome=RollOutcome.FAILURE, margin=-7,
            )

        monkeypatch.setattr("engine.combat.check_concentration_save", fake_save)
        apply_damage(fighter, 10)
        assert not has_condition(fighter.conditions, ConditionType.CONCENTRATING)

    def test_not_triggered_when_not_concentrating(
        self, fighter: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def should_not_be_called(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("check_concentration_save should not run")

        monkeypatch.setattr("engine.combat.check_concentration_save", should_not_be_called)
        apply_damage(fighter, 10)  # should be a no-op on the hook


class TestResolveNPCAttack:
    def test_hit_applies_damage_and_returns_result(
        self, orc: Combatant, fighter: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(15))
        monkeypatch.setattr(
            "engine.combat.roll",
            lambda expr: DiceResult(expression=expr, rolls=[7], total=7),
        )
        hp_before = fighter.character.hp
        assert orc.stat_block is not None
        result = resolve_npc_attack(orc, fighter, orc.stat_block.attacks[0])
        assert isinstance(result, AttackResult)
        assert result.hit is True
        assert result.damage == 7
        assert fighter.character.hp == hp_before - 7
        assert result.weapon_name == "Battleaxe"

    def test_nat_1_auto_miss(
        self, orc: Combatant, fighter: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(1))
        assert orc.stat_block is not None
        fighter.character.ac = 0
        result = resolve_npc_attack(orc, fighter, orc.stat_block.attacks[0])
        assert result.hit is False
        assert result.damage == 0

    def test_crit_doubles_damage_dice(
        self, orc: Combatant, fighter: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(20))

        def mock_roll(expr: str) -> DiceResult:
            # Crit doubles dice count: 1d10+2 -> 2d10+2
            if "2d10" in expr:
                return DiceResult(expression=expr, rolls=[5, 5], total=12)
            return DiceResult(expression=expr, rolls=[5], total=5)

        monkeypatch.setattr("engine.combat.roll", mock_roll)
        assert orc.stat_block is not None
        result = resolve_npc_attack(orc, fighter, orc.stat_block.attacks[0])
        assert result.critical is True
        assert result.damage == 12


# ---------------------------------------------------------------------------
# TASK 23 — Action Economy
# ---------------------------------------------------------------------------


class TestActionBudget:
    def test_defaults(self) -> None:
        b = ActionBudget()
        assert b.movement_remaining_feet == 30
        assert b.action_used is False
        assert b.bonus_action_used is False
        assert b.reaction_used_this_round is False
        assert b.disengaged_this_turn is False

    def test_reset_for_new_turn_preserves_reaction(self) -> None:
        b = ActionBudget()
        b.action_used = True
        b.bonus_action_used = True
        b.disengaged_this_turn = True
        b.movement_remaining_feet = 0
        b.reaction_used_this_round = True

        b.reset_for_new_turn(base_speed_feet=30)

        assert b.movement_remaining_feet == 30
        assert b.action_used is False
        assert b.bonus_action_used is False
        assert b.disengaged_this_turn is False
        # Reaction persists across turns.
        assert b.reaction_used_this_round is True

    def test_consume_action_raises_on_second_call(self, fighter: Combatant) -> None:
        consume_action(fighter)
        with pytest.raises(ValueError, match="already used their Action"):
            consume_action(fighter)

    def test_consume_bonus_action_independent_from_action(
        self, fighter: Combatant
    ) -> None:
        consume_action(fighter)
        # Bonus action still available.
        consume_bonus_action(fighter)
        with pytest.raises(ValueError):
            consume_bonus_action(fighter)

    def test_consume_movement_partial_spend(self, fighter: Combatant) -> None:
        fighter.action_budget.movement_remaining_feet = 30
        consume_movement(fighter, 15)
        assert fighter.action_budget.movement_remaining_feet == 15

    def test_consume_movement_insufficient_raises(self, fighter: Combatant) -> None:
        fighter.action_budget.movement_remaining_feet = 10
        with pytest.raises(ValueError, match="movement"):
            consume_movement(fighter, 15)

    def test_consume_movement_negative_raises(self, fighter: Combatant) -> None:
        with pytest.raises(ValueError, match="negative"):
            consume_movement(fighter, -5)

    def test_consume_reaction_raises_on_second(self, fighter: Combatant) -> None:
        consume_reaction(fighter)
        with pytest.raises(ValueError, match="Reaction"):
            consume_reaction(fighter)


# ---------------------------------------------------------------------------
# TASK 24 — Zone movement, OOA, Disengage
# ---------------------------------------------------------------------------


def _zoned_location() -> Location:
    return Location(
        name="Arena",
        combat_zones=[
            Zone(name="gate", adjacent_zone_names=["courtyard"]),
            Zone(
                name="courtyard",
                adjacent_zone_names=["gate", "altar"],
            ),
            Zone(
                name="altar",
                adjacent_zone_names=["courtyard"],
                tags=[ZoneTag.DIFFICULT_TERRAIN],
            ),
        ],
    )


class TestZoneMovement:
    def test_move_to_adjacent_zone_succeeds(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        fighter.current_zone = "gate"
        goblin.current_zone = "altar"
        state = start_combat([fighter, goblin])
        location = _zoned_location()

        fighter_state = next(c for c in state.combatants if c.name == "Arden")
        fighter_state.current_zone = "gate"
        results = move_combatant_to_zone(state, fighter_state, "courtyard", location)

        assert fighter_state.current_zone == "courtyard"
        assert fighter_state.action_budget.movement_remaining_feet == 30 - 15
        assert results == []

    def test_move_to_non_adjacent_zone_raises(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        location = _zoned_location()
        state.combatants[0].current_zone = "gate"

        with pytest.raises(ValueError, match="not adjacent"):
            move_combatant_to_zone(state, state.combatants[0], "altar", location)

    def test_move_with_insufficient_movement_raises(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        location = _zoned_location()
        state.combatants[0].current_zone = "gate"
        state.combatants[0].action_budget.movement_remaining_feet = 10

        with pytest.raises(ValueError, match="movement"):
            move_combatant_to_zone(state, state.combatants[0], "courtyard", location)

    def test_difficult_terrain_doubles_cost(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        location = _zoned_location()
        fighter_c = next(c for c in state.combatants if c.name == "Arden")
        fighter_c.current_zone = "courtyard"

        move_combatant_to_zone(state, fighter_c, "altar", location)
        # altar is DIFFICULT_TERRAIN → cost 30 ft.
        assert fighter_c.action_budget.movement_remaining_feet == 0
        assert fighter_c.current_zone == "altar"

    def test_opportunity_attack_triggered_without_disengage(
        self, fighter: Combatant, orc: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, orc])
        location = _zoned_location()

        fighter_c = next(c for c in state.combatants if c.name == "Arden")
        orc_c = next(c for c in state.combatants if c.name == "Orc")
        fighter_c.current_zone = "gate"
        orc_c.current_zone = "gate"

        # Force the OOA to hit with a guaranteed attack roll + damage.
        monkeypatch.setattr("engine.combat.roll_check", _mock_roll_check(15))

        def mock_roll(expr: str) -> DiceResult:
            if "d10" in expr:
                return DiceResult(expression=expr, rolls=[5], total=5 + 2)
            return DiceResult(expression=expr, rolls=[10], total=10)

        monkeypatch.setattr("engine.combat.roll", mock_roll)

        results = move_combatant_to_zone(state, fighter_c, "courtyard", location)
        assert len(results) == 1
        assert results[0].attacker == "Orc"
        assert orc_c.action_budget.reaction_used_this_round is True

    def test_disengage_suppresses_opportunity_attacks(
        self, fighter: Combatant, orc: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, orc])
        location = _zoned_location()

        fighter_c = next(c for c in state.combatants if c.name == "Arden")
        orc_c = next(c for c in state.combatants if c.name == "Orc")
        fighter_c.current_zone = "gate"
        orc_c.current_zone = "gate"

        disengage(fighter_c)
        assert fighter_c.action_budget.action_used is True
        assert fighter_c.action_budget.disengaged_this_turn is True

        results = move_combatant_to_zone(state, fighter_c, "courtyard", location)
        assert results == []
        assert orc_c.action_budget.reaction_used_this_round is False

    def test_ally_does_not_trigger_opportunity_attack(
        self,
        fighter: Combatant,
        fighter2: Combatant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, fighter2])
        location = _zoned_location()

        a = next(c for c in state.combatants if c.name == "Arden")
        b = next(c for c in state.combatants if c.name == "Bren")
        a.current_zone = "gate"
        b.current_zone = "gate"

        results = move_combatant_to_zone(state, a, "courtyard", location)
        assert results == []

    def test_enemy_without_reaction_does_not_ooa(
        self, fighter: Combatant, orc: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, orc])
        location = _zoned_location()
        fighter_c = next(c for c in state.combatants if c.name == "Arden")
        orc_c = next(c for c in state.combatants if c.name == "Orc")
        fighter_c.current_zone = "gate"
        orc_c.current_zone = "gate"
        orc_c.action_budget.reaction_used_this_round = True

        results = move_combatant_to_zone(state, fighter_c, "courtyard", location)
        assert results == []

    def test_dead_enemy_does_not_ooa(
        self, fighter: Combatant, orc: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, orc])
        location = _zoned_location()
        fighter_c = next(c for c in state.combatants if c.name == "Arden")
        orc_c = next(c for c in state.combatants if c.name == "Orc")
        fighter_c.current_zone = "gate"
        orc_c.current_zone = "gate"
        orc_c.is_alive = False

        results = move_combatant_to_zone(state, fighter_c, "courtyard", location)
        assert results == []

    def test_move_without_zones_raises(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        empty_location = Location(name="Nowhere")

        with pytest.raises(ValueError, match="no combat zones"):
            move_combatant_to_zone(state, state.combatants[0], "x", empty_location)

    def test_move_without_current_zone_raises(
        self, fighter: Combatant, goblin: Combatant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.combat.roll", lambda _expr: _make_roll(10))
        state = start_combat([fighter, goblin])
        location = _zoned_location()
        # fighter.current_zone not set
        with pytest.raises(ValueError, match="no current zone"):
            move_combatant_to_zone(state, state.combatants[0], "courtyard", location)


class TestDisengage:
    def test_consumes_action(self, fighter: Combatant) -> None:
        disengage(fighter)
        assert fighter.action_budget.action_used is True
        assert fighter.action_budget.disengaged_this_turn is True

    def test_cannot_disengage_twice(self, fighter: Combatant) -> None:
        disengage(fighter)
        with pytest.raises(ValueError):
            disengage(fighter)


# ---------------------------------------------------------------------------
# Narration plumbing on CombatState + PhaseTransitionEvent
# ---------------------------------------------------------------------------


class TestRecentEventsField:
    def test_default_is_empty(self) -> None:
        state = CombatState()
        assert state.recent_events == []

    def test_record_combat_event_appends(self) -> None:
        from engine.combat import record_combat_event

        state = CombatState()
        record_combat_event(state, "Thorin attaque Gob 1 : HIT 8 dégâts.")
        assert state.recent_events == [
            "Thorin attaque Gob 1 : HIT 8 dégâts.",
        ]

    def test_record_combat_event_caps_at_12(self) -> None:
        from engine.combat import RECENT_EVENTS_CAP, record_combat_event

        state = CombatState()
        for i in range(15):
            record_combat_event(state, f"event {i}")
        assert len(state.recent_events) == RECENT_EVENTS_CAP
        # Only the latest 12 entries survive.
        assert state.recent_events[0] == "event 3"
        assert state.recent_events[-1] == "event 14"

    def test_serialization_round_trip_preserves_recent_events(self) -> None:
        state = CombatState(
            recent_events=["a", "b", "c"],
        )
        dumped = state.model_dump_json()
        loaded = CombatState.model_validate_json(dumped)
        assert loaded.recent_events == ["a", "b", "c"]


class TestPhaseTransitionEventConsumed:
    def test_consumed_defaults_false(self) -> None:
        event = PhaseTransitionEvent(combatant_name="Vellus")
        assert event.consumed is False

    def test_consumed_round_trips_through_json(self) -> None:
        event = PhaseTransitionEvent(
            combatant_name="Vellus",
            phase_index=1,
            narrative_cue="Ses yeux virent au blanc.",
            consumed=True,
        )
        dumped = event.model_dump_json()
        loaded = PhaseTransitionEvent.model_validate_json(dumped)
        assert loaded.consumed is True
        assert loaded.narrative_cue == "Ses yeux virent au blanc."

    def test_legacy_payload_without_consumed_field_deserializes(self) -> None:
        # A pre-task-71 serialized event does not carry ``consumed``.
        # The default must kick in so old combat states round-trip cleanly.
        legacy = '{"combatant_name": "Vellus", "phase_index": 0, "narrative_cue": ""}'
        loaded = PhaseTransitionEvent.model_validate_json(legacy)
        assert loaded.consumed is False
