"""Tests for scripted minion AI.

Covers the ``engine.npc_ai.scripted`` module: ``NPCActionPlan`` model,
``decide_minion_action`` heuristic brain, ``execute_action_plan`` resolver.

The minion brain is a pure heuristic:
1. Attack the weakest enemy in range (melee = same zone, ranged = any zone).
2. Else move one step toward the closest enemy via BFS.
3. Else fall back on Dodge.
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
)
from engine.dice import DiceResult
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    ITEM_CATALOG,
    add_item,
    create_inventory,
    equip_item,
)
from engine.npc_ai.scripted import (
    NPCActionPlan,
    decide_minion_action,
    execute_action_plan,
)
from engine.npc_stat_block import (
    NPCAttack,
    NPCStatBlock,
    NPCTier,
)
from engine.validators import ActionType
from world.combat_zone import Zone
from world.location import Location


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pc(name: str, hp: int = 20, ac: int = 15, zone: str | None = None) -> Combatant:
    """Build a simple PC combatant with a longsword."""
    scores = AbilityScores(STR=14, DEX=12, CON=14, INT=10, WIS=10, CHA=10)
    scores = apply_racial_bonuses(scores, Race.HUMAN)
    char = create_character(name, Race.HUMAN, CharacterClass.FIGHTER, scores)
    # Override HP/max_hp to a controllable value
    char.hp = hp
    char.max_hp = max(hp, char.max_hp)
    char.ac = ac
    inv = create_inventory()
    inv = add_item(inv, ITEM_CATALOG["Longsword"])
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=inv,
        current_zone=zone,
    )


def _make_melee_goblin(
    name: str = "Goblin",
    hp: int = 10,
    zone: str | None = None,
) -> Combatant:
    """Minion with a single melee attack."""
    scores = AbilityScores(STR=8, DEX=14, CON=10, INT=8, WIS=8, CHA=8)
    char = create_character(name, Race.HALFLING, CharacterClass.ROGUE, scores)
    char.hp = hp
    char.max_hp = max(hp, char.max_hp)
    char.ac = 13
    inv = create_inventory()
    stat_block = NPCStatBlock(
        tier=NPCTier.MINION,
        archetype="goblin",
        multiattack_count=1,
        attacks=[
            NPCAttack(
                name="Scimitar",
                damage_dice="1d6+2",
                damage_type=DamageType.SLASHING,
                to_hit_bonus=4,
                range_type="melee",
            ),
        ],
    )
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
        stat_block=stat_block,
        current_zone=zone,
    )


def _make_archer(
    name: str = "Archer",
    hp: int = 10,
    zone: str | None = None,
) -> Combatant:
    """Minion with a ranged-only attack."""
    scores = AbilityScores(STR=10, DEX=16, CON=10, INT=10, WIS=10, CHA=10)
    char = create_character(name, Race.ELF, CharacterClass.RANGER, scores)
    char.hp = hp
    char.max_hp = max(hp, char.max_hp)
    char.ac = 13
    inv = create_inventory()
    stat_block = NPCStatBlock(
        tier=NPCTier.MINION,
        archetype="archer",
        multiattack_count=1,
        attacks=[
            NPCAttack(
                name="Shortbow",
                damage_dice="1d6+3",
                damage_type=DamageType.PIERCING,
                to_hit_bonus=5,
                range_type="ranged",
                range_value=80,
            ),
        ],
    )
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
        stat_block=stat_block,
        current_zone=zone,
    )


def _make_zoneless_state(combatants: list[Combatant]) -> CombatState:
    """Build a state without combat zones (all combatants in range)."""
    return CombatState(combatants=combatants, round_number=1, current_turn_index=0)


def _make_linear_location(zone_count: int = 3) -> Location:
    """Build a Location with a chain of zones: Z1 - Z2 - Z3 - ..."""
    zones: list[Zone] = []
    for i in range(1, zone_count + 1):
        neighbours: list[str] = []
        if i > 1:
            neighbours.append(f"Z{i - 1}")
        if i < zone_count:
            neighbours.append(f"Z{i + 1}")
        zones.append(
            Zone(
                name=f"Z{i}",
                description=f"Zone {i}",
                adjacent_zone_names=neighbours,
            )
        )
    return Location(name="Arena", combat_zones=zones)


# ---------------------------------------------------------------------------
# Tests — decide_minion_action
# ---------------------------------------------------------------------------


class TestDecideMinionAction:
    def test_minion_attacks_weakest_enemy_in_same_zone(self) -> None:
        """Given two enemies in range, the minion picks the one with the lowest HP."""
        goblin = _make_melee_goblin()
        strong_pc = _make_pc("Thorin", hp=30)
        weak_pc = _make_pc("Elen", hp=5)
        state = _make_zoneless_state([goblin, strong_pc, weak_pc])

        plan = decide_minion_action(goblin, state, location=None)

        assert plan.action_type == ActionType.ATTACK
        assert plan.target_name == "Elen"
        assert plan.weapon_name == "Scimitar"

    def test_minion_moves_to_adjacent_zone_containing_enemy(self) -> None:
        """No enemy in melee range → step toward an adjacent zone with a PC."""
        location = _make_linear_location(zone_count=2)
        goblin = _make_melee_goblin(zone="Z1")
        pc = _make_pc("Thorin", zone="Z2")
        state = _make_zoneless_state([goblin, pc])

        plan = decide_minion_action(goblin, state, location=location)

        assert plan.action_type == ActionType.MOVE
        assert plan.move_to_zone == "Z2"

    def test_minion_bfs_finds_next_step_toward_far_enemy(self) -> None:
        """Enemy two zones away → the plan is to move to the intermediate zone."""
        location = _make_linear_location(zone_count=3)
        goblin = _make_melee_goblin(zone="Z1")
        pc = _make_pc("Thorin", zone="Z3")
        state = _make_zoneless_state([goblin, pc])

        plan = decide_minion_action(goblin, state, location=location)

        assert plan.action_type == ActionType.MOVE
        assert plan.move_to_zone == "Z2"  # first step, not final target

    def test_minion_dodges_when_no_target_reachable(self) -> None:
        """All enemies dead → fall back on Dodge (DEFEND)."""
        goblin = _make_melee_goblin()
        dead_pc = _make_pc("Thorin")
        dead_pc.is_alive = False
        state = _make_zoneless_state([goblin, dead_pc])

        plan = decide_minion_action(goblin, state, location=None)

        assert plan.action_type == ActionType.DEFEND

    def test_minion_ranged_attack_across_zones(self) -> None:
        """An archer with a ranged attack can target enemies in other zones."""
        location = _make_linear_location(zone_count=3)
        archer = _make_archer(zone="Z1")
        pc = _make_pc("Thorin", zone="Z3")
        state = _make_zoneless_state([archer, pc])

        plan = decide_minion_action(archer, state, location=location)

        assert plan.action_type == ActionType.ATTACK
        assert plan.target_name == "Thorin"
        assert plan.weapon_name == "Shortbow"

    def test_minion_does_not_target_fled_or_dead(self) -> None:
        """Fled/dead enemies are filtered out of the target pool."""
        goblin = _make_melee_goblin()
        fled_pc = _make_pc("Runner", hp=1)
        fled_pc.fled = True
        dead_pc = _make_pc("Corpse", hp=0)
        dead_pc.is_alive = False
        live_pc = _make_pc("Thorin", hp=15)
        state = _make_zoneless_state([goblin, fled_pc, dead_pc, live_pc])

        plan = decide_minion_action(goblin, state, location=None)

        assert plan.action_type == ActionType.ATTACK
        assert plan.target_name == "Thorin"


# ---------------------------------------------------------------------------
# Tests — execute_action_plan
# ---------------------------------------------------------------------------


class TestExecuteActionPlan:
    def test_execute_attack_plan_rolls_dice_and_applies_damage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ATTACK plan consumes Action and routes through resolve_npc_attack."""
        goblin = _make_melee_goblin()
        pc = _make_pc("Thorin", hp=20, ac=10)
        state = _make_zoneless_state([goblin, pc])

        # Deterministic attack roll: 15 total (guaranteed hit vs AC 10).
        from engine.dice import D20CheckResult, RollOutcome

        def mock_roll_check(expr: str, dc: int) -> D20CheckResult:
            return D20CheckResult(
                expression=expr,
                rolls=[15],
                modifier=4,
                total=19,
                dc=dc,
                outcome=RollOutcome.SUCCESS,
                margin=19 - dc,
            )

        def mock_roll(expr: str) -> DiceResult:
            return DiceResult(expression=expr, rolls=[5], total=5)

        monkeypatch.setattr("engine.combat.roll_check", mock_roll_check)
        monkeypatch.setattr("engine.combat.roll", mock_roll)

        plan = NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name="Thorin",
            weapon_name="Scimitar",
            rationale="test",
        )
        summary = execute_action_plan(goblin, plan, state, location=None)

        assert goblin.action_budget.action_used is True
        assert pc.character.hp < 20
        assert "Thorin" in summary

    def test_execute_move_plan_calls_move_combatant_to_zone(self) -> None:
        """MOVE plan delegates to engine.combat.move_combatant_to_zone."""
        location = _make_linear_location(zone_count=2)
        goblin = _make_melee_goblin(zone="Z1")
        pc = _make_pc("Thorin", zone="Z2")
        state = _make_zoneless_state([goblin, pc])

        plan = NPCActionPlan(
            action_type=ActionType.MOVE,
            move_to_zone="Z2",
            rationale="test",
        )
        execute_action_plan(goblin, plan, state, location=location)

        assert goblin.current_zone == "Z2"

    def test_execute_dodge_plan_consumes_action(self) -> None:
        """DEFEND plan (Dodge fallback) consumes the action budget."""
        goblin = _make_melee_goblin()
        pc = _make_pc("Thorin")
        state = _make_zoneless_state([goblin, pc])

        plan = NPCActionPlan(
            action_type=ActionType.DEFEND,
            rationale="blocked",
        )
        execute_action_plan(goblin, plan, state, location=None)

        assert goblin.action_budget.action_used is True


class TestExecuteSignatureBudget:
    """An exhausted signature must not fire at execution time (audit H19)."""

    def test_exhausted_signature_does_not_execute(self) -> None:
        from engine.npc_stat_block import SignatureAbility, SignatureAbilityEffect

        goblin = _make_melee_goblin()
        assert goblin.stat_block is not None
        goblin.stat_block.signature_abilities.append(
            SignatureAbility(
                name="Nuke",
                description="Once per combat, in theory.",
                usage="per_combat",
                uses_remaining=0,
                effects=[
                    SignatureAbilityEffect(
                        kind="damage",
                        dice="10d12",
                        damage_type=DamageType.FIRE,
                    ),
                ],
            )
        )
        pc = _make_pc("Thorin", hp=20)
        state = _make_zoneless_state([goblin, pc])

        plan = NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name="Thorin",
            signature_name="Nuke",
            rationale="spam attempt",
        )
        summary = execute_action_plan(goblin, plan, state, location=None)

        assert "no uses remaining" in summary
        assert pc.character.hp == 20  # effect did not fire
