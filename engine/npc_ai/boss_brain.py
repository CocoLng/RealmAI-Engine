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
from typing import TYPE_CHECKING, Protocol

from engine.combat import Combatant, CombatState
from engine.contracts import TacticalDecision
from engine.npc_ai.elite import decide_elite_action
from engine.npc_ai.scripted import NPCActionPlan, _find_by_name, _find_npc_attack
from engine.npc_stat_block import SignatureAbility
from engine.validators import ActionType

if TYPE_CHECKING:
    from world.location import Location

logger = logging.getLogger(__name__)


_RETRY_LIMIT = 2

# Signature effect kinds that hurt their target — these require an enemy
# target, mirroring the side gate players get from the ActionValidator.
_HARMFUL_EFFECT_KINDS = frozenset({"damage", "aoe_damage", "condition", "debuff"})


class Tactician(Protocol):
    """Structural type for the LLM boss tactician injected by the bot.

    The concrete implementation lives in ``ai.npc_tactician.NPCTactician``.
    Typing the parameter as a Protocol keeps ``engine/`` free of any ``ai``
    import while still type-checking the ``.decide(...)`` call below.
    """

    def decide(
        self,
        boss: Combatant,
        state: CombatState,
        party_context: str,
        recent_events: list[str],
        language: str = "fr",
    ) -> TacticalDecision: ...


def decide_boss_action(
    combatant: Combatant,
    state: CombatState,
    location: "Location | None",
    tactician: Tactician,
    party_context: str = "",
    recent_events: list[str] | None = None,
    language: str = "fr",
) -> NPCActionPlan:
    """Decide a boss NPC's turn via the LLM tactician, with retries + fallback.

    The function retries the LLM call ``_RETRY_LIMIT`` times on ``ValueError``
    (bad JSON, failed schema validation, dangling references, or an
    engine-side rejection from :func:`_validate_decision` — dead/fled/allied
    target, melee across zones, signature out of budget). If every attempt
    fails, it falls back on :func:`decide_elite_action` so the boss still
    takes a turn instead of standing still. The fallback plan's
    ``rationale`` documents that the LLM was bypassed.
    """
    events = recent_events or []

    for attempt in range(1, _RETRY_LIMIT + 1):
        try:
            decision = tactician.decide(
                combatant, state, party_context, events, language,
            )
            _validate_decision(decision, combatant, state, location)
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


def _validate_decision(
    decision: TacticalDecision,
    boss: Combatant,
    state: CombatState,
    location: "Location | None",
) -> None:
    """Engine-side gate on a tactician decision (audit H19).

    The ai-layer only checks that referenced names exist. The engine is
    the authority: it re-resolves every reference against the actual
    combat state and rejects decisions a player would never be allowed
    to take — attacking the dead, the fled, or an ally; melee strikes
    across zones (the same gate players get from
    ``validators._check_range``); signatures with no uses left; moves to
    unknown or non-adjacent zones.

    Raises:
        ValueError: On any violation. The caller counts it as a failed
            attempt and retries or falls back on the scripted brain.
    """
    if decision.action_type == "attack":
        target = _require_living_enemy(decision.target_name, boss, state)
        attack = _find_npc_attack(boss, decision.weapon_name)
        if attack is None:
            raise ValueError(
                f"Boss {boss.name} has no stat-block attack named "
                f"{decision.weapon_name!r}"
            )
        if not _attack_reaches(boss, target, attack.range_type, location):
            raise ValueError(
                f"{attack.name} is melee and {target.name} is in zone "
                f"{target.current_zone!r} while {boss.name} is in "
                f"{boss.current_zone!r}"
            )

    elif decision.action_type == "signature":
        signature = _find_signature(boss, decision.signature_name)
        if signature is None:
            raise ValueError(
                f"Boss {boss.name} has no signature named "
                f"{decision.signature_name!r}"
            )
        if signature.uses_remaining == 0:
            raise ValueError(
                f"Signature {signature.name!r} has no uses remaining this combat"
            )
        if _is_harmful(signature):
            _require_living_enemy(decision.target_name, boss, state)
        elif decision.target_name is not None:
            _require_living_combatant(decision.target_name, state)

    elif decision.action_type == "move":
        if location is not None and location.has_combat_zones():
            zone = decision.move_to_zone
            if zone is None or location.get_zone(zone) is None:
                raise ValueError(f"Move to unknown zone {zone!r}")
            if boss.current_zone is not None and not location.are_adjacent(
                boss.current_zone, zone
            ):
                raise ValueError(
                    f"Zone {zone!r} is not adjacent to {boss.current_zone!r}"
                )


def _require_living_combatant(name: str | None, state: CombatState) -> Combatant:
    """Resolve ``name`` to a living, non-fled combatant or raise."""
    target = _find_by_name(name, state)
    if target is None:
        raise ValueError(f"Decision targets unknown combatant {name!r}")
    if not target.is_alive:
        raise ValueError(f"Decision targets dead combatant {name!r}")
    if target.fled:
        raise ValueError(f"Decision targets fled combatant {name!r}")
    return target


def _require_living_enemy(
    name: str | None,
    boss: Combatant,
    state: CombatState,
) -> Combatant:
    """Resolve ``name`` to a living enemy of ``boss`` or raise."""
    target = _require_living_combatant(name, state)
    if target.side == boss.side:
        raise ValueError(
            f"Decision targets {name!r} on the boss's own side"
        )
    return target


def _attack_reaches(
    attacker: Combatant,
    target: Combatant,
    range_type: str,
    location: "Location | None",
) -> bool:
    """Zone-aware range gate mirroring ``validators._check_range``.

    Melee/reach requires the same zone; ranged reaches any zone; zoneless
    combats put everyone in range.
    """
    if location is None or not location.has_combat_zones():
        return True
    if attacker.current_zone is None or target.current_zone is None:
        return True
    if attacker.current_zone == target.current_zone:
        return True
    return range_type == "ranged"


def _find_signature(boss: Combatant, name: str | None) -> SignatureAbility | None:
    """Return the named signature from the boss's stat block, or ``None``."""
    if boss.stat_block is None or name is None:
        return None
    for sig in boss.stat_block.signature_abilities:
        if sig.name == name:
            return sig
    return None


def _is_harmful(signature: SignatureAbility) -> bool:
    """True when any effect hurts its target (damage, condition, debuff)."""
    return any(e.kind in _HARMFUL_EFFECT_KINDS for e in signature.effects)


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
