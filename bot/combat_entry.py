"""Combat entry module — detect combat triggers and build party-wide CombatState.

This module is the single entry point for bringing a session from exploration
into combat. It handles four trigger kinds today (see
:class:`engine.combat_trigger.CombatTriggerKind`):

1. Explicit player ``ATTACK`` on a combat-worthy NPC.
2. ``IMPROVISE`` actions flagged by the interpreter as lethal intent
   (task 40 — the flag is tolerated as optional until then).
3. ``INTERACT`` on a location trap / trigger definition (task 41 will
   populate ``Location.combat_triggers``; for now this path is a no-op
   stub guarded by ``hasattr``).
4. ``TALK`` provocation reserved for task 81.

The module does **not** roll initiative — that belongs to
:func:`engine.combat.start_combat`, which consumes the trigger this module
produces.

Pure deterministic Python — no LLM calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai.models import InterpretedAction
from engine.combat import CombatState, Combatant
from engine.combat_trigger import (
    CombatTrigger,
    CombatTriggerKind,
    InitiativeSide,
)
from engine.validators import ActionType
from world.npc import NPC, NPCDisposition

if TYPE_CHECKING:
    from bot.game_session import GameSession


__all__ = [
    "CombatTrigger",
    "CombatTriggerKind",
    "InitiativeSide",
    "detect_combat_trigger",
    "enter_combat",
]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_combat_trigger(
    action: InterpretedAction,
    session: "GameSession",
) -> CombatTrigger | None:
    """Examine an interpreted action and return a trigger if it starts combat.

    Returns ``None`` for the vast majority of actions. The function is
    side-effect-free — callers may invoke it speculatively.
    """
    # CASE 1a — explicit Attack action against a combat-worthy NPC.
    if action.action_type == ActionType.ATTACK:
        target = _resolve_target_npc(action, session)
        if target is None or not _is_combat_worthy(target):
            return None
        return CombatTrigger(
            kind=CombatTriggerKind.PLAYER_ATTACK,
            aggressor_name=action.actor_name,
            enemy_names=[target.name],
            surprise_side=_compute_surprise_for_attack(target),
            narrative_hint=f"{action.actor_name} attaque {target.name}.",
        )

    # CASE 1b — lethal intent detected on an Improvise action (task 40).
    # The flag is optional until the interpreter ships it; tolerate absence.
    if action.action_type == ActionType.IMPROVISE and getattr(
        action, "is_lethal_intent", False
    ):
        target = _resolve_target_npc(action, session)
        if target is None or not _is_combat_worthy(target):
            return None
        return CombatTrigger(
            kind=CombatTriggerKind.LETHAL_INTENT,
            aggressor_name=action.actor_name,
            enemy_names=[target.name],
            surprise_side=InitiativeSide.PLAYERS,
            narrative_hint=f"{action.actor_name} dégaine contre {target.name}.",
        )

    # CASE 2 — interact with a scripted combat trigger on the current location.
    # Task 41 will populate ``Location.combat_triggers``; the hasattr guard keeps
    # this path dormant until then without failing.
    if action.action_type == ActionType.INTERACT:
        location = session.current_location
        if location is None or not hasattr(location, "combat_triggers"):
            return None
        combat_triggers = getattr(location, "combat_triggers", None)
        if not combat_triggers:
            return None
        trigger_def = combat_triggers.get(action.target_name or "")
        if trigger_def is None:
            return None
        spawn_npcs = list(getattr(trigger_def, "spawn_npcs", []))
        if not spawn_npcs:
            return None
        return CombatTrigger(
            kind=CombatTriggerKind.AMBUSH,
            aggressor_name=spawn_npcs[0],
            enemy_names=spawn_npcs,
            surprise_side=InitiativeSide.NPCS,
            narrative_hint=getattr(trigger_def, "reveal_narration", "") or "",
        )

    # CASE 3 — social provocation (task 81). No-op for now.
    return None


# ---------------------------------------------------------------------------
# Entry — assemble the party-wide CombatState
# ---------------------------------------------------------------------------


def enter_combat(
    session: "GameSession",
    trigger: CombatTrigger,
) -> CombatState:
    """Build a party-wide :class:`CombatState` from a validated trigger.

    - Every PC in ``session`` is added to the PLAYER side.
    - Each NPC named in ``trigger.enemy_names`` is resolved from
      ``session.npcs`` and added to the ENEMY side.
    - Initiative is **not** rolled here — call
      :func:`engine.combat.start_combat` with the trigger to apply the 5e
      surprise rules and order the combatants.
    - The resulting state is stored on ``session.combat_state``.

    Raises:
        ValueError: If none of the requested enemies can be found in the
        session's NPC registry.
    """
    # Local import avoids a circular dep: bot.cogs.combat imports from here
    # indirectly via engine.combat.
    from bot.cogs.combat import build_npc_combatant, build_pc_combatants

    pcs = build_pc_combatants(session)
    enemies: list[Combatant] = []
    for name in trigger.enemy_names:
        npc = session.npcs.get(name) if session.npcs else None
        if npc is None:
            continue
        enemies.append(build_npc_combatant(npc))

    if not enemies:
        raise ValueError(
            f"Cannot enter combat: no valid enemies found for trigger {trigger!r}"
        )

    state = CombatState(
        combatants=pcs + enemies,
        round_number=1,
        current_turn_index=0,
        is_active=True,
    )
    session.combat_state = state
    return state


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_target_npc(
    action: InterpretedAction,
    session: "GameSession",
) -> NPC | None:
    """Look up the action's target NPC in the session registry, if any."""
    if action.target_name is None or not session.npcs:
        return None
    return session.npcs.get(action.target_name)


def _is_combat_worthy(npc: NPC) -> bool:
    """Return True if this NPC should enter a full combat encounter.

    A commoner without a stat block and without combat-grade HP/AC goes
    through the :func:`engine.combat.trivial_resolve` fast path instead.
    """
    if npc.stat_block is not None:
        return True
    if npc.disposition == NPCDisposition.HOSTILE:
        return True
    if npc.max_hp >= 10 or npc.ac > 12:
        return True
    return False


def _compute_surprise_for_attack(target: NPC) -> InitiativeSide:
    """Decide the surprise side for an explicit player attack.

    An attack on an already-hostile or unfriendly NPC is a recognised
    face-off (case 3). An attack on a neutral/friendly NPC catches it
    off-guard (case 1).
    """
    if target.disposition in (NPCDisposition.HOSTILE, NPCDisposition.UNFRIENDLY):
        return InitiativeSide.BOTH_READY
    return InitiativeSide.PLAYERS
