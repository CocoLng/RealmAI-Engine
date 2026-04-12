"""Tests for bot/combat_truce.py and TRUCE validation (task 81).

Covers :func:`bot.combat_truce.attempt_truce` (success/failure/auto-refusal
paths, action consumption, fled marking) and the
:func:`engine.validators.validate_truce_attempt` validator.
"""

from __future__ import annotations

import pytest

from bot.combat_truce import (
    _PROFICIENCY_BONUS,
    _SUCCESS_OUTCOMES,
    attempt_truce,
)
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import CombatSide, CombatState, Combatant
from engine.dice import D20CheckResult, RollOutcome
from engine.inventory import DamageType, create_inventory
from engine.npc_stat_block import (
    BehaviorProfile,
    NPCAttack,
    NPCStatBlock,
    NPCTier,
    PhaseTransition,
)
from engine.validators import (
    Action,
    ActionType,
    validate_truce_attempt,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _pc(
    name: str = "Aragorn",
    *,
    cha: int = 16,
) -> Combatant:
    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(
            STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=cha,
        ),
    )
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=create_inventory(),
        initiative=15,
    )


def _stat_block(
    tier: NPCTier = NPCTier.MINION,
    *,
    aggression_threshold: int = 15,
    mindless: bool = False,
    phases: list[PhaseTransition] | None = None,
) -> NPCStatBlock:
    return NPCStatBlock(
        tier=tier,
        archetype="test",
        attacks=[
            NPCAttack(
                name="Griffe",
                to_hit_bonus=3,
                damage_dice="1d6",
                damage_type=DamageType.SLASHING,
                range_type="melee",
            ),
        ],
        aggression_threshold=aggression_threshold,
        mindless=mindless,
        phases=phases or [],
        behavior_profile=BehaviorProfile.AGGRESSIVE,
    )


