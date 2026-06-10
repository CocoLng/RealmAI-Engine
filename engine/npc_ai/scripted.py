"""Scripted NPC tactical brain — minion tier (task 50).

The minion brain is a pure heuristic: no LLM, no dice rolling, no planning
horizon. It picks the weakest enemy currently in range, falls back on a BFS
step toward the closest enemy, and dodges if everything is blocked. Elite
and boss logic layer on top in :mod:`engine.npc_ai.elite` (task 51) and
:mod:`engine.npc_ai.boss_brain` (task 52).

The brain produces an :class:`NPCActionPlan`. Execution is handled by
:func:`execute_action_plan`, which consumes the combatant's action budget,
delegates attacks to :func:`engine.combat.resolve_npc_attack`, and routes
movement through :func:`engine.combat.move_combatant_to_zone`. The engine
remains the sole arbiter of dice and damage.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from engine.combat import (
    Combatant,
    CombatState,
    consume_action,
    move_combatant_to_zone,
    resolve_npc_attack,
)
from engine.npc_stat_block import NPCAttack, NPCTier, SignatureAbility
from engine.validators import ActionType

if TYPE_CHECKING:
    from world.location import Location

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plan model
# ---------------------------------------------------------------------------


class NPCActionPlan(BaseModel):
    """A planned action decided by an NPC AI brain.

    The plan is the engine-friendly intermediate between "brain decides"
    and "engine executes". For attack plans, ``target_name`` and
    ``weapon_name`` point at the target combatant and the named
    :class:`~engine.npc_stat_block.NPCAttack` on the attacker's stat
    block. For movement plans, ``move_to_zone`` names the destination.
    ``signature_name`` is reserved for elite/boss brains that want to
    route through :func:`engine.npc_ai.elite.execute_signature_ability`
    (task 51); the minion brain leaves it ``None``.
    """

    action_type: ActionType
    target_name: str | None = None
    weapon_name: str | None = None
    move_to_zone: str | None = None
    signature_name: str | None = None
    rationale: str = Field(default="")


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def decide_minion_action(
    combatant: Combatant,
    state: CombatState,
    location: "Location | None",
) -> NPCActionPlan:
    """Heuristic decision for a minion-tier NPC.

    The rules, in order:

    1. If at least one living enemy is in range (melee = same zone,
       ranged = any zone), attack the one with the lowest current HP
       (tiebreak on AC ascending). Single-attack contract.
    2. Else if an enemy is reachable via the zone adjacency graph,
       step one zone toward them (BFS first-step).
    3. Else fall back on Dodge.
    """
    enemies = _living_opposites(combatant, state)

    in_range = [e for e in enemies if _in_attack_range(combatant, e, location)]
    if in_range:
        target = _pick_weakest(in_range)
        return NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name=target.name,
            weapon_name=_primary_weapon_name(combatant),
            rationale=f"Attack weakest enemy in range: {target.name}",
        )

    if location is not None and combatant.current_zone is not None:
        target_zone = _closest_enemy_zone(combatant, enemies, location)
        if target_zone is not None:
            return NPCActionPlan(
                action_type=ActionType.MOVE,
                move_to_zone=target_zone,
                rationale=f"Move toward enemy zone: {target_zone}",
            )

    return NPCActionPlan(
        action_type=ActionType.DEFEND,
        rationale="No valid target or movement — holding ground",
    )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def decide_action_for(
    combatant: Combatant,
    state: CombatState,
    location: "Location | None",
) -> NPCActionPlan:
    """Tier dispatcher — route an NPC to the brain matching its stat block.

    Minion tier uses :func:`decide_minion_action`. Elite tier is routed to
    :func:`engine.npc_ai.elite.decide_elite_action`. Boss tier will route
    through ``engine.npc_ai.boss_brain`` when task 52 lands. Combatants
    without a stat block fall back on the minion heuristic.
    """
    if combatant.stat_block is None:
        return decide_minion_action(combatant, state, location)

    tier = combatant.stat_block.tier
    if tier == NPCTier.MINION:
        return decide_minion_action(combatant, state, location)
    if tier == NPCTier.ELITE:
        from engine.npc_ai.elite import decide_elite_action

        return decide_elite_action(combatant, state, location)
    # Boss tier — pending task 52. For now, fall back on elite logic.
    from engine.npc_ai.elite import decide_elite_action

    return decide_elite_action(combatant, state, location)


def execute_action_plan(
    combatant: Combatant,
    plan: NPCActionPlan,
    state: CombatState,
    location: "Location | None",
) -> str:
    """Execute a planned action and return a one-line human summary.

    Mutates ``state`` (and ``combatant``) in place. ATTACK plans consume
    the combatant's Action slot and delegate to
    :func:`engine.combat.resolve_npc_attack` using the named
    :class:`~engine.npc_stat_block.NPCAttack` from the stat block, or
    routes to :func:`engine.npc_ai.elite.execute_signature_ability` when
    ``plan.signature_name`` is set. MOVE plans are routed through
    :func:`engine.combat.move_combatant_to_zone` which enforces adjacency,
    spends movement, and triggers opportunity attacks. DEFEND plans just
    consume the Action slot.

    The function never raises on a badly pointed plan — if the target or
    the weapon is not found, it logs the diagnostic and returns a clean
    summary without mutating anything. Validation errors from the engine
    (insufficient movement, non-adjacent zone, etc.) do propagate.

    Summaries are written in French — the TurnManager posts them verbatim
    in the Discord channel as the NPC turn recap (audit H14). Internal
    diagnostics (unknown target, missing attack, unsupported action type)
    go to the log, never to the players.
    """
    if plan.action_type == ActionType.ATTACK:
        if plan.signature_name is not None:
            return _execute_signature(combatant, plan, state)
        return _execute_attack(combatant, plan, state)
    if plan.action_type == ActionType.MOVE:
        return _execute_move(combatant, plan, state, location)
    if plan.action_type == ActionType.DEFEND:
        consume_action(combatant)
        return f"{combatant.name} esquive"
    logger.warning(
        "NPC %s planned unsupported action type %r — no-op.",
        combatant.name,
        plan.action_type.value,
    )
    return f"{combatant.name} ne fait rien"


def _execute_signature(
    combatant: Combatant,
    plan: NPCActionPlan,
    state: CombatState,
) -> str:
    """Resolve a signature-ability plan via the elite executor.

    Consumes the combatant's Action slot, looks up the signature by name
    on the stat block, locates the primary target from ``plan.target_name``
    (falling back on the caster itself for ``target_scope=self`` style
    effects), and delegates to
    :func:`engine.npc_ai.elite.execute_signature_ability`.
    """
    if combatant.stat_block is None:
        logger.warning(
            "NPC %s has no stat block to resolve signature %r — no-op.",
            combatant.name,
            plan.signature_name,
        )
        return f"{combatant.name} ne peut pas utiliser {plan.signature_name}"

    signature: SignatureAbility | None = None
    for sig in combatant.stat_block.signature_abilities:
        if sig.name == plan.signature_name:
            signature = sig
            break
    if signature is None:
        logger.warning(
            "NPC %s has no signature named %r — no-op.",
            combatant.name,
            plan.signature_name,
        )
        return f"{combatant.name} ne peut pas utiliser {plan.signature_name}"

    if signature.uses_remaining == 0:
        # Budget chokepoint (audit H19): the decision layers filter
        # exhausted signatures, but a mispointed plan must not fire the
        # once-per-combat nuke anyway.
        return (
            f"{combatant.name} cannot use {signature.name}: no uses remaining"
        )

    target = _find_by_name(plan.target_name, state)
    targets: list[Combatant] = [target] if target is not None else [combatant]

    from engine.npc_ai.elite import execute_signature_ability

    consume_action(combatant)
    summaries = execute_signature_ability(combatant, signature, targets, state)
    joined = "; ".join(summaries) if summaries else "aucun effet"
    return f"{combatant.name} utilise {signature.name} : {joined}"


# ---------------------------------------------------------------------------
# Private helpers — execution
# ---------------------------------------------------------------------------


def _execute_attack(
    combatant: Combatant,
    plan: NPCActionPlan,
    state: CombatState,
) -> str:
    target = _find_by_name(plan.target_name, state)
    if target is None:
        logger.warning(
            "NPC %s could not find attack target %r — no-op.",
            combatant.name,
            plan.target_name,
        )
        return f"{combatant.name} ne trouve aucune cible"

    npc_attack = _find_npc_attack(combatant, plan.weapon_name)
    if npc_attack is None:
        logger.warning(
            "NPC %s has no stat-block attack named %r — no-op.",
            combatant.name,
            plan.weapon_name,
        )
        return f"{combatant.name} ne peut pas attaquer"

    consume_action(combatant)
    result = resolve_npc_attack(combatant, target, npc_attack)
    if result.hit:
        return (
            f"{combatant.name} touche {target.name} avec {npc_attack.name} "
            f"— {result.damage} dégâts"
        )
    return f"{combatant.name} rate {target.name} avec {npc_attack.name}"


def _execute_move(
    combatant: Combatant,
    plan: NPCActionPlan,
    state: CombatState,
    location: "Location | None",
) -> str:
    if plan.move_to_zone is None or location is None:
        return f"{combatant.name} ne peut pas se déplacer"
    move_combatant_to_zone(state, combatant, plan.move_to_zone, location)
    return f"{combatant.name} se déplace vers {plan.move_to_zone}"


# ---------------------------------------------------------------------------
# Private helpers — selection
# ---------------------------------------------------------------------------


def _living_opposites(me: Combatant, state: CombatState) -> list[Combatant]:
    """Return living, non-fled combatants on the other side."""
    return [
        c
        for c in state.combatants
        if c.side != me.side and c.is_alive and not c.fled
    ]


def _pick_weakest(combatants: list[Combatant]) -> Combatant:
    """Pick the weakest target: lowest HP, then lowest AC tiebreak."""
    return min(
        combatants,
        key=lambda c: (c.character.hp, c.character.ac),
    )


def _in_attack_range(
    attacker: Combatant,
    target: Combatant,
    location: "Location | None",
) -> bool:
    """Return True if ``attacker`` can reach ``target`` with its primary kit.

    Melee attacks require the same zone. A ranged attack on the stat
    block makes the attacker "always in range" from any zone (simplified
    5e ranged — no line of sight, no range bracket for scripted minions).
    Zoneless encounters treat everyone as in range.
    """
    if location is None or not location.has_combat_zones():
        return True
    if attacker.current_zone is None or target.current_zone is None:
        return True
    if attacker.current_zone == target.current_zone:
        return True
    if attacker.stat_block is not None:
        for atk in attacker.stat_block.attacks:
            if atk.range_type == "ranged":
                return True
    return False


def _closest_enemy_zone(
    me: Combatant,
    enemies: list[Combatant],
    location: "Location",
) -> str | None:
    """BFS from ``me``'s zone — return the first step toward the nearest enemy.

    Returns the name of the immediately adjacent zone to step into
    (not the final target zone). ``None`` if no enemy is reachable or
    if ``me`` has no current zone or the start zone is unknown.
    """
    if me.current_zone is None:
        return None
    start_zone = location.get_zone(me.current_zone)
    if start_zone is None:
        return None

    enemy_zones = {e.current_zone for e in enemies if e.current_zone is not None}
    if not enemy_zones:
        return None

    visited: set[str] = {me.current_zone}
    queue: deque[tuple[str, str]] = deque()
    for adj in start_zone.adjacent_zone_names:
        if adj in visited:
            continue
        queue.append((adj, adj))
        visited.add(adj)

    while queue:
        current, first_step = queue.popleft()
        if current in enemy_zones:
            return first_step
        zone = location.get_zone(current)
        if zone is None:
            continue
        for adj in zone.adjacent_zone_names:
            if adj in visited:
                continue
            visited.add(adj)
            queue.append((adj, first_step))
    return None


def _primary_weapon_name(combatant: Combatant) -> str | None:
    """Return the first stat-block attack name, or ``None`` if absent."""
    if combatant.stat_block is None:
        return None
    if not combatant.stat_block.attacks:
        return None
    return combatant.stat_block.attacks[0].name


def _find_by_name(name: str | None, state: CombatState) -> Combatant | None:
    """Linear lookup of a combatant by name."""
    if name is None:
        return None
    for c in state.combatants:
        if c.name == name:
            return c
    return None


def _find_npc_attack(combatant: Combatant, name: str | None) -> NPCAttack | None:
    """Return the named stat-block attack, or ``None`` if not found."""
    if combatant.stat_block is None:
        return None
    if name is None:
        if combatant.stat_block.attacks:
            return combatant.stat_block.attacks[0]
        return None
    for atk in combatant.stat_block.attacks:
        if atk.name == name:
            return atk
    return None
