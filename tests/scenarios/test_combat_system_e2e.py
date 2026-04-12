"""Task 82 — End-to-end combat scenario (Mageta vs Vellus le Mentisseur).

Gate-of-completion test for the Phase 8 chantier. Exercises the full
combat chain from bootstrap to finalisation:

- Bootstrap a combat against a realistic boss stat block.
- Apply damage via the engine until phase 2 triggers.
- Finalise with VICTORY, verify XP + loot in the ``CombatEndSummary``.
- Validate TRUCE (success, failure, phase-2 auto-refusal).
- Non-regression checks: trivial commoner kill, MOVE→FLEE auto-conversion
  (via the existing action pipeline path), TALK outside combat.

The test uses :class:`~tests.scenarios.scenario_runner.ScenarioRunner`
for orchestration — it doesn't instantiate the full live Discord stack.
The live stack test is documented in ``docs/internal/combat_system_e2e_results.md``
and run manually via the ``discord-test`` MCP.
"""

from __future__ import annotations

import pytest

from bot.combat_end import CombatEndSummary, finalize_combat
from bot.combat_truce import attempt_truce
from engine.combat import (
    CombatEndReason,
    CombatSide,
    CombatState,
    apply_damage,
    check_combat_end,
)
from engine.combat_phases import check_phase_transition
from engine.dice import RollOutcome
from engine.npc_stat_block import NPCStatBlock, NPCTier
from tests.scenarios.conftest import (
    give_starter_weapon,
    make_boss_enemy,
    make_weak_enemy,
)
from tests.scenarios.scenario_runner import ScenarioRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _mageta_vs_vellus(
    scenario: ScenarioRunner,
    vellus_stat_block: NPCStatBlock,
) -> None:
    """Bootstrap Mageta (PC) and Vellus (boss) in a live combat."""
    await scenario.start_campaign(theme="désert", players=1)
    await scenario.add_player(
        "Mageta", race="Human", class_="Ranger", player_idx=0,
    )
    give_starter_weapon(scenario, player_idx=0)

    vellus = make_boss_enemy(
        "Vellus le Mentisseur",
        vellus_stat_block,
        hp=80,
        ac=16,
    )
    await scenario.start_combat(enemies=[vellus])


def _find_vellus(state: CombatState):
    return next(
        c for c in state.combatants
        if c.name == "Vellus le Mentisseur"
    )


def _find_mageta(state: CombatState):
    return next(
        c for c in state.combatants
        if c.side == CombatSide.PLAYER
    )


# ---------------------------------------------------------------------------
# Combat bootstrap & state
# ---------------------------------------------------------------------------


class TestCombatBootstrap:
    @pytest.mark.asyncio
    async def test_combat_starts_with_party_wide_state(
        self,
        scenario: ScenarioRunner,
        vellus_stat_block: NPCStatBlock,
    ) -> None:
        await _mageta_vs_vellus(scenario, vellus_stat_block)
        scenario.assert_in_combat()

        state = scenario.session.combat_state
        assert state is not None
        # PC + boss
        assert len(state.combatants) == 2
        assert state.round_number == 1
        assert state.is_active is True

    @pytest.mark.asyncio
    async def test_vellus_has_boss_stat_block(
        self,
        scenario: ScenarioRunner,
        vellus_stat_block: NPCStatBlock,
    ) -> None:
        await _mageta_vs_vellus(scenario, vellus_stat_block)
        vellus = _find_vellus(scenario.session.combat_state)

        assert vellus.stat_block is not None
        assert vellus.stat_block.tier == NPCTier.BOSS
        assert vellus.stat_block.aggression_threshold == 25
        assert vellus.legendary_points_remaining == 3
        assert len(vellus.stat_block.phases) == 1


# ---------------------------------------------------------------------------
# Phase transitions
# ---------------------------------------------------------------------------


class TestPhaseTransitions:
    @pytest.mark.asyncio
    async def test_phase_2_triggers_at_50_percent_hp(
        self,
        scenario: ScenarioRunner,
        vellus_stat_block: NPCStatBlock,
    ) -> None:
        await _mageta_vs_vellus(scenario, vellus_stat_block)
        state = scenario.session.combat_state
        vellus = _find_vellus(state)

        # Not triggered at full HP.
        assert vellus.stat_block.phases[0].triggered is False

        # Damage Vellus to exactly 50% HP. ``apply_damage`` already
        # calls ``check_phase_transition`` internally, so the phase
        # flips and the pending_phase_narrations queue is populated by
        # the single damage call.
        half_hp = vellus.character.max_hp // 2
        # Note: ``apply_damage`` doesn't touch ``state.pending_phase_narrations``
        # directly — it mutates the combatant in place. We queue the
        # narration event via ``check_phase_transition`` on the state.
        apply_damage(vellus, vellus.character.hp - half_hp)

        # ``apply_damage`` already called ``check_phase_transition``
        # internally on the damage path — the phase is set; calling it
        # again here is a no-op (already-fired phases are skipped).
        check_phase_transition(vellus)

        assert vellus.stat_block.phases[0].triggered is True

    @pytest.mark.asyncio
    async def test_phase_2_above_threshold_not_triggered(
        self,
        scenario: ScenarioRunner,
        vellus_stat_block: NPCStatBlock,
    ) -> None:
        await _mageta_vs_vellus(scenario, vellus_stat_block)
        vellus = _find_vellus(scenario.session.combat_state)

        # Deal a small amount of damage — still above 50%.
        apply_damage(vellus, 10)

        check_phase_transition(vellus)
        assert vellus.stat_block.phases[0].triggered is False