def _enemy(
    name: str = "Gobelin",
    *,
    tier: NPCTier = NPCTier.MINION,
    aggression_threshold: int = 15,
    mindless: bool = False,
    phases: list[PhaseTransition] | None = None,
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
    kwargs: dict = {
        "name": name,
        "side": CombatSide.ENEMY,
        "character": char,
        "inventory": create_inventory(),
        "initiative": 10,
    }
    if with_stat_block:
        kwargs["stat_block"] = _stat_block(
            tier,
            aggression_threshold=aggression_threshold,
            mindless=mindless,
            phases=phases,
        )
    return Combatant(**kwargs)


def _state(combatants: list[Combatant]) -> CombatState:
    return CombatState(combatants=combatants, round_number=1)


def _fake_check(
    outcome: RollOutcome,
    total: int = 18,
    dc: int = 15,
) -> D20CheckResult:
    return D20CheckResult(
        expression="1d20+5",
        rolls=[15],
        modifier=3,
        total=total,
        dc=dc,
        outcome=outcome,
        margin=total - dc,
    )


# ---------------------------------------------------------------------------
# attempt_truce — success paths
# ---------------------------------------------------------------------------


class TestAttemptTruceSuccess:
    def test_success_vs_standard_enemy_marks_fled(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        actor = _pc(cha=18)
        goblin = _enemy("Gob1")
        orc = _enemy("Orc1")
        state = _state([actor, goblin, orc])

        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: _fake_check(RollOutcome.SUCCESS, total=20, dc=dc),
        )

        succeeded, check, summary = attempt_truce(actor, goblin, state)

        assert succeeded is True
        assert check is not None
        assert check.outcome == RollOutcome.SUCCESS
        assert goblin.fled is True
        assert orc.fled is True
        assert "CHA 20" in summary

    def test_critical_success_accepted(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        actor = _pc()
        enemy = _enemy()
        state = _state([actor, enemy])

        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: _fake_check(
                RollOutcome.CRITICAL_SUCCESS, total=30, dc=dc,
            ),
        )

        succeeded, _, _ = attempt_truce(actor, enemy, state)
        assert succeeded is True
        assert enemy.fled is True

    def test_success_passes_dc_to_roll_check(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_dc: list[int] = []

        def capture(expr, dc):
            captured_dc.append(dc)
            return _fake_check(RollOutcome.SUCCESS, dc=dc)

        monkeypatch.setattr("bot.combat_truce.roll_check", capture)

        actor = _pc()
        enemy = _enemy(aggression_threshold=22)
        state = _state([actor, enemy])

        attempt_truce(actor, enemy, state)

        assert captured_dc == [22]

    def test_success_proficiency_applied_in_expression(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_expr: list[str] = []

        def capture(expr, dc):
            captured_expr.append(expr)
            return _fake_check(RollOutcome.SUCCESS)

        monkeypatch.setattr("bot.combat_truce.roll_check", capture)

        # CHA 18 → mod +4. With proficiency +2, expression must be 1d20+6.
        actor = _pc(cha=18)
        enemy = _enemy()
        state = _state([actor, enemy])

        attempt_truce(actor, enemy, state)

        assert captured_expr == [f"1d20+{4 + _PROFICIENCY_BONUS}"]


# ---------------------------------------------------------------------------
# attempt_truce — failure paths
# ---------------------------------------------------------------------------


class TestAttemptTruceFailure:
    def test_failure_below_dc(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        actor = _pc()
        enemy = _enemy(aggression_threshold=25)
        other_enemy = _enemy("Orc2")
        state = _state([actor, enemy, other_enemy])

        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: _fake_check(
                RollOutcome.FAILURE, total=10, dc=25,
            ),
        )

        succeeded, check, summary = attempt_truce(actor, enemy, state)

        assert succeeded is False
        assert check is not None
        assert enemy.fled is False
        assert other_enemy.fled is False

    def test_near_success_counts_as_failure(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Strict DC — NEAR_SUCCESS (missed by 1-2) is not enough."""
        actor = _pc()
        enemy = _enemy()
        state = _state([actor, enemy])

        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: _fake_check(
                RollOutcome.NEAR_SUCCESS, total=14, dc=15,
            ),
        )

        succeeded, _, _ = attempt_truce(actor, enemy, state)

        assert succeeded is False
        assert enemy.fled is False
        assert RollOutcome.NEAR_SUCCESS not in _SUCCESS_OUTCOMES

    def test_consumes_action_on_failure(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        actor = _pc()
        enemy = _enemy()
        state = _state([actor, enemy])

        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: _fake_check(RollOutcome.FAILURE),
        )

        assert actor.action_budget.action_used is False
        attempt_truce(actor, enemy, state)
        assert actor.action_budget.action_used is True

    def test_consumes_action_on_success(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        actor = _pc()
        enemy = _enemy()
        state = _state([actor, enemy])

        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: _fake_check(RollOutcome.SUCCESS),
        )

        attempt_truce(actor, enemy, state)
        assert actor.action_budget.action_used is True


# ---------------------------------------------------------------------------
# attempt_truce — auto-refusals
# ---------------------------------------------------------------------------


class TestAttemptTruceAutoRefusal:
    def test_mindless_target_refused_without_rolling(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called: list[bool] = []
        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: (called.append(True), _fake_check(
                RollOutcome.SUCCESS,
            ))[1],
        )

        actor = _pc()
        zombie = _enemy("Zombie", mindless=True)
        state = _state([actor, zombie])

        succeeded, check, summary = attempt_truce(actor, zombie, state)

        assert succeeded is False
        assert check is None
        assert called == []  # no roll performed
        assert actor.action_budget.action_used is False
        assert "bestial" in summary.lower()

    def test_boss_in_phase_2_refused(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called: list[bool] = []
        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: (called.append(True), _fake_check(
                RollOutcome.SUCCESS,
            ))[1],
        )

        actor = _pc()
        triggered_phase = PhaseTransition(
            trigger_hp_percent=50,
            narrative_cue="",
            triggered=True,
        )
        boss = _enemy(
            "Dragon",
            tier=NPCTier.BOSS,
            phases=[triggered_phase],
        )
        state = _state([actor, boss])

        succeeded, check, _ = attempt_truce(actor, boss, state)

        assert succeeded is False
        assert check is None
        assert called == []

    def test_boss_in_phase_1_still_negotiable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Untriggered phase → normal roll."""
        actor = _pc(cha=18)
        untriggered_phase = PhaseTransition(
            trigger_hp_percent=50,
            narrative_cue="",
            triggered=False,  # not yet past 50% HP
        )
        boss = _enemy(
            "Dragon",
            tier=NPCTier.BOSS,
            phases=[untriggered_phase],
        )
        state = _state([actor, boss])

        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: _fake_check(RollOutcome.SUCCESS),
        )

        succeeded, check, _ = attempt_truce(actor, boss, state)

        assert succeeded is True
        assert check is not None
        assert boss.fled is True

    def test_target_without_stat_block_refused(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        actor = _pc()
        commoner = _enemy("Paysan", with_stat_block=False)
        state = _state([actor, commoner])

        monkeypatch.setattr(
            "bot.combat_truce.roll_check",
            lambda expr, dc: _fake_check(RollOutcome.SUCCESS),
        )

        succeeded, check, _ = attempt_truce(actor, commoner, state)

        assert succeeded is False
        assert check is None
        assert actor.action_budget.action_used is False


# ---------------------------------------------------------------------------
# validate_truce_attempt
# ---------------------------------------------------------------------------


class TestValidateTruceAttempt:
    def test_accepts_valid_target(self) -> None:
        actor = _pc()
        enemy = _enemy()
        state = _state([actor, enemy])

        result = validate_truce_attempt(
            Action(
                actor_name="Aragorn",
                action_type=ActionType.TALK,
                target_name="Gobelin",
            ),
            state,
        )
        assert result.is_valid is True

    def test_rejects_missing_actor(self) -> None:
        enemy = _enemy()
        state = _state([enemy])

        result = validate_truce_attempt(
            Action(
                actor_name="Fantôme",
                action_type=ActionType.TALK,
                target_name="Gobelin",
            ),
            state,
        )
        assert result.is_valid is False
        assert "Fantôme" in (result.error_message or "")

    def test_rejects_missing_target_name(self) -> None:
        actor = _pc()
        enemy = _enemy()
        state = _state([actor, enemy])

        result = validate_truce_attempt(
            Action(
                actor_name="Aragorn",
                action_type=ActionType.TALK,
                target_name=None,
            ),
            state,
        )
        assert result.is_valid is False
        assert "cible" in (result.error_message or "").lower()

    def test_rejects_ally_target(self) -> None:
        actor = _pc("Aragorn")
        ally = _pc("Legolas")
        enemy = _enemy()
        state = _state([actor, ally, enemy])

        result = validate_truce_attempt(
            Action(
                actor_name="Aragorn",
                action_type=ActionType.TALK,
                target_name="Legolas",
            ),
            state,
        )
        assert result.is_valid is False
        assert "allié" in (result.error_message or "").lower()

    def test_rejects_target_without_stat_block(self) -> None:
        actor = _pc()
        commoner = _enemy("Paysan", with_stat_block=False)
        state = _state([actor, commoner])

        result = validate_truce_attempt(
            Action(
                actor_name="Aragorn",
                action_type=ActionType.TALK,
                target_name="Paysan",
            ),
            state,
        )
        assert result.is_valid is False

    def test_rejects_mindless_target(self) -> None:
        actor = _pc()
        zombie = _enemy("Zombie", mindless=True)
        state = _state([actor, zombie])

        result = validate_truce_attempt(
            Action(
                actor_name="Aragorn",
                action_type=ActionType.TALK,
                target_name="Zombie",
            ),
            state,
        )
        assert result.is_valid is False
        assert "bestial" in (result.error_message or "").lower()

    def test_rejects_unknown_target(self) -> None:
        actor = _pc()
        state = _state([actor])

        result = validate_truce_attempt(
            Action(
                actor_name="Aragorn",
                action_type=ActionType.TALK,
                target_name="Fantôme",
            ),
            state,
        )
        assert result.is_valid is False


# ---------------------------------------------------------------------------
# Exploration validator dispatch — task 81 integration
# ---------------------------------------------------------------------------


class TestExplorationValidatorDispatchesTruce:
    def test_talk_in_combat_routed_to_validate_truce_attempt(self) -> None:
        """``validate_exploration_action`` delegates TALK-in-combat to the
        TRUCE validator so the dispatcher stays a single entry point."""
        from engine.validators import validate_exploration_action

        actor = _pc()
        enemy = _enemy()
        state = _state([actor, enemy])

        result = validate_exploration_action(
            Action(
                actor_name="Aragorn",
                action_type=ActionType.TALK,
                target_name="Gobelin",
            ),
            combat_state=state,
        )
        assert result.is_valid is True

    def test_talk_in_combat_rejected_for_mindless_target(self) -> None:
        from engine.validators import validate_exploration_action

        actor = _pc()
        zombie = _enemy("Zombie", mindless=True)
        state = _state([actor, zombie])

        result = validate_exploration_action(
            Action(
                actor_name="Aragorn",
                action_type=ActionType.TALK,
                target_name="Zombie",
            ),
            combat_state=state,
        )
        assert result.is_valid is False

    def test_talk_out_of_combat_still_accepted(self) -> None:
        """Non-regression: TALK outside combat goes through the standard
        exploration path, not TRUCE."""
        from engine.validators import validate_exploration_action

        result = validate_exploration_action(
            Action(
                actor_name="Aragorn",
                action_type=ActionType.TALK,
                target_name="Vieillard",
            ),
            combat_state=None,
        )
        assert result.is_valid is True

    def test_move_in_combat_still_blocked(self) -> None:
        """Non-regression: MOVE in combat stays blocked, only TALK was
        opened up for the TRUCE path."""
        from engine.validators import validate_exploration_action

        actor = _pc()
        enemy = _enemy()
        state = _state([actor, enemy])

        result = validate_exploration_action(
            Action(
                actor_name="Aragorn",
                action_type=ActionType.MOVE,
                target_name="corridor",
            ),
            combat_state=state,
        )
        assert result.is_valid is False
