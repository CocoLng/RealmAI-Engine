"""Tests for engine/npc_stat_block.py."""

import pytest
from pydantic import ValidationError

from engine.inventory import DamageType
from engine.npc_stat_block import (
    BehaviorProfile,
    LegendaryAction,
    NPCAttack,
    NPCStatBlock,
    NPCTier,
    PhaseTransition,
    SignatureAbility,
    SignatureAbilityEffect,
)


# ---------------------------------------------------------------------------
# NPCAttack
# ---------------------------------------------------------------------------


class TestNPCAttack:
    def test_basic_melee(self) -> None:
        atk = NPCAttack(
            name="Scimitar",
            damage_dice="1d6+1",
            damage_type=DamageType.SLASHING,
            to_hit_bonus=3,
        )
        assert atk.range_type == "melee"
        assert atk.range_value is None

    def test_ranged_with_range_value(self) -> None:
        atk = NPCAttack(
            name="Shortbow",
            damage_dice="1d6+2",
            damage_type=DamageType.PIERCING,
            to_hit_bonus=4,
            range_type="ranged",
            range_value=80,
        )
        assert atk.range_type == "ranged"
        assert atk.range_value == 80


# ---------------------------------------------------------------------------
# NPCStatBlock construction per tier
# ---------------------------------------------------------------------------


class TestMinionStatBlock:
    def test_minimal_minion(self) -> None:
        block = NPCStatBlock(
            tier=NPCTier.MINION,
            archetype="commoner",
            attacks=[
                NPCAttack(
                    name="Club",
                    damage_dice="1d4",
                    damage_type=DamageType.BLUDGEONING,
                    to_hit_bonus=1,
                ),
            ],
        )
        assert block.tier == NPCTier.MINION
        assert block.multiattack_count == 1
        assert block.legendary_points_per_round == 0
        assert block.phases == []
        assert block.signature_abilities == []
        assert block.behavior_profile == BehaviorProfile.AGGRESSIVE


class TestEliteStatBlock:
    def test_elite_with_signature(self) -> None:
        block = NPCStatBlock(
            tier=NPCTier.ELITE,
            archetype="captain",
            multiattack_count=2,
            attacks=[
                NPCAttack(
                    name="Longsword",
                    damage_dice="1d8+3",
                    damage_type=DamageType.SLASHING,
                    to_hit_bonus=5,
                ),
            ],
            signature_abilities=[
                SignatureAbility(
                    name="Rally",
                    description="Heal and remove Frightened.",
                    usage="per_combat",
                    uses_remaining=1,
                    effects=[
                        SignatureAbilityEffect(
                            kind="heal",
                            dice="1d8+3",
                            target_scope="all_allies_in_zone",
                        ),
                    ],
                ),
            ],
            behavior_profile=BehaviorProfile.SUPPORT,
        )
        assert block.tier == NPCTier.ELITE
        assert len(block.signature_abilities) == 1
        assert block.signature_abilities[0].uses_remaining == 1


class TestBossStatBlock:
    def test_full_boss(self) -> None:
        block = NPCStatBlock(
            tier=NPCTier.BOSS,
            archetype="villain",
            multiattack_count=3,
            attacks=[
                NPCAttack(
                    name="Dread Blade",
                    damage_dice="2d6+4",
                    damage_type=DamageType.SLASHING,
                    to_hit_bonus=7,
                ),
            ],
            signature_abilities=[
                SignatureAbility(
                    name=f"Signature {i}",
                    usage="per_combat",
                    uses_remaining=1,
                )
                for i in range(3)
            ],
            legendary_actions=[
                LegendaryAction(
                    name=f"Legendary {i}",
                    cost=1,
                    description="A legendary strike.",
                )
                for i in range(3)
            ],
            legendary_points_per_round=3,
            phases=[
                PhaseTransition(trigger_hp_percent=75, narrative_cue="Enraged."),
                PhaseTransition(trigger_hp_percent=25, narrative_cue="Desperate."),
            ],
            behavior_profile=BehaviorProfile.TACTICAL,
        )
        assert block.multiattack_count == 3
        assert len(block.signature_abilities) == 3
        assert len(block.legendary_actions) == 3
        assert block.legendary_points_per_round == 3
        assert len(block.phases) == 2


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestPhaseTransitionValidation:
    def test_rejects_zero_percent(self) -> None:
        with pytest.raises(ValidationError):
            PhaseTransition(trigger_hp_percent=0)

    def test_rejects_hundred_percent(self) -> None:
        with pytest.raises(ValidationError):
            PhaseTransition(trigger_hp_percent=100)

    def test_accepts_mid_range(self) -> None:
        pt = PhaseTransition(trigger_hp_percent=50)
        assert pt.triggered is False


class TestLegendaryActionValidation:
    def test_rejects_cost_zero(self) -> None:
        with pytest.raises(ValidationError):
            LegendaryAction(name="Bad", cost=0)

    def test_rejects_cost_four(self) -> None:
        with pytest.raises(ValidationError):
            LegendaryAction(name="Bad", cost=4)

    def test_accepts_cost_one_through_three(self) -> None:
        for c in (1, 2, 3):
            la = LegendaryAction(name="OK", cost=c)
            assert la.cost == c


class TestNPCStatBlockValidation:
    def test_rejects_multiattack_zero(self) -> None:
        with pytest.raises(ValidationError):
            NPCStatBlock(tier=NPCTier.MINION, archetype="x", multiattack_count=0)

    def test_rejects_aggression_threshold_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            NPCStatBlock(
                tier=NPCTier.MINION,
                archetype="x",
                aggression_threshold=31,
            )

    def test_rejects_empty_archetype(self) -> None:
        with pytest.raises(ValidationError):
            NPCStatBlock(tier=NPCTier.MINION, archetype="")


# ---------------------------------------------------------------------------
# Regression: NPC without stat_block still valid
# ---------------------------------------------------------------------------


class TestNPCWithoutStatBlock:
    def test_npc_sans_stat_block_still_valid(self) -> None:
        from engine.character import AbilityScores, Race
        from world.npc import NPC, NPCDisposition

        npc = NPC(
            name="Old Marek",
            race=Race.HUMAN,
            ability_scores=AbilityScores(
                STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10
            ),
            hp=8,
            max_hp=8,
            ac=10,
            disposition=NPCDisposition.FRIENDLY,
        )
        assert npc.stat_block is None

    def test_npc_with_stat_block(self) -> None:
        from engine.character import AbilityScores, Race
        from world.npc import NPC, NPCDisposition

        block = NPCStatBlock(
            tier=NPCTier.MINION,
            archetype="bandit",
            attacks=[
                NPCAttack(
                    name="Dagger",
                    damage_dice="1d4+1",
                    damage_type=DamageType.PIERCING,
                    to_hit_bonus=3,
                ),
            ],
        )
        npc = NPC(
            name="Bandit",
            race=Race.HUMAN,
            ability_scores=AbilityScores(
                STR=11, DEX=12, CON=12, INT=10, WIS=10, CHA=10
            ),
            hp=11,
            max_hp=11,
            ac=12,
            disposition=NPCDisposition.HOSTILE,
            stat_block=block,
        )
        assert npc.stat_block is not None
        assert npc.stat_block.archetype == "bandit"