# ---------------------------------------------------------------------------
# TRUCE — task 81 end-to-end validation
# ---------------------------------------------------------------------------


class TestTruceInE2E:
    @pytest.mark.asyncio
    async def test_truce_succeeds_in_phase_1(
        self,
        scenario: ScenarioRunner,
        vellus_stat_block: NPCStatBlock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from engine.dice import D20CheckResult

        await _mageta_vs_vellus(scenario, vellus_stat_block)
        state = scenario.session.combat_state
        mageta = _find_mageta(state)
        vellus = _find_vellus(state)

        # Force a CHA check success deterministically.
        fake_check = D20CheckResult(
            expression="1d20+5",
            rolls=[20],
            modifier=5,
            total=25,
            dc=25,
            outcome=RollOutcome.SUCCESS,
            margin=0,
        )
        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: fake_check,
        )

        succeeded, check, summary = attempt_truce(mageta, vellus, state)

        assert succeeded is True
        assert check is not None
        assert vellus.fled is True
        # PC action consumed.
        assert mageta.action_budget.action_used is True

        # Finalize the encounter as the pipeline would after a
        # successful truce.
        end_summary = finalize_combat(
            scenario.session, CombatEndReason.TRUCE,
        )

        assert isinstance(end_summary, CombatEndSummary)
        assert end_summary.reason == CombatEndReason.TRUCE
        assert state.is_active is False
        assert state.end_reason == CombatEndReason.TRUCE

    @pytest.mark.asyncio
    async def test_truce_refused_after_phase_2_triggered(
        self,
        scenario: ScenarioRunner,
        vellus_stat_block: NPCStatBlock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from engine.dice import D20CheckResult

        await _mageta_vs_vellus(scenario, vellus_stat_block)
        state = scenario.session.combat_state
        mageta = _find_mageta(state)
        vellus = _find_vellus(state)

        # Force Vellus into phase 2.
        half_hp = vellus.character.max_hp // 2
        apply_damage(vellus, vellus.character.hp - half_hp)
        check_phase_transition(vellus)
        assert vellus.stat_block.phases[0].triggered is True

        called: list[bool] = []
        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: called.append(True) or D20CheckResult(
                expression="1d20", rolls=[20], modifier=0,
                total=20, dc=25, outcome=RollOutcome.SUCCESS, margin=-5,
            ),
        )

        succeeded, check, summary = attempt_truce(mageta, vellus, state)

        assert succeeded is False
        assert check is None  # auto-refusal, no roll
        assert called == []
        assert vellus.fled is False
        # Action NOT consumed (structural refusal).
        assert mageta.action_budget.action_used is False

    @pytest.mark.asyncio
    async def test_truce_failure_below_dc_keeps_combat_active(
        self,
        scenario: ScenarioRunner,
        vellus_stat_block: NPCStatBlock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from engine.dice import D20CheckResult

        await _mageta_vs_vellus(scenario, vellus_stat_block)
        state = scenario.session.combat_state
        mageta = _find_mageta(state)
        vellus = _find_vellus(state)

        fake_check = D20CheckResult(
            expression="1d20+5",
            rolls=[5],
            modifier=5,
            total=10,
            dc=25,
            outcome=RollOutcome.FAILURE,
            margin=-15,
        )
        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: fake_check,
        )

        succeeded, _, _ = attempt_truce(mageta, vellus, state)

        assert succeeded is False
        assert vellus.fled is False
        assert state.is_active is True
        # Action consumed even on failure — the attempt counts.
        assert mageta.action_budget.action_used is True


# ---------------------------------------------------------------------------
# VICTORY path — finalize_combat end-to-end
# ---------------------------------------------------------------------------


class TestVictoryPath:
    @pytest.mark.asyncio
    async def test_killing_vellus_yields_victory_summary(
        self,
        scenario: ScenarioRunner,
        vellus_stat_block: NPCStatBlock,
    ) -> None:
        await _mageta_vs_vellus(scenario, vellus_stat_block)
        state = scenario.session.combat_state
        vellus = _find_vellus(state)
        mageta = _find_mageta(state)

        xp_before = mageta.character.xp

        # Drop Vellus directly (bypass dice for determinism).
        apply_damage(vellus, vellus.character.hp)
        assert vellus.is_alive is False

        # The normal end-of-turn flow would detect this; we call
        # finalize_combat explicitly.
        reason = check_combat_end(state)
        assert reason == CombatEndReason.VICTORY

        summary = finalize_combat(scenario.session, reason)

        assert summary.reason == CombatEndReason.VICTORY
        assert "Vellus le Mentisseur" in summary.killed_enemies
        assert summary.xp_earned == 500  # single boss kill
        assert mageta.character.xp == xp_before + 500
        # Boss primary attack becomes a loot trophy.
        assert "Lame de sable" in summary.loot_items
        # Combat state preserved but inactive (task 80 invariant).
        assert state.is_active is False
        assert scenario.session.combat_state is state


