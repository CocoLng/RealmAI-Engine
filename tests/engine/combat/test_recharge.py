"""Turn-start recharge roll for ``recharge_5_6`` signature abilities.

SRD 5e: at the start of the creature's turn, roll 1d6 — on 5-6 the spent
ability recharges. Until 2026-07-19 the roll existed nowhere in the engine
and ``recharge_5_6`` was budgeted like ``per_combat`` (1 use, never back).
``advance_turn`` now rolls at the incoming combatant's turn start, exactly
where legendary points refill.
"""

from __future__ import annotations

import pytest

from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import (
    CombatSide,
    CombatState,
    Combatant,
    advance_turn,
)
from engine.dice import DiceResult
from engine.inventory import DamageType, create_inventory
from engine.npc_stat_block import (
    BehaviorProfile,
    NPCAttack,
    NPCStatBlock,
    NPCTier,
    SignatureAbility,
    SignatureAbilityEffect,
    SignatureUsage,
)


def _breath(usage: SignatureUsage, uses_remaining: int | None) -> SignatureAbility:
    return SignatureAbility(
        name="Souffle de givre",
        usage=usage,
        uses_remaining=uses_remaining,
        effects=[
            SignatureAbilityEffect(
                kind="aoe_damage",
                dice="3d6",
                damage_type=DamageType.COLD,
                target_scope="zone",
            ),
        ],
    )


def _npc(ability: SignatureAbility, tier: NPCTier = NPCTier.ELITE) -> Combatant:
    scores = AbilityScores(STR=16, DEX=12, CON=16, INT=8, WIS=10, CHA=8)
    char = create_character("Wyrm", Race.HUMAN, CharacterClass.FIGHTER, scores)
    char.hp = char.max_hp = 40
    stat_block = NPCStatBlock(
        tier=tier,
        archetype="test_wyrm",
        attacks=[
            NPCAttack(
                name="Morsure",
                damage_dice="1d10+3",
                damage_type=DamageType.PIERCING,
                to_hit_bonus=5,
            ),
        ],
        signature_abilities=[ability],
        behavior_profile=BehaviorProfile.AGGRESSIVE,
    )
    return Combatant(
        name="Wyrm",
        side=CombatSide.ENEMY,
        character=char,
        inventory=create_inventory(),
        stat_block=stat_block,
    )


def _pc(name: str = "Aria") -> Combatant:
    scores = AbilityScores(STR=14, DEX=12, CON=14, INT=10, WIS=10, CHA=10)
    char = create_character(name, Race.HUMAN, CharacterClass.FIGHTER, scores)
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=create_inventory(),
    )


def _pin_d6(monkeypatch: pytest.MonkeyPatch, total: int) -> list[str]:
    """Pin engine.combat.roll and record the expressions it was asked."""
    asked: list[str] = []

    def _fake(expr: str) -> DiceResult:
        asked.append(expr)
        return DiceResult(expression=expr, rolls=[total], total=total)

    monkeypatch.setattr("engine.combat.roll", _fake)
    return asked


def _to_npc_turn(state: CombatState) -> None:
    """PC has index 0 — one advance lands on the NPC."""
    advance_turn(state)


class TestRechargeRoll:
    def test_roll_of_5_restores_one_use_and_queues_cue(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        npc = _npc(_breath("recharge_5_6", 0))
        state = CombatState(
            combatants=[_pc(), npc], round_number=1, current_turn_index=0,
        )
        _pin_d6(monkeypatch, 5)

        _to_npc_turn(state)

        assert npc.stat_block is not None
        assert npc.stat_block.signature_abilities[0].uses_remaining == 1
        assert any(
            "Souffle de givre" in cue for cue in state.pending_legendary_summaries
        )

    def test_roll_of_4_stays_spent_and_silent(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        npc = _npc(_breath("recharge_5_6", 0))
        state = CombatState(
            combatants=[_pc(), npc], round_number=1, current_turn_index=0,
        )
        _pin_d6(monkeypatch, 4)

        _to_npc_turn(state)

        assert npc.stat_block is not None
        assert npc.stat_block.signature_abilities[0].uses_remaining == 0
        assert not any(
            "Souffle de givre" in cue for cue in state.pending_legendary_summaries
        )

    def test_charged_ability_is_not_rerolled_and_never_stacks(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        npc = _npc(_breath("recharge_5_6", 1))
        state = CombatState(
            combatants=[_pc(), npc], round_number=1, current_turn_index=0,
        )
        asked = _pin_d6(monkeypatch, 6)

        _to_npc_turn(state)

        assert npc.stat_block is not None
        assert npc.stat_block.signature_abilities[0].uses_remaining == 1
        assert "1d6" not in asked  # no pointless recharge roll

    def test_per_combat_never_recharges(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        npc = _npc(_breath("per_combat", 0))
        state = CombatState(
            combatants=[_pc(), npc], round_number=1, current_turn_index=0,
        )
        _pin_d6(monkeypatch, 6)

        _to_npc_turn(state)

        assert npc.stat_block is not None
        assert npc.stat_block.signature_abilities[0].uses_remaining == 0

    def test_pc_without_stat_block_is_untouched(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Advancing onto a plain PC (stat_block=None) must not roll."""
        npc = _npc(_breath("recharge_5_6", 0))
        state = CombatState(
            combatants=[npc, _pc()], round_number=1, current_turn_index=0,
        )
        asked = _pin_d6(monkeypatch, 6)

        advance_turn(state)  # NPC index 0 → lands on the PC

        assert "1d6" not in asked
