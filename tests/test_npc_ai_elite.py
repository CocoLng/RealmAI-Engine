"""Tests for Task 51 — elite behavior profiles + signature executor.

Covers ``engine.npc_ai.elite``:
- ``decide_elite_action`` dispatcher over AGGRESSIVE / DEFENSIVE / SUPPORT /
  TACTICAL profiles.
- ``execute_signature_ability`` for the MVP effect kinds (damage, heal,
  condition with save). Non-MVP kinds (``aoe_damage``, ``buff``, ``debuff``,
  ``move``) degrade gracefully with a warning and a fallback summary.
- ``decide_action_for`` tier dispatcher in ``engine.npc_ai.scripted``.
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
from engine.conditions import (
    ActiveCondition,
    ConditionType,
    has_condition,
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
from engine.npc_ai.elite import (
    decide_elite_action,
    execute_signature_ability,
)
from engine.npc_ai.scripted import decide_action_for
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


def _make_pc(
    name: str,
    hp: int = 20,
    ac: int = 15,
    zone: str | None = None,
) -> Combatant:
    scores = AbilityScores(STR=14, DEX=12, CON=14, INT=10, WIS=10, CHA=10)
    scores = apply_racial_bonuses(scores, Race.HUMAN)
    char = create_character(name, Race.HUMAN, CharacterClass.FIGHTER, scores)
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


def _make_elite(
    name: str,
    profile: BehaviorProfile,
    signatures: list[SignatureAbility] | None = None,
    hp: int | None = None,
    side: CombatSide = CombatSide.ENEMY,
) -> Combatant:
    scores = AbilityScores(STR=14, DEX=12, CON=14, INT=10, WIS=10, CHA=10)
    char = create_character(name, Race.HUMAN, CharacterClass.FIGHTER, scores)
    if hp is not None:
        char.hp = hp
        char.max_hp = max(hp, char.max_hp)
    char.ac = 15
    inv = create_inventory()
    stat_block = NPCStatBlock(
        tier=NPCTier.ELITE,
        archetype=f"test_{profile.value}",
        multiattack_count=2,
        attacks=[
            NPCAttack(
                name="Longsword",
                damage_dice="1d8+3",
                damage_type=DamageType.SLASHING,
                to_hit_bonus=5,
            ),
        ],
        signature_abilities=signatures or [],
        behavior_profile=profile,
    )
    return Combatant(
        name=name,
        side=side,
        character=char,
        inventory=inv,
        stat_block=stat_block,
    )


def _make_minion(name: str, hp: int = 10) -> Combatant:
    """Minimal minion with a melee attack — used for the dispatcher tests."""
    scores = AbilityScores(STR=10, DEX=12, CON=10, INT=8, WIS=8, CHA=8)
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
            ),
        ],
    )
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=inv,
        stat_block=stat_block,
    )


def _state(combatants: list[Combatant]) -> CombatState:
    return CombatState(combatants=combatants, round_number=1, current_turn_index=0)


def _damage_signature(name: str = "Cleave", uses_remaining: int | None = 1) -> SignatureAbility:
    return SignatureAbility(
        name=name,
        description="A heavy swing.",
        usage="per_combat",
        uses_remaining=uses_remaining,
        effects=[
            SignatureAbilityEffect(
                kind="damage",
                dice="2d8+3",
                damage_type=DamageType.SLASHING,
                target_scope="single",
            ),
        ],
    )


def _heal_signature(name: str = "Rally", uses_remaining: int | None = 1) -> SignatureAbility:
    return SignatureAbility(
        name=name,
        description="Heal an ally.",
        usage="per_combat",
        uses_remaining=uses_remaining,
        effects=[
            SignatureAbilityEffect(
                kind="heal",
                dice="1d8+3",
                target_scope="single",
            ),
        ],
    )


def _condition_signature_with_save(
    name: str = "Menace",
    dc: int = 15,
    uses_remaining: int | None = 1,
) -> SignatureAbility:
    return SignatureAbility(
        name=name,
        description="Inflict Frightened.",
        usage="per_combat",
        uses_remaining=uses_remaining,
        effects=[
            SignatureAbilityEffect(
                kind="condition",
                condition_name="Frightened",
                condition_duration_rounds=2,
                save_ability="WIS",
                save_dc=dc,
                target_scope="single",
            ),
        ],
    )


def _aoe_damage_signature() -> SignatureAbility:
    """Non-MVP kind — should fall back with a warning."""
    return SignatureAbility(
        name="Firestorm",
        description="AoE fire.",
        usage="per_combat",
        uses_remaining=1,
        effects=[
            SignatureAbilityEffect(
                kind="aoe_damage",
                dice="2d6",
                damage_type=DamageType.FIRE,
                target_scope="zone",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# decide_elite_action — AGGRESSIVE
# ---------------------------------------------------------------------------


class TestDecideAggressive:
    def test_uses_damage_signature_when_available(self) -> None:
        sig = _damage_signature()
        attacker = _make_elite("Bruiser", BehaviorProfile.AGGRESSIVE, [sig])
        weak = _make_pc("Thorin", hp=5)
        state = _state([attacker, weak])

        plan = decide_elite_action(attacker, state, location=None)

        assert plan.action_type == ActionType.ATTACK
        assert plan.signature_name == "Cleave"
        assert plan.target_name == "Thorin"

    def test_falls_back_to_standard_attack_when_no_signature(self) -> None:
        attacker = _make_elite("Bruiser", BehaviorProfile.AGGRESSIVE, [])
        weak = _make_pc("Thorin", hp=5)
        state = _state([attacker, weak])

        plan = decide_elite_action(attacker, state, location=None)

        assert plan.action_type == ActionType.ATTACK
        assert plan.signature_name is None
        assert plan.weapon_name == "Longsword"
        assert plan.target_name == "Thorin"

    def test_aggressive_falls_back_to_signature_on_cooldown(self) -> None:
        """uses_remaining == 0 → treat as unavailable, use plain attack."""
        spent = _damage_signature(uses_remaining=0)
        attacker = _make_elite("Bruiser", BehaviorProfile.AGGRESSIVE, [spent])
        weak = _make_pc("Thorin", hp=5)
        state = _state([attacker, weak])

        plan = decide_elite_action(attacker, state, location=None)

        assert plan.signature_name is None


# ---------------------------------------------------------------------------
# decide_elite_action — DEFENSIVE
# ---------------------------------------------------------------------------


class TestDecideDefensive:
    def test_dodges_when_hp_low(self) -> None:
        attacker = _make_elite("Sentinel", BehaviorProfile.DEFENSIVE, [], hp=3)
        pc = _make_pc("Thorin", hp=20)
        state = _state([attacker, pc])

        plan = decide_elite_action(attacker, state, location=None)

        assert plan.action_type == ActionType.DEFEND

    def test_attacks_cautiously_when_healthy(self) -> None:
        attacker = _make_elite("Sentinel", BehaviorProfile.DEFENSIVE, [])
        pc = _make_pc("Thorin", hp=20)
        state = _state([attacker, pc])

        plan = decide_elite_action(attacker, state, location=None)

        assert plan.action_type == ActionType.ATTACK
        assert plan.target_name == "Thorin"
        assert plan.signature_name is None  # no signature = plain attack


# ---------------------------------------------------------------------------
# decide_elite_action — SUPPORT
# ---------------------------------------------------------------------------


class TestDecideSupport:
    def test_heals_wounded_ally(self) -> None:
        heal_sig = _heal_signature()
        medic = _make_elite("Priest", BehaviorProfile.SUPPORT, [heal_sig])
        wounded_ally = _make_elite("Warrior", BehaviorProfile.AGGRESSIVE, [], hp=5)
        pc = _make_pc("Thorin")
        state = _state([medic, wounded_ally, pc])

        plan = decide_elite_action(medic, state, location=None)

        assert plan.signature_name == "Rally"
        assert plan.target_name == "Warrior"

    def test_attacks_when_no_one_to_support(self) -> None:
        medic = _make_elite("Priest", BehaviorProfile.SUPPORT, [_heal_signature()])
        pc = _make_pc("Thorin", hp=20)
        state = _state([medic, pc])

        plan = decide_elite_action(medic, state, location=None)

        assert plan.action_type == ActionType.ATTACK
        assert plan.signature_name is None


# ---------------------------------------------------------------------------
# decide_elite_action — TACTICAL
# ---------------------------------------------------------------------------


class TestDecideTactical:
    def test_prioritizes_frightened_enemy(self) -> None:
        attacker = _make_elite("Trickster", BehaviorProfile.TACTICAL, [])
        strong_pc = _make_pc("Thorin", hp=30)
        fragile_pc = _make_pc("Elen", hp=20)
        # Fragile PC has lower HP but is not Frightened; Thorin is.
        strong_pc.conditions.append(
            ActiveCondition(
                condition_type=ConditionType.FRIGHTENED,
                duration_rounds=3,
            )
        )
        state = _state([attacker, strong_pc, fragile_pc])

        plan = decide_elite_action(attacker, state, location=None)

        assert plan.action_type == ActionType.ATTACK
        assert plan.target_name == "Thorin"  # frightened target preferred


# ---------------------------------------------------------------------------
# execute_signature_ability — MVP kinds
# ---------------------------------------------------------------------------


class TestExecuteSignature:
    def test_execute_damage_effect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        caster = _make_elite("Bruiser", BehaviorProfile.AGGRESSIVE, [_damage_signature()])
        target = _make_pc("Thorin", hp=20)
        state = _state([caster, target])

        def mock_roll(expr: str) -> DiceResult:
            return DiceResult(expression=expr, rolls=[11], total=11)

        monkeypatch.setattr("engine.npc_ai.elite.roll", mock_roll)

        sig = caster.stat_block.signature_abilities[0]  # type: ignore[union-attr]
        execute_signature_ability(caster, sig, [target], state)

        assert target.character.hp == 9

    def test_execute_heal_effect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        caster = _make_elite("Priest", BehaviorProfile.SUPPORT, [_heal_signature()])
        ally = _make_elite("Warrior", BehaviorProfile.AGGRESSIVE, [], hp=5, side=CombatSide.ENEMY)
        # Store max_hp so heal is visible
        ally.character.max_hp = 20
        state = _state([caster, ally])

        def mock_roll(expr: str) -> DiceResult:
            return DiceResult(expression=expr, rolls=[7], total=7)

        monkeypatch.setattr("engine.npc_ai.elite.roll", mock_roll)

        sig = caster.stat_block.signature_abilities[0]  # type: ignore[union-attr]
        execute_signature_ability(caster, sig, [ally], state)

        assert ally.character.hp == 12

    def test_execute_heal_caps_at_max_hp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        caster = _make_elite("Priest", BehaviorProfile.SUPPORT, [_heal_signature()])
        ally = _make_elite("Warrior", BehaviorProfile.AGGRESSIVE, [], hp=18, side=CombatSide.ENEMY)
        ally.character.max_hp = 20
        state = _state([caster, ally])

        def mock_roll(expr: str) -> DiceResult:
            return DiceResult(expression=expr, rolls=[20], total=20)

        monkeypatch.setattr("engine.npc_ai.elite.roll", mock_roll)

        sig = caster.stat_block.signature_abilities[0]  # type: ignore[union-attr]
        execute_signature_ability(caster, sig, [ally], state)

        assert ally.character.hp == 20

    def test_execute_condition_fail_save_applies_condition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        caster = _make_elite(
            "Scary",
            BehaviorProfile.TACTICAL,
            [_condition_signature_with_save(dc=30)],  # impossible DC = always fail
        )
        pc = _make_pc("Thorin", hp=20)
        state = _state([caster, pc])

        from engine.dice import D20CheckResult, RollOutcome

        def mock_roll_check(expr: str, dc: int) -> D20CheckResult:
            return D20CheckResult(
                expression=expr,
                rolls=[10],
                modifier=0,
                total=10,
                dc=dc,
                outcome=RollOutcome.FAILURE,
                margin=10 - dc,
            )

        monkeypatch.setattr("engine.npc_ai.elite.roll_check", mock_roll_check)

        sig = caster.stat_block.signature_abilities[0]  # type: ignore[union-attr]
        execute_signature_ability(caster, sig, [pc], state)

        assert has_condition(pc.conditions, ConditionType.FRIGHTENED)

    def test_execute_condition_success_save_no_condition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        caster = _make_elite(
            "Scary",
            BehaviorProfile.TACTICAL,
            [_condition_signature_with_save(dc=5)],  # trivial DC = always succeed
        )
        pc = _make_pc("Thorin", hp=20)
        state = _state([caster, pc])

        from engine.dice import D20CheckResult, RollOutcome

        def mock_roll_check(expr: str, dc: int) -> D20CheckResult:
            return D20CheckResult(
                expression=expr,
                rolls=[18],
                modifier=0,
                total=18,
                dc=dc,
                outcome=RollOutcome.SUCCESS,
                margin=18 - dc,
            )

        monkeypatch.setattr("engine.npc_ai.elite.roll_check", mock_roll_check)

        sig = caster.stat_block.signature_abilities[0]  # type: ignore[union-attr]
        execute_signature_ability(caster, sig, [pc], state)

        assert not has_condition(pc.conditions, ConditionType.FRIGHTENED)

    def test_signature_uses_remaining_decrements(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        caster = _make_elite("Bruiser", BehaviorProfile.AGGRESSIVE, [_damage_signature()])
        target = _make_pc("Thorin", hp=20)
        state = _state([caster, target])

        monkeypatch.setattr(
            "engine.npc_ai.elite.roll",
            lambda expr: DiceResult(expression=expr, rolls=[5], total=5),
        )

        sig = caster.stat_block.signature_abilities[0]  # type: ignore[union-attr]
        assert sig.uses_remaining == 1
        execute_signature_ability(caster, sig, [target], state)
        assert sig.uses_remaining == 0

    def test_at_will_signature_uses_remaining_stays_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sig = SignatureAbility(
            name="Basic Blast",
            description="At-will attack.",
            usage="at_will",
            uses_remaining=None,
            effects=[
                SignatureAbilityEffect(
                    kind="damage",
                    dice="1d6",
                    damage_type=DamageType.FORCE,
                    target_scope="single",
                ),
            ],
        )
        caster = _make_elite("Wizard", BehaviorProfile.AGGRESSIVE, [sig])
        target = _make_pc("Thorin", hp=20)
        state = _state([caster, target])

        monkeypatch.setattr(
            "engine.npc_ai.elite.roll",
            lambda expr: DiceResult(expression=expr, rolls=[3], total=3),
        )

        execute_signature_ability(caster, sig, [target], state)

        assert sig.uses_remaining is None

    def test_non_mvp_kind_logs_warning_and_falls_back(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """aoe_damage is not MVP — should log WARNING and return fallback summary."""
        sig = _aoe_damage_signature()
        caster = _make_elite("Mage", BehaviorProfile.AGGRESSIVE, [sig])
        target = _make_pc("Thorin", hp=20)
        state = _state([caster, target])

        import logging

        with caplog.at_level(logging.WARNING, logger="engine.npc_ai.elite"):
            summaries = execute_signature_ability(caster, sig, [target], state)

        # HP unchanged — effect was NOT applied
        assert target.character.hp == 20
        # A warning was logged
        assert any("aoe_damage" in rec.message for rec in caplog.records)
        # Fallback summary mentions the degradation
        assert any("fallback" in s.lower() for s in summaries)


# ---------------------------------------------------------------------------
# decide_action_for — tier dispatcher
# ---------------------------------------------------------------------------


class TestDecideActionForDispatcher:
    def test_minion_routes_to_minion_brain(self) -> None:
        goblin = _make_minion("Goblin")
        pc = _make_pc("Thorin")
        state = _state([goblin, pc])

        plan = decide_action_for(goblin, state, location=None)

        assert plan.action_type == ActionType.ATTACK
        assert plan.signature_name is None  # minion brain never uses signatures

    def test_elite_routes_to_elite_brain(self) -> None:
        sig = _damage_signature()
        elite = _make_elite("Bruiser", BehaviorProfile.AGGRESSIVE, [sig])
        pc = _make_pc("Thorin", hp=5)
        state = _state([elite, pc])

        plan = decide_action_for(elite, state, location=None)

        assert plan.signature_name == "Cleave"  # routed to elite brain

    def test_missing_stat_block_falls_back_to_minion(self) -> None:
        """Combatants without stat_block default to the minion heuristic."""
        bare = _make_pc("LooseCannon", hp=10)
        bare.side = CombatSide.ENEMY  # force enemy side so it has a "turn"
        pc = _make_pc("Thorin")
        state = _state([bare, pc])

        plan = decide_action_for(bare, state, location=None)

        # Minion brain: weapon_name is None (no stat block), but decision flows.
        assert plan.action_type in (ActionType.ATTACK, ActionType.DEFEND)
