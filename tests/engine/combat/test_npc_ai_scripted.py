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
    SignatureAbility,
    SignatureAbilityEffect,
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


# ---------------------------------------------------------------------------
# Tests — execution summaries (H14 reliquat)
# ---------------------------------------------------------------------------


def _make_elite_with_signature(
    name: str = "Brute",
    signature: SignatureAbility | None = None,
) -> Combatant:
    """Elite-tier combatant carrying one signature ability (or none)."""
    scores = AbilityScores(STR=14, DEX=12, CON=14, INT=10, WIS=10, CHA=10)
    char = create_character(name, Race.HUMAN, CharacterClass.FIGHTER, scores)
    inv = create_inventory()
    stat_block = NPCStatBlock(
        tier=NPCTier.ELITE,
        archetype="brute",
        multiattack_count=1,
        attacks=[
            NPCAttack(
                name="Maul",
                damage_dice="2d6+3",
                damage_type=DamageType.BLUDGEONING,
                to_hit_bonus=5,
                range_type="melee",
            ),
        ],
        signature_abilities=[signature] if signature is not None else [],
    )
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
        stat_block=stat_block,
    )


def _damage_signature(name: str = "Cleave") -> SignatureAbility:
    return SignatureAbility(
        name=name,
        description="A heavy swing.",
        usage="per_combat",
        uses_remaining=1,
        effects=[
            SignatureAbilityEffect(
                kind="damage",
                dice="2d8+3",
                damage_type=DamageType.SLASHING,
                target_scope="single",
            ),
        ],
    )


