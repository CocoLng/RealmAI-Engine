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
from world.combat_zone import Zone
from world.location import Location


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


# ---------------------------------------------------------------------------
# Engine-side decision validation (audit H19)
# ---------------------------------------------------------------------------


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


def _make_boss_ally(name: str = "Acolyte") -> Combatant:
    """A living minion on the boss's side (ENEMY)."""
    ally = _make_pc(name)
    return ally.model_copy(update={"side": CombatSide.ENEMY})


def _attack(target: str, weapon: str = "Greataxe") -> TacticalDecision:
    return TacticalDecision(
        action_type="attack",
        target_name=target,
        weapon_name=weapon,
        reasoning="Validation test decision.",
    )


class TestDecisionValidationTargets:
    """The engine rejects decisions aimed at dead / fled / allied combatants."""

    def test_rejects_dead_target_then_accepts_valid_retry(self) -> None:
        boss = _make_boss()
        alive = _make_pc("Thorin")
        dead = _make_pc("Ghost")
        dead.is_alive = False
        state = _state([boss, alive, dead])

        tactician = MagicMock()
        tactician.decide.side_effect = [_attack("Ghost"), _attack("Thorin")]

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 2
        assert plan.target_name == "Thorin"

    def test_rejects_fled_target(self) -> None:
        boss = _make_boss()
        alive = _make_pc("Thorin")
        coward = _make_pc("Coward")
        coward.fled = True
        state = _state([boss, alive, coward])

        tactician = MagicMock()
        tactician.decide.side_effect = [_attack("Coward"), _attack("Thorin")]

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 2
        assert plan.target_name == "Thorin"

    def test_rejects_same_side_target(self) -> None:
        boss = _make_boss()
        ally = _make_boss_ally("Acolyte")
        pc = _make_pc("Thorin")
        state = _state([boss, ally, pc])

        tactician = MagicMock()
        tactician.decide.side_effect = [_attack("Acolyte"), _attack("Thorin")]

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 2
        assert plan.target_name == "Thorin"

    def test_rejects_attack_without_target(self) -> None:
        boss = _make_boss()
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        no_target = TacticalDecision(
            action_type="attack",
            weapon_name="Greataxe",
            reasoning="Swinging at nobody in particular.",
        )
        tactician = MagicMock()
        tactician.decide.side_effect = [no_target, _attack("Thorin")]

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 2
        assert plan.target_name == "Thorin"

    def test_rejects_unknown_target_engine_side(self) -> None:
        """The engine must not trust the ai-layer name check."""
        boss = _make_boss()
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.side_effect = [_attack("Nobody"), _attack("Thorin")]

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 2
        assert plan.target_name == "Thorin"


class TestDecisionValidationRange:
    """Melee decisions across zones are rejected — same gate as players."""

    def test_rejects_melee_attack_across_zones(self) -> None:
        location = _make_linear_location(zone_count=2)
        boss = _make_boss()
        boss.current_zone = "Z1"
        pc = _make_pc("Thorin")
        pc.current_zone = "Z2"
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.side_effect = [
            _attack("Thorin", weapon="Greataxe"),
            _attack("Thorin", weapon="Greataxe"),
        ]

        plan = decide_boss_action(boss, state, location=location, tactician=tactician)

        # Both attempts invalid → scripted fallback took over.
        assert tactician.decide.call_count == 2
        assert "LLM fallback" in plan.rationale

    def test_allows_ranged_attack_across_zones(self) -> None:
        location = _make_linear_location(zone_count=2)
        boss = _make_boss()
        assert boss.stat_block is not None
        boss.stat_block.attacks.append(
            NPCAttack(
                name="Longbow",
                damage_dice="1d8+2",
                damage_type=DamageType.PIERCING,
                to_hit_bonus=5,
                range_type="ranged",
                range_value=150,
            )
        )
        boss.current_zone = "Z1"
        pc = _make_pc("Thorin")
        pc.current_zone = "Z2"
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.return_value = _attack("Thorin", weapon="Longbow")

        plan = decide_boss_action(boss, state, location=location, tactician=tactician)

        assert tactician.decide.call_count == 1
        assert plan.weapon_name == "Longbow"

    def test_allows_melee_attack_same_zone(self) -> None:
        location = _make_linear_location(zone_count=2)
        boss = _make_boss()
        boss.current_zone = "Z1"
        pc = _make_pc("Thorin")
        pc.current_zone = "Z1"
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.return_value = _attack("Thorin", weapon="Greataxe")

        plan = decide_boss_action(boss, state, location=location, tactician=tactician)

        assert tactician.decide.call_count == 1
        assert plan.target_name == "Thorin"

    def test_rejects_unknown_weapon_engine_side(self) -> None:
        boss = _make_boss()
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.side_effect = [
            _attack("Thorin", weapon="Excalibur"),
            _attack("Thorin", weapon="Greataxe"),
        ]

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 2
        assert plan.weapon_name == "Greataxe"


