"""Legendary actions off-turn — task 53.

D&D 5e bosses can spend legendary points **between** other creatures'
turns for off-turn attacks and effects. This module encodes the MVP
logic:

- 3 points per round, reset at the start of the boss's own turn.
- Each action costs 1, 2, or 3 points (spec on the stat block).
- After any PC turn, living bosses try to spend their points via a
  heuristic picker: cost-1 eagerly, cost-2 when available, cost-3 only
  when HP < 30% (desperate measure).
- Effects resolve through the existing signature pipeline by wrapping
  the legendary action's effects in an ephemeral ``SignatureAbility``.

No LLM call is involved in MVP; the scripted heuristic is sufficient for
a believable boss off-turn presence. The LLM tactician (task 52) may
extend this in a future pass via its ``legendary_action_name`` field.
"""

from __future__ import annotations

import logging

from engine.combat import Combatant, CombatState
from engine.npc_ai.elite import execute_signature_ability
from engine.npc_stat_block import (
    LegendaryAction,
    NPCTier,
    SignatureAbility,
)

logger = logging.getLogger(__name__)


def maybe_spend_legendary_action(
    state: CombatState,
    boss: Combatant,
    previous_combatant: Combatant,
) -> str | None:
    """Spend one legendary action off-turn if the boss can and should.

    Returns a human-readable summary when an action fired, ``None``
    otherwise. Mutates ``boss.legendary_points_remaining`` and the
    effect targets.

    Gate conditions (all must hold):
    - ``boss`` has a BOSS-tier stat block.
    - ``boss`` is alive and has not fled.
    - ``boss`` has legendary points left.
    - At least one legendary action is affordable with the remaining
      points.

    The previous combatant is used as the default target for damage and
    condition effects — a boss's off-turn is typically a reaction to
    the last thing it saw.
    """
    sb = boss.stat_block
    if sb is None or sb.tier != NPCTier.BOSS:
        return None
    if not boss.is_alive or boss.fled:
        return None
    if boss.legendary_points_remaining <= 0:
        return None

    affordable = [
        la
        for la in sb.legendary_actions
        if la.cost <= boss.legendary_points_remaining
    ]
    if not affordable:
        return None

    chosen = _pick_legendary(affordable, boss)
    if chosen is None:
        return None

    boss.legendary_points_remaining -= chosen.cost
    return _execute_legendary(chosen, boss, previous_combatant, state)


def _pick_legendary(
    options: list[LegendaryAction],
    boss: Combatant,
) -> LegendaryAction | None:
    """Heuristic selection — cost-3 only when desperate, cost-1 eager.

    The picker exists so test vectors are deterministic: given a sorted
    ``options`` list, the chosen action is a pure function of the boss's
    HP ratio and the available cost brackets.
    """
    hp_ratio = boss.character.hp / max(1, boss.character.max_hp)

    cost_3 = [la for la in options if la.cost == 3]
    if cost_3 and hp_ratio < 0.3:
        return cost_3[0]

    cost_2 = [la for la in options if la.cost == 2]
    if cost_2:
        return cost_2[0]

    cost_1 = [la for la in options if la.cost == 1]
    if cost_1:
        return cost_1[0]

    return None


def _execute_legendary(
    action: LegendaryAction,
    boss: Combatant,
    target: Combatant,
    state: CombatState,
) -> str:
    """Resolve a legendary action via the signature executor pipeline.

    The legendary action is wrapped in an ephemeral ``SignatureAbility``
    with the same effects and ``at_will`` usage so the decrement logic
    in :func:`execute_signature_ability` is a no-op. The target list is
    a single-element list built from ``target`` for single-scope effects;
    the executor handles the MVP kinds and degrades the non-MVP ones
    with a warning.
    """
    fake_sig = SignatureAbility(
        name=action.name,
        description=action.description,
        usage="at_will",
        uses_remaining=None,
        action_cost="reaction",
        effects=action.effects,
    )
    summaries = execute_signature_ability(boss, fake_sig, [target], state)
    joined = "; ".join(summaries) if summaries else "aucun effet"
    logger.debug(
        "Legendary action: %s spends %d point(s) on %s",
        boss.name,
        action.cost,
        action.name,
    )
    # Player-facing summary, posted verbatim in the combat channel:
    # clean French, no internal tags (audit H14).
    return f"{boss.name} utilise {action.name} : {joined}"