class TestExecutionSummariesFrench:
    """H14 (reliquat scripted.py) — execute_action_plan summaries are
    player-visible: the TurnManager posts them verbatim in the Discord
    channel (📜 prefix). They must be clean French — no English, no
    internal enum values or repr diagnostics."""

    def test_dodge_summary_is_french(self) -> None:
        goblin = _make_melee_goblin()
        pc = _make_pc("Thorin")
        state = _make_zoneless_state([goblin, pc])

        plan = NPCActionPlan(action_type=ActionType.DEFEND, rationale="blocked")
        summary = execute_action_plan(goblin, plan, state, location=None)

        assert summary == "Goblin esquive"

    def test_unsupported_action_summary_is_french_without_internal_value(
        self,
    ) -> None:
        """Unsupported plan types degrade to a clean French no-op line —
        the internal ActionType value must not leak to the channel."""
        goblin = _make_melee_goblin()
        pc = _make_pc("Thorin")
        state = _make_zoneless_state([goblin, pc])

        plan = NPCActionPlan(action_type=ActionType.TALK, rationale="?")
        summary = execute_action_plan(goblin, plan, state, location=None)

        assert summary == "Goblin ne fait rien"
        assert "does nothing" not in summary
        assert ActionType.TALK.value not in summary

    def test_attack_hit_summary_is_french(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hit wording mirrors the TurnManager embed path: 'X touche Y
        avec Z — N dégâts'."""
        goblin = _make_melee_goblin()
        pc = _make_pc("Thorin", hp=20, ac=10)
        state = _make_zoneless_state([goblin, pc])

        from engine.dice import D20CheckResult, RollOutcome

        monkeypatch.setattr(
            "engine.combat.roll_check",
            lambda expr, dc: D20CheckResult(
                expression=expr, rolls=[15], modifier=4, total=19,
                dc=dc, outcome=RollOutcome.SUCCESS, margin=19 - dc,
            ),
        )
        monkeypatch.setattr(
            "engine.combat.roll",
            lambda expr: DiceResult(expression=expr, rolls=[5], total=5),
        )

        plan = NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name="Thorin",
            weapon_name="Scimitar",
            rationale="test",
        )
        summary = execute_action_plan(goblin, plan, state, location=None)

        assert summary == "Goblin touche Thorin avec Scimitar — 5 dégâts"

    def test_attack_miss_summary_is_french(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        goblin = _make_melee_goblin()
        pc = _make_pc("Thorin", hp=20, ac=18)
        state = _make_zoneless_state([goblin, pc])

        from engine.dice import D20CheckResult, RollOutcome

        monkeypatch.setattr(
            "engine.combat.roll_check",
            lambda expr, dc: D20CheckResult(
                expression=expr, rolls=[3], modifier=4, total=7,
                dc=dc, outcome=RollOutcome.FAILURE, margin=7 - dc,
            ),
        )

        plan = NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name="Thorin",
            weapon_name="Scimitar",
            rationale="test",
        )
        summary = execute_action_plan(goblin, plan, state, location=None)

        assert summary == "Goblin rate Thorin avec Scimitar"

    def test_attack_unknown_target_summary_is_french(self) -> None:
        """A badly pointed plan degrades to clean French — the repr
        diagnostic goes to the log, not to the players."""
        goblin = _make_melee_goblin()
        pc = _make_pc("Thorin")
        state = _make_zoneless_state([goblin, pc])

        plan = NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name="Fantôme",
            weapon_name="Scimitar",
            rationale="test",
        )
        summary = execute_action_plan(goblin, plan, state, location=None)

        assert summary == "Goblin ne trouve aucune cible"
        assert "could not find" not in summary

    def test_attack_unknown_weapon_summary_is_french(self) -> None:
        goblin = _make_melee_goblin()
        pc = _make_pc("Thorin")
        state = _make_zoneless_state([goblin, pc])

        plan = NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name="Thorin",
            weapon_name="Halberd",
            rationale="test",
        )
        summary = execute_action_plan(goblin, plan, state, location=None)

        assert summary == "Goblin ne peut pas attaquer"
        assert "has no" not in summary

    def test_move_summary_is_french(self) -> None:
        location = _make_linear_location(zone_count=2)
        goblin = _make_melee_goblin(zone="Z1")
        pc = _make_pc("Thorin", zone="Z2")
        state = _make_zoneless_state([goblin, pc])

        plan = NPCActionPlan(
            action_type=ActionType.MOVE, move_to_zone="Z2", rationale="test",
        )
        summary = execute_action_plan(goblin, plan, state, location=location)

        assert summary == "Goblin se déplace vers Z2"

    def test_blocked_move_summary_is_french(self) -> None:
        goblin = _make_melee_goblin()
        pc = _make_pc("Thorin")
        state = _make_zoneless_state([goblin, pc])

        plan = NPCActionPlan(action_type=ActionType.MOVE, rationale="test")
        summary = execute_action_plan(goblin, plan, state, location=None)

        assert summary == "Goblin ne peut pas se déplacer"
        assert "cannot" not in summary

    def test_signature_wrapper_summary_is_french(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The wrapper around elite.py's French summaries must be French
        too: 'X utilise Y : ...'."""
        brute = _make_elite_with_signature(signature=_damage_signature())
        pc = _make_pc("Thorin", hp=20)
        state = _make_zoneless_state([brute, pc])

        monkeypatch.setattr(
            "engine.npc_ai.elite.roll",
            lambda expr: DiceResult(expression=expr, rolls=[11], total=11),
        )

        plan = NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name="Thorin",
            signature_name="Cleave",
            rationale="test",
        )
        summary = execute_action_plan(brute, plan, state, location=None)

        assert summary == "Brute utilise Cleave : Thorin subit 11 dégâts"
        assert "uses" not in summary

    def test_signature_without_effect_summary_is_french(self) -> None:
        """An empty-effects signature reads 'aucun effet', not 'no effect'."""
        empty_sig = SignatureAbility(
            name="Posture",
            description="Does nothing mechanical.",
            usage="at_will",
            effects=[],
        )
        brute = _make_elite_with_signature(signature=empty_sig)
        pc = _make_pc("Thorin")
        state = _make_zoneless_state([brute, pc])

        plan = NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name="Thorin",
            signature_name="Posture",
            rationale="test",
        )
        summary = execute_action_plan(brute, plan, state, location=None)

        assert summary == "Brute utilise Posture : aucun effet"
        assert "no effect" not in summary

    def test_signature_unknown_name_summary_is_french(self) -> None:
        brute = _make_elite_with_signature(signature=_damage_signature())
        pc = _make_pc("Thorin")
        state = _make_zoneless_state([brute, pc])

        plan = NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name="Thorin",
            signature_name="Tornade",
            rationale="test",
        )
        summary = execute_action_plan(brute, plan, state, location=None)

        assert summary == "Brute ne peut pas utiliser Tornade"
        assert "has no signature" not in summary

    def test_signature_without_stat_block_summary_is_french(self) -> None:
        pc_like = _make_pc("Imposteur")
        pc_like.side = CombatSide.ENEMY
        target = _make_pc("Thorin")
        state = _make_zoneless_state([pc_like, target])

        plan = NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name="Thorin",
            signature_name="Cleave",
            rationale="test",
        )
        summary = execute_action_plan(pc_like, plan, state, location=None)

        assert summary == "Imposteur ne peut pas utiliser Cleave"
        assert "stat block" not in summary
