"""Centralised end-of-combat finalisation.

Single entry point called by :class:`bot.combat_turn_manager.TurnManager`
and :meth:`bot.action_pipeline.ActionPipeline._resolve_flee` (and the
truce path) to wrap up a combat encounter: build a structured summary,
apply XP to survivors, purge transient conditions, and freeze the state
for the hub UI to render.

The function is **idempotent** — guarded by ``CombatState._finalized`` —
so both the pipeline-level finalisation (flee/truce) and the TurnManager's
post-``advance_turn`` call can run without double-counting XP or removing
already-removed conditions.

No LLM calls. Pure Python.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from engine.character import add_xp, check_level_up
from engine.combat import CombatEndReason, CombatSide, CombatState, Combatant
from engine.conditions import ConditionType, remove_condition
from engine.npc_stat_block import NPCTier

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


# XP rewards by tier — MVP values, no progression curve.
_XP_BY_TIER: dict[NPCTier, int] = {
    NPCTier.MINION: 50,
    NPCTier.ELITE: 150,
    NPCTier.BOSS: 500,
}
_XP_FALLBACK = 25
"""XP granted for enemies without a stat_block (legacy / trivial NPCs)."""

# Conditions that make no sense to carry over once the fight is over.
_TRANSIENT_CONDITIONS: frozenset[ConditionType] = frozenset(
    {ConditionType.SURPRISED, ConditionType.CONCENTRATING},
)


@dataclass
class CombatEndSummary:
    """Structured output of :func:`finalize_combat`.

    Consumed by :func:`bot.embeds.combat_end_embed.build_combat_end_embed`
    to render the end-of-fight recap. All list fields default to empty so
    an embed can safely show only the populated ones.
    """

    reason: CombatEndReason
    rounds_taken: int
    survivors_pc: list[str] = field(default_factory=list)
    survivors_enemy: list[str] = field(default_factory=list)
    killed_pcs: list[str] = field(default_factory=list)
    killed_enemies: list[str] = field(default_factory=list)
    fled_pcs: list[str] = field(default_factory=list)
    loot_items: list[str] = field(default_factory=list)
    xp_earned: int = 0
    """XP *per surviving PC* — the value is already applied to each
    survivor's ``Character.xp`` by :func:`finalize_combat` on the first
    call. Embeds display this as 'Experience earned per survivor'."""
    level_ups: list[str] = field(default_factory=list)
    """Names of PCs whose XP passed the next-level threshold. Surfaces the
    ``/level_up`` hint in the end embed — no automatic levelup (cf. task
    80 plan). Computed after ``add_xp`` runs on each survivor."""
    narrative: str = ""


def finalize_combat(
    session: "GameSession",
    reason: CombatEndReason,
) -> CombatEndSummary:
    """Wrap up the active combat on ``session`` and return a recap summary.

    First call: mutates ``session.combat_state`` (flips ``is_active`` off,
    sets ``end_reason``, applies XP to survivor PCs, purges SURPRISED and
    CONCENTRATING conditions). Sets the private ``_finalized`` flag so
    subsequent calls are short-circuited.

    Subsequent calls: return an equivalent ``CombatEndSummary`` computed
    from the frozen state without re-applying any effect. This is the
    idempotence guarantee used by the pipeline → TurnManager handoff
    (see task 80 plan).

    ``session.combat_state`` is **not** reset to ``None`` — the state
    stays for inspection, tests, and post-combat history. The next call
    to :mod:`bot.combat_entry` resets it before bootstrapping a new
    encounter.
    """
    state = session.combat_state
    if state is None:
        raise ValueError("finalize_combat called with no active combat_state")

    summary = _compute_summary(state, reason)

    if state._finalized:
        logger.debug(
            "finalize_combat: already finalized combat_id=%s reason=%s "
            "(returning recomputed summary)",
            state.combat_id,
            reason,
        )
        return summary

    # First-run mutations — guarded by the _finalized flag.
    state.end_reason = reason
    state.is_active = False

    level_ups = _apply_xp_to_survivors(state, summary.xp_earned)
    summary.level_ups = level_ups
    _cleanup_combat_state(session, state)

    state._finalized = True

    logger.info(
        "finalize_combat: combat_id=%s reason=%s rounds=%d xp_per_survivor=%d "
        "killed_enemies=%d killed_pcs=%d fled_pcs=%d",
        state.combat_id,
        reason,
        summary.rounds_taken,
        summary.xp_earned,
        len(summary.killed_enemies),
        len(summary.killed_pcs),
        len(summary.fled_pcs),
    )
    return summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_summary(
    state: CombatState, reason: CombatEndReason,
) -> CombatEndSummary:
    """Pure computation of a ``CombatEndSummary`` from a combat state.

    Does not mutate ``state``. Safe to call on an already-finalised state
    (idempotence path).
    """
    survivors_pc: list[str] = []
    killed_pcs: list[str] = []
    fled_pcs: list[str] = []
    survivors_enemy: list[str] = []
    killed_enemies: list[Combatant] = []

    for c in state.combatants:
        if c.side == CombatSide.PLAYER:
            if not c.is_alive:
                killed_pcs.append(c.name)
            elif c.fled:
                fled_pcs.append(c.name)
            else:
                survivors_pc.append(c.name)
        else:  # ENEMY
            if not c.is_alive:
                killed_enemies.append(c)
            elif not c.fled:
                survivors_enemy.append(c.name)

    # XP total — sum per tier, then divide equally among PC survivors.
    xp_total = sum(_xp_for_enemy(c) for c in killed_enemies)
    xp_per_survivor = (
        xp_total // len(survivors_pc) if survivors_pc else 0
    )

    # Loot MVP — the primary attack name of each fallen enemy becomes a
    # placeholder trophy. Richer loot tables are left to a future chantier.
    loot_items: list[str] = []
    for enemy in killed_enemies:
        sb = enemy.stat_block
        if sb is not None and sb.attacks:
            loot_items.append(sb.attacks[0].name)

    return CombatEndSummary(
        reason=reason,
        rounds_taken=state.round_number,
        survivors_pc=survivors_pc,
        survivors_enemy=survivors_enemy,
        killed_pcs=killed_pcs,
        killed_enemies=[c.name for c in killed_enemies],
        fled_pcs=fled_pcs,
        loot_items=loot_items,
        xp_earned=xp_per_survivor,
    )


def _xp_for_enemy(enemy: Combatant) -> int:
    """XP reward for a single fallen enemy, based on stat_block tier."""
    sb = enemy.stat_block
    if sb is None:
        return _XP_FALLBACK
    return _XP_BY_TIER.get(sb.tier, _XP_FALLBACK)


def _apply_xp_to_survivors(
    state: CombatState, xp_per_survivor: int,
) -> list[str]:
    """Credit ``xp_per_survivor`` to every alive, non-fled PC in ``state``.

    No levelup triggered here — the ``/level_up`` command remains the
    single entry point for levelup. This helper only bumps the raw XP
    counter on each survivor's ``Character`` via :func:`engine.character.add_xp`
    and returns the names of PCs whose XP crossed the next-level threshold
    so the embed can surface a "level up available" hint.
    """
    level_ups: list[str] = []
    if xp_per_survivor <= 0:
        return level_ups
    for c in state.combatants:
        if c.side != CombatSide.PLAYER:
            continue
        if not c.is_alive or c.fled:
            continue
        add_xp(c.character, xp_per_survivor)
        if check_level_up(c.character):
            level_ups.append(c.name)
    return level_ups


def _cleanup_combat_state(
    session: "GameSession", state: CombatState,
) -> None:
    """Purge transient conditions from surviving combatants.

    SURPRISED and CONCENTRATING make no sense once combat ends:
    - SURPRISED is a first-round-only marker.
    - CONCENTRATING can keep ticking off-combat, but since we freeze the
      state, any concentration-bound effect is orphaned. Dropping it here
      keeps the narrative contract simple.

    Every other condition (POISONED, PRONE, FRIGHTENED, etc.) is
    preserved — those can legitimately persist out of combat.
    """
    for c in state.combatants:
        if not c.is_alive:
            continue
        for cond_type in _TRANSIENT_CONDITIONS:
            # remove_condition is a no-op if the condition isn't present,
            # so we don't need to pre-check — but it does log a warning,
            # which would be noisy. Gate on presence.
            if any(ac.condition_type == cond_type for ac in c.conditions):
                remove_condition(c.conditions, cond_type)
