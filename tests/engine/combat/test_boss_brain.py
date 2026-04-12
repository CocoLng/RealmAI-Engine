"""Tests for boss_brain retry + fallback.

Covers ``engine.npc_ai.boss_brain.decide_boss_action``: the retry loop
on ``ValueError`` from the tactician, the scripted fallback after retries
are exhausted, and the ``TacticalDecision → NPCActionPlan`` mapping.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ai.models import TacticalDecision
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    apply_racial_bonuses,
    create_character,
)
from engine.combat import CombatSide, CombatState, Combatant
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    ITEM_CATALOG,
    add_item,
    create_inventory,
    equip_item,
)
from engine.npc_ai.boss_brain import _decision_to_plan, decide_boss_action
from engine.npc_stat_block import (
    BehaviorProfile,
    NPCAttack,
    NPCStatBlock,
    NPCTier,
    SignatureAbility,
    SignatureAbilityEffect,
)
from engine.validators import ActionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_boss(name: str = "Dread") -> Combatant:
    scores = AbilityScores(STR=16, DEX=14, CON=16, INT=14, WIS=14, CHA=14)
    char = create_character(name, Race.HUMAN, CharacterClass.FIGHTER, scores)
    char.hp = 80
    char.max_hp = 80
    char.ac = 18
    inv = create_inventory()
    stat_block = NPCStatBlock(
        tier=NPCTier.BOSS,
        archetype="dread_lord",
        multiattack_count=3,
        attacks=[
            NPCAttack(
                name="Greataxe",
                damage_dice="1d12+4",
                damage_type=DamageType.SLASHING,
                to_hit_bonus=7,
            ),
        ],
        signature_abilities=[
            SignatureAbility(
                name="Cleave",
                description="Massive damage swing.",
                usage="per_combat",
                uses_remaining=1,
                effects=[
                    SignatureAbilityEffect(
                        kind="damage",
                        dice="3d8+4",
                        damage_type=DamageType.SLASHING,
                        target_scope="single",
                    ),
                ],
            ),
        ],
        behavior_profile=BehaviorProfile.AGGRESSIVE,
    )
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
        stat_block=stat_block,
    )


def _make_pc(name: str, hp: int = 20) -> Combatant:
    scores = AbilityScores(STR=14, DEX=12, CON=14, INT=10, WIS=10, CHA=10)
    scores = apply_racial_bonuses(scores, Race.HUMAN)
    char = create_character(name, Race.HUMAN, CharacterClass.FIGHTER, scores)
    char.hp = hp
    char.max_hp = max(hp, char.max_hp)
    char.ac = 15
    inv = create_inventory()
    inv = add_item(inv, ITEM_CATALOG["Longsword"])
    inv = equip_item(inv, "Longsword", EquipmentSlot.MAIN_HAND)
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=inv,
    )


def _state(combatants: list[Combatant]) -> CombatState:
    return CombatState(combatants=combatants, round_number=1, current_turn_index=0)


# ---------------------------------------------------------------------------
# Retry + fallback
# ---------------------------------------------------------------------------


class TestDecideBossAction:
    def test_uses_llm_decision_when_valid(self) -> None:
        boss = _make_boss()
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.return_value = TacticalDecision(
            action_type="attack",
            target_name="Thorin",
            weapon_name="Greataxe",
            reasoning="Cet adversaire est une menace immédiate.",
        )

        plan = decide_boss_action(
            boss, state, location=None, tactician=tactician,
            party_context="test", recent_events=["prev turn event"],
        )

        assert plan.action_type == ActionType.ATTACK
        assert plan.target_name == "Thorin"
        assert plan.weapon_name == "Greataxe"
        assert "menace" in plan.rationale  # reasoning passed through
        assert tactician.decide.call_count == 1

    def test_retries_on_invalid_output(self) -> None:
        boss = _make_boss()
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.side_effect = [
            ValueError("bad json"),
            TacticalDecision(
                action_type="signature",
                target_name="Thorin",
                signature_name="Cleave",
                reasoning="Big damage on the weakest PC.",
            ),
        ]

        plan = decide_boss_action(
            boss, state, location=None, tactician=tactician,
            party_context="", recent_events=[],
        )

        assert tactician.decide.call_count == 2
        assert plan.action_type == ActionType.ATTACK
        assert plan.signature_name == "Cleave"

    def test_falls_back_to_scripted_after_retries(self) -> None:
        boss = _make_boss()
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.side_effect = [
            ValueError("fail 1"),
            ValueError("fail 2"),
        ]

        plan = decide_boss_action(
            boss, state, location=None, tactician=tactician,
            party_context="", recent_events=[],
        )

        # Used both retries
        assert tactician.decide.call_count == 2
        # Fell back: plan still valid, rationale tagged with [LLM fallback]
        assert plan.action_type == ActionType.ATTACK
        assert "LLM fallback" in plan.rationale


# ---------------------------------------------------------------------------
# TacticalDecision → NPCActionPlan mapping
# ---------------------------------------------------------------------------


class TestDecisionToPlanMapping:
    def test_attack_maps_to_attack(self) -> None:
        decision = TacticalDecision(
            action_type="attack",
            target_name="Thorin",
            weapon_name="Greataxe",
            reasoning="Solid hit this round.",
        )
        plan = _decision_to_plan(decision)
        assert plan.action_type == ActionType.ATTACK
        assert plan.signature_name is None
        assert plan.weapon_name == "Greataxe"

    def test_signature_maps_to_attack_with_signature_name(self) -> None:
        decision = TacticalDecision(
            action_type="signature",
            target_name="Thorin",
            signature_name="Cleave",
            reasoning="Burning my cooldown to finish the fight.",
        )
        plan = _decision_to_plan(decision)
        assert plan.action_type == ActionType.ATTACK
        assert plan.signature_name == "Cleave"
        assert plan.weapon_name is None

    def test_move_maps_to_move(self) -> None:
        decision = TacticalDecision(
            action_type="move",
            move_to_zone="North Ridge",
            reasoning="Need high ground for the next turn.",
        )
        plan = _decision_to_plan(decision)
        assert plan.action_type == ActionType.MOVE
        assert plan.move_to_zone == "North Ridge"

    def test_dodge_and_disengage_map_to_defend(self) -> None:
        dodge = _decision_to_plan(
            TacticalDecision(action_type="dodge", reasoning="Stall the fight."),
        )
        disengage = _decision_to_plan(
            TacticalDecision(
                action_type="disengage",
                reasoning="Retreat without provoking an OOA.",
            ),
        )
        assert dodge.action_type == ActionType.DEFEND
        assert disengage.action_type == ActionType.DEFEND
