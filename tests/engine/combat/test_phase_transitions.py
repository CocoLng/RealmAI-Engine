"""Tests for HP phase transitions on boss NPCs.

Covers:
- ``engine.combat_phases.check_phase_transition`` — threshold detection,
  idempotence (each phase fires once), multi-phase traversal on big hits,
  signature unlocks, attack bonus application, save bonus accumulation,
  dead boss guard.
- ``engine.combat.apply_damage`` integration — phases fire on the damage
  hook, and when a ``CombatState`` is provided the events are appended
  to ``state.pending_phase_narrations``.
"""

from __future__ import annotations

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
    apply_damage,
)
from engine.combat_phases import check_phase_transition
from engine.inventory import DamageType, create_inventory
from engine.npc_stat_block import (
    BehaviorProfile,
    NPCAttack,
    NPCStatBlock,
    NPCTier,
    PhaseTransition,
    SignatureAbility,
    SignatureAbilityEffect,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_boss_with_phases(
    phases: list[PhaseTransition],
    hp: int = 100,
    max_hp: int = 100,
    signatures: list[SignatureAbility] | None = None,
    attacks: list[NPCAttack] | None = None,
) -> Combatant:
    scores = AbilityScores(STR=16, DEX=14, CON=16, INT=14, WIS=14, CHA=14)
    char = create_character("Boss", Race.HUMAN, CharacterClass.FIGHTER, scores)
    char.hp = hp
    char.max_hp = max_hp
    char.ac = 18
    stat_block = NPCStatBlock(
        tier=NPCTier.BOSS,
        archetype="phased_boss",
        multiattack_count=3,
        attacks=attacks or [
            NPCAttack(
                name="Greataxe",
                damage_dice="1d12+4",
                damage_type=DamageType.SLASHING,
                to_hit_bonus=5,
            ),
        ],
        signature_abilities=signatures or [],
        phases=phases,
        behavior_profile=BehaviorProfile.AGGRESSIVE,
    )
    return Combatant(
        name="Boss",
        side=CombatSide.ENEMY,
        character=char,
        inventory=create_inventory(),
        stat_block=stat_block,
    )


def _phase(
    threshold: int,
    narrative: str = "",
    unlock: list[str] | None = None,
    attack_bonus: int = 0,
    save_bonus: int = 0,
) -> PhaseTransition:
    return PhaseTransition(
        trigger_hp_percent=threshold,
        narrative_cue=narrative,
        unlock_signatures=unlock or [],
        attack_bonus=attack_bonus,
        save_bonus=save_bonus,
    )


# ---------------------------------------------------------------------------
# check_phase_transition — direct
# ---------------------------------------------------------------------------


class TestCheckPhaseTransition:
    def test_triggers_at_exact_hp_percent(self) -> None:
        boss = _make_boss_with_phases([_phase(50, "halved!")])
        boss.character.hp = 50  # exactly at threshold

        triggered = check_phase_transition(boss)

        assert len(triggered) == 1
        assert triggered[0].trigger_hp_percent == 50
        assert boss.stat_block.phases[0].triggered is True  # type: ignore[union-attr]

    def test_triggers_below_threshold(self) -> None:
        boss = _make_boss_with_phases([_phase(50, "halved!")])
        boss.character.hp = 30

        triggered = check_phase_transition(boss)

        assert len(triggered) == 1

    def test_does_not_trigger_above_threshold(self) -> None:
        boss = _make_boss_with_phases([_phase(50, "halved!")])
        boss.character.hp = 75

        triggered = check_phase_transition(boss)

        assert triggered == []
        assert boss.stat_block.phases[0].triggered is False  # type: ignore[union-attr]

    def test_phase_does_not_retrigger_after_heal_and_redamage(self) -> None:
        boss = _make_boss_with_phases([_phase(50, "halved!")])
        boss.character.hp = 40

        # First trigger
        check_phase_transition(boss)
        assert boss.stat_block.phases[0].triggered is True  # type: ignore[union-attr]

        # Heal above threshold then drop again
        boss.character.hp = 80
        boss.character.hp = 30
        triggered = check_phase_transition(boss)

        # Phase stays triggered — no retrigger
        assert triggered == []

    def test_multiple_phases_can_trigger_in_single_hit(self) -> None:
        """A massive hit that crosses two thresholds at once fires both."""
        boss = _make_boss_with_phases(
            [_phase(66, "first"), _phase(33, "second")],
        )
        boss.character.hp = 10  # way below both thresholds

        triggered = check_phase_transition(boss)

        assert len(triggered) == 2

    def test_phase_unlocks_signature(self) -> None:
        locked_sig = SignatureAbility(
            name="Rage Mode",
            description="Unlocked only in phase 2.",
            usage="per_combat",
            uses_remaining=0,  # locked
            effects=[
                SignatureAbilityEffect(
                    kind="damage",
                    dice="3d8",
                    damage_type=DamageType.FORCE,
                    target_scope="single",
                ),
            ],
        )
        boss = _make_boss_with_phases(
            [_phase(50, unlock=["Rage Mode"])],
            signatures=[locked_sig],
        )
        boss.character.hp = 40

        check_phase_transition(boss)

        assert locked_sig.uses_remaining == 1  # unlocked

    def test_phase_applies_attack_bonus_to_all_attacks(self) -> None:
        boss = _make_boss_with_phases(
            [_phase(50, attack_bonus=2)],
            attacks=[
                NPCAttack(
                    name="Axe",
                    damage_dice="1d12+4",
                    damage_type=DamageType.SLASHING,
                    to_hit_bonus=5,
                ),
                NPCAttack(
                    name="Bite",
                    damage_dice="1d8+3",
                    damage_type=DamageType.PIERCING,
                    to_hit_bonus=4,
                ),
            ],
        )
        boss.character.hp = 40

        check_phase_transition(boss)

        assert boss.stat_block.attacks[0].to_hit_bonus == 7  # type: ignore[union-attr]
        assert boss.stat_block.attacks[1].to_hit_bonus == 6  # type: ignore[union-attr]

    def test_phase_applies_save_bonus_to_combatant(self) -> None:
        boss = _make_boss_with_phases([_phase(50, save_bonus=3)])
        boss.character.hp = 40

        check_phase_transition(boss)

        assert boss.phase_save_bonus == 3

    def test_phase_does_not_trigger_on_dead_boss(self) -> None:
        boss = _make_boss_with_phases([_phase(50)])
        boss.character.hp = 0
        boss.is_alive = False

        triggered = check_phase_transition(boss)

        assert triggered == []

    def test_no_phases_returns_empty(self) -> None:
        boss = _make_boss_with_phases([])
        boss.character.hp = 10

        assert check_phase_transition(boss) == []

    def test_no_stat_block_returns_empty(self) -> None:
        """Combatants without a stat block cannot have phases."""
        scores = AbilityScores(STR=12, DEX=12, CON=12, INT=12, WIS=12, CHA=12)
        char = create_character(
            "Plain", Race.HUMAN, CharacterClass.FIGHTER, scores,
        )
        combatant = Combatant(
            name="Plain",
            side=CombatSide.PLAYER,
            character=char,
            inventory=create_inventory(),
        )

        assert check_phase_transition(combatant) == []


# ---------------------------------------------------------------------------
# apply_damage integration
# ---------------------------------------------------------------------------


class TestApplyDamagePhaseHook:
    def test_apply_damage_fires_phase_and_appends_event(self) -> None:
        """apply_damage with state appends a PhaseTransitionEvent."""
        boss = _make_boss_with_phases([_phase(50, "Eyes flare red.")])
        state = CombatState(combatants=[boss], round_number=1, current_turn_index=0)

        apply_damage(boss, 60, state=state)  # 100 → 40 HP = 40%

        assert len(state.pending_phase_narrations) == 1
        event = state.pending_phase_narrations[0]
        assert event.combatant_name == "Boss"
        assert event.narrative_cue == "Eyes flare red."

    def test_apply_damage_without_state_still_mutates_stat_block(self) -> None:
        """Legacy callers that skip state still get the mechanical effect."""
        boss = _make_boss_with_phases(
            [_phase(50, attack_bonus=3)],
        )
        original = boss.stat_block.attacks[0].to_hit_bonus  # type: ignore[union-attr]

        apply_damage(boss, 60)  # no state

        assert boss.stat_block.phases[0].triggered is True  # type: ignore[union-attr]
        assert boss.stat_block.attacks[0].to_hit_bonus == original + 3  # type: ignore[union-attr]

    def test_big_hit_fires_both_phases_appends_two_events(self) -> None:
        boss = _make_boss_with_phases(
            [_phase(66, "first"), _phase(33, "second")],
        )
        state = CombatState(combatants=[boss], round_number=1, current_turn_index=0)

        apply_damage(boss, 95, state=state)  # 100 → 5 HP

        assert len(state.pending_phase_narrations) == 2
        cues = [e.narrative_cue for e in state.pending_phase_narrations]
        assert "first" in cues
        assert "second" in cues

    def test_healed_boss_does_not_retrigger_phase(self) -> None:
        boss = _make_boss_with_phases([_phase(50, "first time")])
        state = CombatState(combatants=[boss], round_number=1, current_turn_index=0)

        apply_damage(boss, 60, state=state)  # triggers
        assert len(state.pending_phase_narrations) == 1

        boss.character.hp = 90  # heal
        apply_damage(boss, 70, state=state)  # drop again

        # Still just one event — phase never retriggers
        assert len(state.pending_phase_narrations) == 1
