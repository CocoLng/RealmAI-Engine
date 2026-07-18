"""Social de-escalation in combat (TRUCE).

Implements the single-check CHA negotiation path that lets a PC end a
combat peacefully by convincing an enemy to stand down. The check is a
strict ``1d20 + CHA_mod + proficiency`` vs the target's
``NPCStatBlock.aggression_threshold``. Only :class:`~engine.dice.RollOutcome`
``SUCCESS`` and ``CRITICAL_SUCCESS`` count — ``NEAR_SUCCESS`` is a
failure, which keeps the aggression DC meaningful.

This module does **not** finalise the encounter — it only rolls the
check and marks enemies as fled on success. The pipeline-level caller
(``ActionPipeline._resolve_talk_in_combat``) calls
:func:`bot.combat_end.finalize_combat` with ``CombatEndReason.TRUCE``
once this helper reports success. That keeps the import graph clean:
``combat_truce`` only imports engine primitives, not ``combat_end``.
"""

from __future__ import annotations

import logging

from engine.character import Ability, compute_modifier
from engine.combat import CombatSide, CombatState, Combatant
from engine.dice import D20CheckResult, RollOutcome, roll_check
from engine.npc_stat_block import NPCTier

logger = logging.getLogger(__name__)


# Proficiency bonus applied to the CHA check. MVP: flat +2 for all PCs,
# because the character system does not yet model Persuasion/Intimidation/
# Deception skill proficiencies. A future chantier on skill proficiencies
# can plug the real bonus in at this single call site.
_PROFICIENCY_BONUS = 2

# Only these outcomes count as a successful truce. NEAR_SUCCESS
# deliberately fails so the aggression_threshold DC stays meaningful.
_SUCCESS_OUTCOMES: frozenset[RollOutcome] = frozenset({
    RollOutcome.SUCCESS,
    RollOutcome.CRITICAL_SUCCESS,
})


def attempt_truce(
    actor: Combatant,
    target: Combatant,
    state: CombatState,
) -> tuple[bool, D20CheckResult | None, str]:
    """Try to talk an enemy down. Returns ``(succeeded, check, summary)``.

    The actor's Action slot is consumed in **all** cases where the roll
    was rolled — failing a TRUCE attempt still costs you the action, the
    same way a missed attack does.

    Auto-refusals (mindless, boss-phase-2) short-circuit without rolling
    a check and without consuming the Action: the validator should have
    rejected them upstream (task 81.2) but the guard stays here for
    defence-in-depth since runtime phase flags can flip after validation.

    On success, every alive enemy combatant is marked ``fled=True`` so
    :func:`~engine.combat.check_combat_end` reports ``FLED`` for the
    follow-up ``advance_turn``. The caller is responsible for calling
    :func:`bot.combat_end.finalize_combat` with
    ``CombatEndReason.TRUCE`` — this function never touches end-of-combat
    state directly.
    """
    sb = target.stat_block
    if sb is None:
        return (
            False,
            None,
            f"{target.name} ne peut pas être raisonné.",
        )
    if sb.mindless:
        return (
            False,
            None,
            f"{target.name} est trop bestial pour entendre raison.",
        )
    if _is_boss_in_phase_2(sb):
        return (
            False,
            None,
            (
                f"{target.name} est dans une rage absolue — "
                "aucune parole ne l'atteint désormais."
            ),
        )

    dc = sb.aggression_threshold
    cha_score = actor.character.ability_scores.get(Ability.CHA)
    total_mod = compute_modifier(cha_score) + _PROFICIENCY_BONUS
    expression = (
        f"1d20+{total_mod}" if total_mod >= 0 else f"1d20{total_mod}"
    )
    check = roll_check(expression, dc)

    # Consume the actor's Action slot even on failure — same as a missed
    # attack. Leaves a visible mechanical cost to the attempt.
    actor.action_budget.action_used = True

    if check.outcome in _SUCCESS_OUTCOMES:
        logger.info(
            "TRUCE success combat_id=%s actor=%s target=%s roll=%d dc=%d",
            state.combat_id, actor.name, target.name, check.total, dc,
        )
        _mark_enemies_fled(state)
        return (
            True,
            check,
            (
                f"{actor.name} convainc {target.name} de cesser "
                f"le combat (CHA {check.total} vs DC {dc})."
            ),
        )

    logger.info(
        "TRUCE failure combat_id=%s actor=%s target=%s roll=%d dc=%d",
        state.combat_id, actor.name, target.name, check.total, dc,
    )
    return (
        False,
        check,
        (
            f"{actor.name} tente de raisonner {target.name}, "
            f"mais sans succès (CHA {check.total} vs DC {dc})."
        ),
    )


def _is_boss_in_phase_2(sb) -> bool:
    """Return True if ``sb`` is a BOSS with a triggered phase at ≤50% HP.

    The plan refuses truce once a boss has entered its second phase:
    narratively, it's past the point of return. We look at
    :attr:`~engine.npc_stat_block.PhaseTransition.triggered` rather than
    the current HP directly, because phase transitions are tracked
    explicitly by the engine's ``check_phase_transition`` hook (task 54).
    """
    if sb.tier != NPCTier.BOSS:
        return False
    return any(
        phase.triggered and phase.trigger_hp_percent <= 50
        for phase in sb.phases
    )


def _mark_enemies_fled(state: CombatState) -> None:
    """Flag every alive ENEMY combatant as ``fled=True``.

    Subsequent :func:`~engine.combat.check_combat_end` calls will then
    report FLED (or DEFEAT if no PCs survive — but finalize_combat
    overrides the reason to TRUCE anyway).
    """
    for c in state.combatants:
        if c.side == CombatSide.ENEMY and c.is_alive:
            c.fled = True
