"""Boss NPC brain — LLM tactician with scripted fallback (task 52).

Bosses use the LLM-driven :class:`~ai.npc_tactician.NPCTactician` to pick
their action, with a scripted fallback when the LLM refuses to behave
(invalid JSON, dangling references, network hiccup). The brain retries
twice before giving up on the LLM and falling back on the scripted
elite-AGGRESSIVE heuristic — the boss always plays, even if it plays
dumb for a turn.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ai.models import TacticalDecision
from engine.combat import Combatant, CombatState
from engine.npc_ai.elite import decide_elite_action
from engine.npc_ai.scripted import NPCActionPlan
from engine.validators import ActionType

if TYPE_CHECKING:
    from ai.npc_tactician import NPCTactician
    from world.location import Location

logger = logging.getLogger(__name__)


_RETRY_LIMIT = 2


def decide_boss_action(
    combatant: Combatant,
    state: CombatState,
    location: "Location | None",
    tactician: "NPCTactician",
    party_context: str = "",
    recent_events: list[str] | None = None,
    language: str = "fr",
) -> NPCActionPlan:
    """Decide a boss NPC's turn via the LLM tactician, with retries + fallback.

    The function retries the LLM call ``_RETRY_LIMIT`` times on ``ValueError``
    (bad JSON, failed schema validation, dangling references). If every
    attempt fails, it falls back on :func:`decide_elite_action` so the
    boss still takes a turn instead of standing still. The fallback
    plan's ``rationale`` documents that the LLM was bypassed.
    """
    events = recent_events or []

    for attempt in range(1, _RETRY_LIMIT + 1):
        try:
            decision = tactician.decide(
                combatant, state, party_context, events, language,
            )
        except ValueError as exc:
            logger.warning(
                "NPC tactician attempt %d/%d failed for %s: %s",
                attempt,
                _RETRY_LIMIT,
                combatant.name,
                exc,
            )
            continue
        return _decision_to_plan(decision)

    logger.warning(
        "NPC tactician giving up for %s after %d attempts — falling back to "
        "scripted elite brain.",
        combatant.name,
        _RETRY_LIMIT,
    )
    plan = decide_elite_action(combatant, state, location)
    plan.rationale = f"[LLM fallback] {plan.rationale}"
    return plan


def _decision_to_plan(decision: TacticalDecision) -> NPCActionPlan:
    """Map a :class:`TacticalDecision` onto the engine's :class:`NPCActionPlan`.

    ``"attack"`` and ``"signature"`` both route through ``ActionType.ATTACK``;
    ``signature_name`` on the plan is what tells the resolver which path
    to take. ``"dodge"`` and ``"disengage"`` both map to ``DEFEND`` for
    MVP (disengage-specific handling lands when task 24 flags are wired
    into the NPC turn path). ``"move"`` stays ``ActionType.MOVE``.

    ``decision.legendary_action_name`` is ignored in MVP — off-turn
    legendary actions are scripted (task 53).
    """
    mapping = {
        "attack": ActionType.ATTACK,
        "signature": ActionType.ATTACK,
        "move": ActionType.MOVE,
        "dodge": ActionType.DEFEND,
        "disengage": ActionType.DEFEND,
    }
    action_type = mapping[decision.action_type]

    return NPCActionPlan(
        action_type=action_type,
        target_name=decision.target_name,
        weapon_name=decision.weapon_name,
        signature_name=(
            decision.signature_name if decision.action_type == "signature" else None
        ),
        move_to_zone=decision.move_to_zone,
        rationale=decision.reasoning,
    )
