"""Tests for bot/combat_end.py and bot/embeds/combat_end_embed.py (task 80).

Covers :func:`bot.combat_end.finalize_combat` (summary construction, XP
application, condition cleanup, idempotence) and the end-of-combat embed
builder (colors, titles, optional-field gating).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bot.combat_end import (
    CombatEndSummary,
    _TRANSIENT_CONDITIONS,
    finalize_combat,
)
from bot.embeds.combat_end_embed import build_combat_end_embed
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import (
    CombatEndReason,
    CombatSide,
    CombatState,
    Combatant,
)
from engine.conditions import ActiveCondition, ConditionType
from engine.inventory import DamageType, create_inventory
from engine.npc_stat_block import NPCAttack, NPCStatBlock, NPCTier


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _pc(
    name: str = "Aragorn",
    *,
    hp: int = 40,
    max_hp: int = 50,
    xp: int = 0,
) -> Combatant:
    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(
            STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=10,
        ),
    )
    char.hp = hp
    char.max_hp = max_hp
    char.xp = xp
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=create_inventory(),
        initiative=15,
    )


def _attack(name: str = "Griffe") -> NPCAttack:
    return NPCAttack(
        name=name,
        to_hit_bonus=4,
        damage_dice="1d6+2",
        damage_type=DamageType.SLASHING,
        range_type="melee",
    )


def _stat_block(tier: NPCTier, attack_name: str = "Griffe") -> NPCStatBlock:
    return NPCStatBlock(
        tier=tier,
        archetype="test",
        attacks=[_attack(attack_name)],
    )


def _enemy(
    name: str,
    *,
    tier: NPCTier = NPCTier.MINION,
    hp: int = 12,
    max_hp: int = 12,
    attack_name: str = "Griffe",
    with_stat_block: bool = True,
) -> Combatant:
    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(
            STR=10, DEX=14, CON=10, INT=10, WIS=10, CHA=10,
        ),
    )
    char.hp = hp
    char.max_hp = max_hp
    kwargs: dict = {
        "name": name,
        "side": CombatSide.ENEMY,
        "character": char,
        "inventory": create_inventory(),
        "initiative": 10,
    }
    if with_stat_block:
        kwargs["stat_block"] = _stat_block(tier, attack_name)
    return Combatant(**kwargs)


def _kill(combatant: Combatant) -> Combatant:
    combatant.is_alive = False
    combatant.character.hp = 0
    return combatant


def _session_with(state: CombatState) -> object:
    """Minimal stand-in for GameSession — finalize_combat only reads
    ``combat_state`` off it.
    """
    sess = MagicMock()
    sess.combat_state = state
    return sess


def _state(combatants: list[Combatant], *, round_number: int = 3) -> CombatState:
    return CombatState(combatants=combatants, round_number=round_number)


# ---------------------------------------------------------------------------
# finalize_combat — summary construction
# ---------------------------------------------------------------------------


class TestFinalizeCombatSummary:
    def test_victory_summary_categorises_combatants(self) -> None:
        pc = _pc("Aragorn")
        dead_enemy = _kill(_enemy("Goblin"))
        state = _state([pc, dead_enemy])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.VICTORY,
        )

        assert summary.reason == CombatEndReason.VICTORY
        assert summary.survivors_pc == ["Aragorn"]
        assert summary.killed_enemies == ["Goblin"]
        assert summary.killed_pcs == []
        assert summary.fled_pcs == []
        assert summary.rounds_taken == 3

    def test_defeat_summary_lists_killed_pcs(self) -> None:
        dead_pc = _kill(_pc("Aragorn"))
        enemy = _enemy("Goblin")  # still alive
        state = _state([dead_pc, enemy])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.DEFEAT,
        )

        assert summary.reason == CombatEndReason.DEFEAT
        assert summary.killed_pcs == ["Aragorn"]
        assert summary.survivors_enemy == ["Goblin"]
        assert summary.survivors_pc == []

    def test_fled_summary_lists_fled_pcs(self) -> None:
        pc = _pc("Aragorn")
        pc.fled = True
        enemy = _enemy("Goblin")
        state = _state([pc, enemy])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.FLED,
        )

        assert summary.reason == CombatEndReason.FLED
        assert summary.fled_pcs == ["Aragorn"]
        assert summary.survivors_pc == []

    def test_sets_end_reason_and_is_active_false(self) -> None:
        state = _state([_pc(), _kill(_enemy("Goblin"))])

        finalize_combat(_session_with(state), CombatEndReason.VICTORY)

        assert state.end_reason == CombatEndReason.VICTORY
        assert state.is_active is False


# ---------------------------------------------------------------------------
# finalize_combat — XP application
# ---------------------------------------------------------------------------


class TestFinalizeCombatXp:
    def test_xp_by_tier_minion_elite_boss(self) -> None:
        pc = _pc("Aragorn", xp=0)
        minion = _kill(_enemy("Goblin", tier=NPCTier.MINION))
        elite = _kill(_enemy("Brute", tier=NPCTier.ELITE))
        boss = _kill(_enemy("Tyrant", tier=NPCTier.BOSS))
        state = _state([pc, minion, elite, boss])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.VICTORY,
        )

        # 50 + 150 + 500 = 700, split among 1 survivor = 700.
        assert summary.xp_earned == 700
        assert pc.character.xp == 700

    def test_xp_split_among_multiple_survivors(self) -> None:
        pc1 = _pc("Aragorn", xp=0)
        pc2 = _pc("Legolas", xp=0)
        boss = _kill(_enemy("Tyrant", tier=NPCTier.BOSS))
        state = _state([pc1, pc2, boss])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.VICTORY,
        )

        # 500 / 2 = 250 each
        assert summary.xp_earned == 250
        assert pc1.character.xp == 250
        assert pc2.character.xp == 250

    def test_xp_not_granted_to_dead_pcs(self) -> None:
        dead_pc = _kill(_pc("Aragorn", xp=100))
        survivor = _pc("Legolas", xp=100)
        boss = _kill(_enemy("Tyrant", tier=NPCTier.BOSS))
        state = _state([dead_pc, survivor, boss])

        finalize_combat(_session_with(state), CombatEndReason.VICTORY)

        assert dead_pc.character.xp == 100  # unchanged
        assert survivor.character.xp == 100 + 500  # whole pot

    def test_xp_not_granted_to_fled_pcs(self) -> None:
        fled_pc = _pc("Aragorn", xp=100)
        fled_pc.fled = True
        survivor = _pc("Legolas", xp=100)
        minion = _kill(_enemy("Goblin", tier=NPCTier.MINION))
        state = _state([fled_pc, survivor, minion])

        finalize_combat(_session_with(state), CombatEndReason.VICTORY)

        assert fled_pc.character.xp == 100
        assert survivor.character.xp == 150  # 100 + 50

    def test_enemy_without_stat_block_grants_fallback_xp(self) -> None:
        pc = _pc("Aragorn", xp=0)
        dead_commoner = _kill(_enemy("Paysan", with_stat_block=False))
        state = _state([pc, dead_commoner])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.VICTORY,
        )

        assert summary.xp_earned == 25  # _XP_FALLBACK

    def test_no_surviving_pcs_yields_zero_xp_earned(self) -> None:
        dead_pc = _kill(_pc("Aragorn"))
        boss = _kill(_enemy("Tyrant", tier=NPCTier.BOSS))
        state = _state([dead_pc, boss])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.DEFEAT,
        )

        assert summary.xp_earned == 0

    def test_level_up_flagged_when_threshold_crossed(self) -> None:
        # Level 1 → level 2 threshold is 300 XP; give the PC 250 already,
        # then kill a boss (500 XP) → plenty over the threshold.
        pc = _pc("Aragorn", xp=250)
        boss = _kill(_enemy("Tyrant", tier=NPCTier.BOSS))
        state = _state([pc, boss])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.VICTORY,
        )

        assert "Aragorn" in summary.level_ups
        assert pc.character.xp == 750  # 250 + 500

    def test_no_level_up_when_below_threshold(self) -> None:
        pc = _pc("Aragorn", xp=0)
        minion = _kill(_enemy("Goblin", tier=NPCTier.MINION))
        state = _state([pc, minion])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.VICTORY,
        )

        assert summary.level_ups == []
        assert pc.character.xp == 50


# ---------------------------------------------------------------------------
# finalize_combat — loot
# ---------------------------------------------------------------------------


class TestFinalizeCombatLoot:
    def test_loot_from_killed_enemies_uses_primary_attack_name(self) -> None:
        pc = _pc()
        enemy1 = _kill(_enemy("Goblin", attack_name="Rusty Scimitar"))
        enemy2 = _kill(_enemy("Kobold", attack_name="Bone Dagger"))
        state = _state([pc, enemy1, enemy2])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.VICTORY,
        )

        assert summary.loot_items == ["Rusty Scimitar", "Bone Dagger"]

    def test_loot_skips_enemies_without_stat_block(self) -> None:
        pc = _pc()
        commoner = _kill(_enemy("Paysan", with_stat_block=False))
        state = _state([pc, commoner])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.VICTORY,
        )

        assert summary.loot_items == []

    def test_loot_skips_living_enemies(self) -> None:
        pc = _pc()
        alive = _enemy("Goblin")  # not killed
        state = _state([pc, alive])

        summary = finalize_combat(
            _session_with(state), CombatEndReason.TRUCE,
        )

        assert summary.loot_items == []


# ---------------------------------------------------------------------------
# finalize_combat — cleanup of transient conditions
# ---------------------------------------------------------------------------


class TestFinalizeCombatCleanup:
    def test_removes_surprised_condition(self) -> None:
        pc = _pc()
        pc.conditions.append(
            ActiveCondition(condition_type=ConditionType.SURPRISED),
        )
        state = _state([pc, _kill(_enemy("Goblin"))])

        finalize_combat(_session_with(state), CombatEndReason.VICTORY)

        assert not any(
            c.condition_type == ConditionType.SURPRISED
            for c in pc.conditions
        )

    def test_removes_concentrating_condition(self) -> None:
        pc = _pc()
        pc.conditions.append(
            ActiveCondition(condition_type=ConditionType.CONCENTRATING),
        )
        state = _state([pc, _kill(_enemy("Goblin"))])

        finalize_combat(_session_with(state), CombatEndReason.VICTORY)

        assert not any(
            c.condition_type == ConditionType.CONCENTRATING
            for c in pc.conditions
        )

    def test_removes_dodging_condition(self) -> None:
        # DODGING (Defend action, audit C2) is combat-only — it must not
        # leak into exploration once the encounter ends.
        pc = _pc()
        pc.conditions.append(
            ActiveCondition(condition_type=ConditionType.DODGING),
        )
        state = _state([pc, _kill(_enemy("Goblin"))])

        finalize_combat(_session_with(state), CombatEndReason.VICTORY)

        assert ConditionType.DODGING in _TRANSIENT_CONDITIONS
        assert not any(
            c.condition_type == ConditionType.DODGING
            for c in pc.conditions
        )

    def test_preserves_poisoned_condition(self) -> None:
        pc = _pc()
        pc.conditions.append(
            ActiveCondition(condition_type=ConditionType.POISONED),
        )
        state = _state([pc, _kill(_enemy("Goblin"))])

        finalize_combat(_session_with(state), CombatEndReason.VICTORY)

        assert any(
            c.condition_type == ConditionType.POISONED
            for c in pc.conditions
        )

    def test_preserves_prone_and_frightened(self) -> None:
        pc = _pc()
        pc.conditions.append(
            ActiveCondition(condition_type=ConditionType.PRONE),
        )
        pc.conditions.append(
            ActiveCondition(condition_type=ConditionType.FRIGHTENED),
        )
        state = _state([pc, _kill(_enemy("Goblin"))])

        finalize_combat(_session_with(state), CombatEndReason.VICTORY)

        cond_types = {c.condition_type for c in pc.conditions}
        assert ConditionType.PRONE in cond_types
        assert ConditionType.FRIGHTENED in cond_types

    def test_skips_dead_combatants_for_condition_cleanup(self) -> None:
        dead_pc = _kill(_pc("Aragorn"))
        dead_pc.conditions.append(
            ActiveCondition(condition_type=ConditionType.SURPRISED),
        )
        state = _state([dead_pc, _kill(_enemy("Goblin"))])

        finalize_combat(_session_with(state), CombatEndReason.DEFEAT)

        # Dead PC's conditions are not scrubbed (they don't matter anyway
        # and we don't want to spam remove_condition warnings).
        assert any(
            c.condition_type == ConditionType.SURPRISED
            for c in dead_pc.conditions
        )

    def test_transient_set_contents(self) -> None:
        # Sanity check on the module-level constant used above.
        assert ConditionType.SURPRISED in _TRANSIENT_CONDITIONS
        assert ConditionType.CONCENTRATING in _TRANSIENT_CONDITIONS
        assert ConditionType.POISONED not in _TRANSIENT_CONDITIONS


# ---------------------------------------------------------------------------
# finalize_combat — idempotence
# ---------------------------------------------------------------------------


class TestFinalizeCombatIdempotence:
    def test_xp_not_doubled_on_second_call(self) -> None:
        pc = _pc("Aragorn", xp=0)
        boss = _kill(_enemy("Tyrant", tier=NPCTier.BOSS))
        state = _state([pc, boss])

        summary1 = finalize_combat(
            _session_with(state), CombatEndReason.VICTORY,
        )
        summary2 = finalize_combat(
            _session_with(state), CombatEndReason.VICTORY,
        )

        assert pc.character.xp == 500  # applied once, not 1000
        assert summary1.xp_earned == summary2.xp_earned == 500

    def test_conditions_not_double_removed(self) -> None:
        pc = _pc()
        pc.conditions.append(
            ActiveCondition(condition_type=ConditionType.SURPRISED),
        )
        state = _state([pc, _kill(_enemy("Goblin"))])

        finalize_combat(_session_with(state), CombatEndReason.VICTORY)
        # Re-add to detect whether a second finalize would remove it.
        pc.conditions.append(
            ActiveCondition(condition_type=ConditionType.SURPRISED),
        )
        finalize_combat(_session_with(state), CombatEndReason.VICTORY)

        # Second call is a no-op → re-added condition should survive.
        assert any(
            c.condition_type == ConditionType.SURPRISED
            for c in pc.conditions
        )

    def test_flag_set_after_first_call(self) -> None:
        state = _state([_pc(), _kill(_enemy("Goblin"))])

        assert state._finalized is False
        finalize_combat(_session_with(state), CombatEndReason.VICTORY)
        assert state._finalized is True

    def test_second_call_returns_equivalent_summary(self) -> None:
        state = _state([_pc("Aragorn"), _kill(_enemy("Goblin"))])

        s1 = finalize_combat(_session_with(state), CombatEndReason.VICTORY)
        s2 = finalize_combat(_session_with(state), CombatEndReason.VICTORY)

        assert s1.killed_enemies == s2.killed_enemies
        assert s1.xp_earned == s2.xp_earned
        assert s1.rounds_taken == s2.rounds_taken


# ---------------------------------------------------------------------------
# finalize_combat — error paths
# ---------------------------------------------------------------------------


class TestFinalizeCombatErrors:
    def test_raises_when_no_combat_state(self) -> None:
        sess = MagicMock()
        sess.combat_state = None

        with pytest.raises(ValueError, match="no active combat_state"):
            finalize_combat(sess, CombatEndReason.VICTORY)


# ---------------------------------------------------------------------------
# build_combat_end_embed
# ---------------------------------------------------------------------------


def _summary(
    reason: CombatEndReason = CombatEndReason.VICTORY,
    **overrides,
) -> CombatEndSummary:
    base = dict(
        reason=reason,
        rounds_taken=3,
        survivors_pc=["Aragorn"],
        survivors_enemy=[],
        killed_pcs=[],
        killed_enemies=["Goblin"],
        fled_pcs=[],
        loot_items=["Rusty Scimitar"],
        xp_earned=50,
        narrative="",
    )
    base.update(overrides)
    return CombatEndSummary(**base)


class TestCombatEndEmbed:
    def test_victory_color_green(self) -> None:
        embed = build_combat_end_embed(_summary(CombatEndReason.VICTORY))
        assert embed.color is not None
        assert embed.color.value == 0x2ECC71
        assert "Victoire" in embed.title

    def test_defeat_color_red(self) -> None:
        embed = build_combat_end_embed(
            _summary(CombatEndReason.DEFEAT, killed_pcs=["Aragorn"], survivors_pc=[]),
        )
        assert embed.color is not None
        assert embed.color.value == 0xE74C3C
        assert "Défaite" in embed.title

    def test_fled_color_gray(self) -> None:
        embed = build_combat_end_embed(
            _summary(
                CombatEndReason.FLED,
                fled_pcs=["Aragorn"],
                survivors_pc=[],
                killed_enemies=[],
                loot_items=[],
                xp_earned=0,
            ),
        )
        assert embed.color is not None
        assert embed.color.value == 0x95A5A6
        assert "Fuite" in embed.title

    def test_truce_color_purple(self) -> None:
        embed = build_combat_end_embed(
            _summary(
                CombatEndReason.TRUCE,
                survivors_enemy=["Goblin"],
                killed_enemies=[],
                loot_items=[],
                xp_earned=0,
            ),
        )
        assert embed.color is not None
        assert embed.color.value == 0x9B59B6
        assert "Trêve" in embed.title

    def test_killed_enemies_field_when_present(self) -> None:
        embed = build_combat_end_embed(_summary(killed_enemies=["Goblin", "Orc"]))
        names = {f.name for f in embed.fields}
        assert "Ennemis vaincus" in names
        val = next(f.value for f in embed.fields if f.name == "Ennemis vaincus")
        assert "Goblin" in val and "Orc" in val

    def test_killed_enemies_field_absent_when_empty(self) -> None:
        embed = build_combat_end_embed(
            _summary(
                CombatEndReason.FLED,
                killed_enemies=[],
                fled_pcs=["Aragorn"],
                survivors_pc=[],
                loot_items=[],
                xp_earned=0,
            ),
        )
        names = {f.name for f in embed.fields}
        assert "Ennemis vaincus" not in names

    def test_loot_field_only_when_present(self) -> None:
        embed_with = build_combat_end_embed(_summary(loot_items=["Sword"]))
        assert any(f.name == "Butin" for f in embed_with.fields)

        embed_without = build_combat_end_embed(_summary(loot_items=[]))
        assert not any(f.name == "Butin" for f in embed_without.fields)

    def test_xp_field_only_when_earned(self) -> None:
        embed_with = build_combat_end_embed(_summary(xp_earned=100))
        assert any(f.name == "Expérience gagnée" for f in embed_with.fields)

        embed_without = build_combat_end_embed(_summary(xp_earned=0))
        assert not any(f.name == "Expérience gagnée" for f in embed_without.fields)

    def test_duration_field_always_present(self) -> None:
        embed = build_combat_end_embed(_summary(rounds_taken=5))
        duration = next(f for f in embed.fields if f.name == "Durée")
        assert "5 rounds" in duration.value

    def test_duration_singular_when_one_round(self) -> None:
        embed = build_combat_end_embed(_summary(rounds_taken=1))
        duration = next(f for f in embed.fields if f.name == "Durée")
        assert "1 round" in duration.value
        assert "1 rounds" not in duration.value

    def test_default_narrative_used_when_missing(self) -> None:
        embed = build_combat_end_embed(_summary(narrative=""))
        assert embed.description
        assert "silence" in embed.description.lower() or \
            "champ" in embed.description.lower()

    def test_custom_narrative_preserved(self) -> None:
        embed = build_combat_end_embed(
            _summary(narrative="Une bataille épique sans pareil."),
        )
        assert embed.description == "Une bataille épique sans pareil."

    def test_level_up_field_when_present(self) -> None:
        embed = build_combat_end_embed(_summary(level_ups=["Aragorn"]))
        assert any(f.name == "Niveau disponible" for f in embed.fields)
        val = next(
            f.value for f in embed.fields if f.name == "Niveau disponible"
        )
        assert "Aragorn" in val
        assert "/level_up" in val

    def test_level_up_field_absent_when_empty(self) -> None:
        embed = build_combat_end_embed(_summary(level_ups=[]))
        assert not any(
            f.name == "Niveau disponible" for f in embed.fields
        )