class TestDecisionValidationSignatureBudget:
    """A signature with no uses left cannot be picked again (audit H19)."""

    def _signature_decision(self, target: str = "Thorin") -> TacticalDecision:
        return TacticalDecision(
            action_type="signature",
            target_name=target,
            signature_name="Cleave",
            reasoning="Spamming the once-per-combat nuke.",
        )

    def test_rejects_signature_out_of_budget(self) -> None:
        boss = _make_boss()
        assert boss.stat_block is not None
        boss.stat_block.signature_abilities[0].uses_remaining = 0
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.side_effect = [
            self._signature_decision(),
            _attack("Thorin"),
        ]

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 2
        assert plan.signature_name is None
        assert plan.weapon_name == "Greataxe"

    def test_allows_signature_with_budget(self) -> None:
        boss = _make_boss()
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.return_value = self._signature_decision()

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 1
        assert plan.signature_name == "Cleave"

    def test_rejects_unknown_signature_engine_side(self) -> None:
        boss = _make_boss()
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        unknown = TacticalDecision(
            action_type="signature",
            target_name="Thorin",
            signature_name="Meteor Swarm",
            reasoning="Casting a spell I never had.",
        )
        tactician = MagicMock()
        tactician.decide.side_effect = [unknown, _attack("Thorin")]

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 2
        assert plan.signature_name is None

    def test_rejects_harmful_signature_on_ally(self) -> None:
        boss = _make_boss()
        ally = _make_boss_ally("Acolyte")
        pc = _make_pc("Thorin")
        state = _state([boss, ally, pc])

        tactician = MagicMock()
        tactician.decide.side_effect = [
            self._signature_decision(target="Acolyte"),
            _attack("Thorin"),
        ]

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 2
        assert plan.signature_name is None

    def test_allows_heal_signature_on_ally(self) -> None:
        boss = _make_boss()
        assert boss.stat_block is not None
        boss.stat_block.signature_abilities.append(
            SignatureAbility(
                name="Dark Mending",
                description="Heals an ally with shadow magic.",
                usage="per_combat",
                uses_remaining=1,
                effects=[
                    SignatureAbilityEffect(kind="heal", dice="2d6"),
                ],
            )
        )
        ally = _make_boss_ally("Acolyte")
        ally.character.hp = 5
        pc = _make_pc("Thorin")
        state = _state([boss, ally, pc])

        decision = TacticalDecision(
            action_type="signature",
            target_name="Acolyte",
            signature_name="Dark Mending",
            reasoning="Keeping my acolyte standing.",
        )
        tactician = MagicMock()
        tactician.decide.return_value = decision

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 1
        assert plan.signature_name == "Dark Mending"
        assert plan.target_name == "Acolyte"


class TestDecisionValidationMove:
    """Move decisions must point at an existing, adjacent zone."""

    def _move(self, zone: str) -> TacticalDecision:
        return TacticalDecision(
            action_type="move",
            move_to_zone=zone,
            reasoning="Repositioning for next round.",
        )

    def test_rejects_unknown_zone(self) -> None:
        location = _make_linear_location(zone_count=3)
        boss = _make_boss()
        boss.current_zone = "Z1"
        pc = _make_pc("Thorin")
        pc.current_zone = "Z3"
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.side_effect = [self._move("Atlantis"), self._move("Z2")]

        plan = decide_boss_action(boss, state, location=location, tactician=tactician)

        assert tactician.decide.call_count == 2
        assert plan.move_to_zone == "Z2"

    def test_rejects_non_adjacent_zone(self) -> None:
        location = _make_linear_location(zone_count=3)
        boss = _make_boss()
        boss.current_zone = "Z1"
        pc = _make_pc("Thorin")
        pc.current_zone = "Z3"
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.side_effect = [self._move("Z3"), self._move("Z2")]

        plan = decide_boss_action(boss, state, location=location, tactician=tactician)

        assert tactician.decide.call_count == 2
        assert plan.move_to_zone == "Z2"

    def test_move_without_zones_is_unrestricted(self) -> None:
        """Zoneless combat: the engine has no map to validate against."""
        boss = _make_boss()
        pc = _make_pc("Thorin")
        state = _state([boss, pc])

        tactician = MagicMock()
        tactician.decide.return_value = self._move("anywhere")

        plan = decide_boss_action(boss, state, location=None, tactician=tactician)

        assert tactician.decide.call_count == 1
        assert plan.action_type == ActionType.MOVE