# ---------------------------------------------------------------------------
# DEFEAT path
# ---------------------------------------------------------------------------


class TestDefeatPath:
    @pytest.mark.asyncio
    async def test_killing_mageta_yields_defeat_summary(
        self,
        scenario: ScenarioRunner,
        vellus_stat_block: NPCStatBlock,
    ) -> None:
        await _mageta_vs_vellus(scenario, vellus_stat_block)
        state = scenario.session.combat_state
        mageta = _find_mageta(state)

        # PCs at HP=0 enter death saves (unconscious, is_alive stays
        # True). To simulate an outright fatal blow we drop HP to 0 and
        # manually flip ``is_alive`` — mirrors what 3 failed death
        # saves would do.
        apply_damage(mageta, mageta.character.hp)
        mageta.is_alive = False

        reason = check_combat_end(state)
        assert reason == CombatEndReason.DEFEAT

        summary = finalize_combat(scenario.session, reason)

        assert summary.reason == CombatEndReason.DEFEAT
        assert mageta.name in summary.killed_pcs
        # No XP when every PC is down.
        assert summary.xp_earned == 0


# ---------------------------------------------------------------------------
# Non-regression: trivial commoner kill
# ---------------------------------------------------------------------------


class TestNonRegression:
    @pytest.mark.asyncio
    async def test_trivial_weak_enemy_kill_still_works(
        self,
        scenario: ScenarioRunner,
    ) -> None:
        """Phase 0 bugfix still holds: one-shot on a weak foe leaves
        the combat loop intact."""
        await scenario.start_campaign(theme="test", players=1)
        await scenario.add_player(
            "Guerrier", race="Human", class_="Fighter", player_idx=0,
        )
        give_starter_weapon(scenario)

        enemies = [make_weak_enemy("Rat")]
        await scenario.start_combat(enemies=enemies)

        # Hammer until the rat drops (runner silently no-ops dead targets).
        for _ in range(10):
            if not scenario.session.combat_state.is_active:
                break
            await scenario.attack(target="Rat", player_idx=0)

        scenario.assert_not_in_combat()
        assert scenario.session.combat_state.end_reason == CombatEndReason.VICTORY

    @pytest.mark.asyncio
    async def test_talk_validation_outside_combat_still_accepted(
        self,
        scenario: ScenarioRunner,
    ) -> None:
        """Task 81 doesn't break the out-of-combat TALK dispatch."""
        from engine.validators import (
            Action,
            ActionType,
            validate_exploration_action,
        )

        result = validate_exploration_action(
            Action(
                actor_name="Aragorn",
                action_type=ActionType.TALK,
                target_name="Vieillard",
            ),
            combat_state=None,
        )
        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_move_in_combat_still_blocked(
        self,
        scenario: ScenarioRunner,
        vellus_stat_block: NPCStatBlock,
    ) -> None:
        """MOVE in combat remains blocked — only TALK got the TRUCE
        exception in task 81."""
        from engine.validators import (
            Action,
            ActionType,
            validate_exploration_action,
        )

        await _mageta_vs_vellus(scenario, vellus_stat_block)

        result = validate_exploration_action(
            Action(
                actor_name="Mageta",
                action_type=ActionType.MOVE,
                target_name="Corridor des Illusions",
            ),
            combat_state=scenario.session.combat_state,
        )
        assert result.is_valid is False


# ---------------------------------------------------------------------------
# Idempotence — finalize_combat called twice (pipeline + TurnManager)
# ---------------------------------------------------------------------------


class TestFinalizeIdempotenceE2E:
    @pytest.mark.asyncio
    async def test_double_finalize_does_not_double_xp(
        self,
        scenario: ScenarioRunner,
        vellus_stat_block: NPCStatBlock,
    ) -> None:
        """Task 80 invariant: the pipeline may call finalize_combat
        (via _resolve_flee / _resolve_talk_in_combat) *and* the
        TurnManager will call it again from _finalize. The second call
        must not double the XP."""
        await _mageta_vs_vellus(scenario, vellus_stat_block)
        state = scenario.session.combat_state
        vellus = _find_vellus(state)
        mageta = _find_mageta(state)

        xp_before = mageta.character.xp
        apply_damage(vellus, vellus.character.hp)

        summary1 = finalize_combat(scenario.session, CombatEndReason.VICTORY)
        summary2 = finalize_combat(scenario.session, CombatEndReason.VICTORY)

        assert summary1.xp_earned == summary2.xp_earned == 500
        # Single application to the character sheet.
        assert mageta.character.xp == xp_before + 500
