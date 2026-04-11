"""Condition system — status effects, exhaustion, duration tracking.

Pure deterministic Python (no LLM).
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from engine.character import Ability, compute_modifier
from engine.dice import D20CheckResult, roll_check

if TYPE_CHECKING:
    from engine.combat import Combatant

logger = logging.getLogger(__name__)

# Maximum exhaustion level (SRD 5e). Death occurs at this level.
MAX_EXHAUSTION_LEVEL = 6


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConditionType(StrEnum):
    """SRD 5e status conditions."""

    BLINDED = "Blinded"
    CHARMED = "Charmed"
    DEAFENED = "Deafened"
    FRIGHTENED = "Frightened"
    GRAPPLED = "Grappled"
    INCAPACITATED = "Incapacitated"
    INVISIBLE = "Invisible"
    PARALYZED = "Paralyzed"
    PETRIFIED = "Petrified"
    POISONED = "Poisoned"
    PRONE = "Prone"
    RESTRAINED = "Restrained"
    STUNNED = "Stunned"
    UNCONSCIOUS = "Unconscious"
    EXHAUSTION = "Exhaustion"
    SURPRISED = "Surprised"
    CONCENTRATING = "Concentrating"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ActiveCondition(BaseModel):
    """A condition currently affecting a creature."""

    condition_type: ConditionType
    source: str = ""
    duration_rounds: int | None = Field(default=None, ge=1)
    save_ability: Ability | None = None
    save_dc: int | None = Field(default=None, ge=1)
    # le=6 must be a literal for Pydantic; see MAX_EXHAUSTION_LEVEL constant.
    exhaustion_level: int = Field(default=0, ge=0, le=6)


# ---------------------------------------------------------------------------
# Lookup Tables
# ---------------------------------------------------------------------------

CONDITIONS_GRANTING_ADVANTAGE_AGAINST: frozenset[ConditionType] = frozenset({
    ConditionType.BLINDED,
    ConditionType.PARALYZED,
    ConditionType.PETRIFIED,
    ConditionType.PRONE,
    ConditionType.RESTRAINED,
    ConditionType.STUNNED,
    ConditionType.UNCONSCIOUS,
})

CONDITIONS_IMPOSING_ATTACK_DISADVANTAGE: frozenset[ConditionType] = frozenset({
    ConditionType.BLINDED,
    ConditionType.FRIGHTENED,
    ConditionType.POISONED,
    ConditionType.PRONE,
    ConditionType.RESTRAINED,
})

CONDITIONS_PREVENTING_MOVEMENT: frozenset[ConditionType] = frozenset({
    ConditionType.GRAPPLED,
    ConditionType.PARALYZED,
    ConditionType.PETRIFIED,
    ConditionType.RESTRAINED,
    ConditionType.STUNNED,
    ConditionType.UNCONSCIOUS,
})

CONDITIONS_CAUSING_INCAPACITATION: frozenset[ConditionType] = frozenset({
    ConditionType.INCAPACITATED,
    ConditionType.PARALYZED,
    ConditionType.PETRIFIED,
    ConditionType.STUNNED,
    ConditionType.UNCONSCIOUS,
})

CONDITIONS_AUTO_FAIL_STR_DEX_SAVES: frozenset[ConditionType] = frozenset({
    ConditionType.PARALYZED,
    ConditionType.PETRIFIED,
    ConditionType.STUNNED,
    ConditionType.UNCONSCIOUS,
})


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def apply_condition(
    conditions: list[ActiveCondition], condition: ActiveCondition
) -> list[ActiveCondition]:
    """Add a condition to the list. Mutates the conditions list in place.

    - EXHAUSTION: if already present, increments exhaustion_level (stacks to
      MAX_EXHAUSTION_LEVEL). If already at max, does nothing.
    - Other types: if same type already present, replaces it (new source/duration).
    - If not present, appends.
    """
    for i, existing in enumerate(conditions):
        if existing.condition_type == condition.condition_type:
            if condition.condition_type == ConditionType.EXHAUSTION:
                if existing.exhaustion_level < MAX_EXHAUSTION_LEVEL:
                    conditions[i] = existing.model_copy(
                        update={
                            "exhaustion_level": min(
                                existing.exhaustion_level + 1,
                                MAX_EXHAUSTION_LEVEL,
                            )
                        }
                    )
            else:
                conditions[i] = condition
            return conditions
    conditions.append(condition)
    return conditions


def remove_condition(
    conditions: list[ActiveCondition], condition_type: ConditionType
) -> list[ActiveCondition]:
    """Remove all conditions of the given type. Mutates the conditions list in place.

    Returns the list unchanged if condition not found (logs a warning).
    """
    indices = [i for i, c in enumerate(conditions) if c.condition_type == condition_type]
    if not indices:
        logger.warning("Condition %s not found, skipping removal", condition_type)
        return conditions
    for i in reversed(indices):
        conditions.pop(i)
    return conditions


def has_condition(
    conditions: list[ActiveCondition], condition_type: ConditionType
) -> bool:
    """Check if a condition type is active."""
    return any(c.condition_type == condition_type for c in conditions)


def get_condition(
    conditions: list[ActiveCondition], condition_type: ConditionType
) -> ActiveCondition | None:
    """Get the active condition of a given type, or None."""
    for c in conditions:
        if c.condition_type == condition_type:
            return c
    return None


def tick_durations(conditions: list[ActiveCondition]) -> list[ActiveCondition]:
    """Decrement duration_rounds for all conditions. Remove expired ones.

    Indefinite conditions (None duration) are untouched.
    Returns the same list (mutates in place).
    """
    to_remove: list[int] = []
    for i, c in enumerate(conditions):
        if c.duration_rounds is not None:
            new_dur = c.duration_rounds - 1
            if new_dur <= 0:
                to_remove.append(i)
            else:
                conditions[i] = c.model_copy(update={"duration_rounds": new_dur})
    for i in reversed(to_remove):
        conditions.pop(i)
    return conditions


def has_disadvantage_on_attacks(conditions: list[ActiveCondition]) -> bool:
    """True if any active condition imposes disadvantage on attack rolls."""
    return any(
        c.condition_type in CONDITIONS_IMPOSING_ATTACK_DISADVANTAGE for c in conditions
    )


def grants_advantage_to_attackers(conditions: list[ActiveCondition]) -> bool:
    """True if any active condition grants advantage to creatures attacking this one."""
    return any(
        c.condition_type in CONDITIONS_GRANTING_ADVANTAGE_AGAINST for c in conditions
    )


def is_incapacitated(conditions: list[ActiveCondition]) -> bool:
    """True if any active condition causes incapacitation."""
    return any(
        c.condition_type in CONDITIONS_CAUSING_INCAPACITATION for c in conditions
    )


def cannot_move(conditions: list[ActiveCondition]) -> bool:
    """True if any active condition prevents movement."""
    return any(
        c.condition_type in CONDITIONS_PREVENTING_MOVEMENT for c in conditions
    )


def auto_fails_str_dex_saves(conditions: list[ActiveCondition]) -> bool:
    """True if any active condition causes automatic failure on STR/DEX saves."""
    return any(
        c.condition_type in CONDITIONS_AUTO_FAIL_STR_DEX_SAVES for c in conditions
    )


def get_exhaustion_level(conditions: list[ActiveCondition]) -> int:
    """Get current exhaustion level (0 if no exhaustion condition)."""
    cond = get_condition(conditions, ConditionType.EXHAUSTION)
    return cond.exhaustion_level if cond is not None else 0


# ---------------------------------------------------------------------------
# SURPRISED helpers
# ---------------------------------------------------------------------------


def is_surprised(conditions: list[ActiveCondition]) -> bool:
    """True if the SURPRISED condition is active."""
    return has_condition(conditions, ConditionType.SURPRISED)


def cannot_act_due_to_surprise(conditions: list[ActiveCondition]) -> bool:
    """A surprised creature cannot act on its first turn (SRD 5e).

    The turn manager calls this at turn start: if True, the combatant
    skips their action and move, then ``consume_surprise_if_present``
    clears the condition so the next turn plays normally.
    """
    return is_surprised(conditions)


def cannot_react_due_to_surprise(conditions: list[ActiveCondition]) -> bool:
    """A surprised creature cannot take reactions until that turn ends (SRD 5e)."""
    return is_surprised(conditions)


def consume_surprise_if_present(conditions: list[ActiveCondition]) -> bool:
    """Remove the SURPRISED condition. Returns True if it was present.

    Called by the turn manager at the END of the surprised creature's first
    turn. Idempotent: if the condition is not present, this is a no-op
    returning False (no warning logged, unlike ``remove_condition``).
    """
    if not has_condition(conditions, ConditionType.SURPRISED):
        return False
    # Inline removal to avoid the warning path in remove_condition.
    indices = [
        i for i, c in enumerate(conditions)
        if c.condition_type == ConditionType.SURPRISED
    ]
    for i in reversed(indices):
        conditions.pop(i)
    return True


# ---------------------------------------------------------------------------
# CONCENTRATING helpers
# ---------------------------------------------------------------------------


def is_concentrating(conditions: list[ActiveCondition]) -> bool:
    """True if the CONCENTRATING condition is active."""
    return has_condition(conditions, ConditionType.CONCENTRATING)


def check_concentration_save(
    combatant: Combatant,
    incoming_damage: int,
) -> D20CheckResult:
    """Roll a CON save to maintain concentration. SRD 5e: DC = max(10, damage // 2).

    Args:
        combatant: The concentrating creature who just took damage.
        incoming_damage: Damage dealt by the triggering hit (before resistance
            reductions are applied — RAW uses the final damage taken, callers
            must pass the post-mitigation value).

    Returns:
        A ``D20CheckResult`` with the roll, the DC, and the outcome.

    Raises:
        ValueError: If the combatant is not currently concentrating.
    """
    if not is_concentrating(combatant.conditions):
        raise ValueError(
            f"{combatant.name} is not concentrating — nothing to save against"
        )
    con_mod = compute_modifier(combatant.character.ability_scores.get(Ability.CON))
    dc = max(10, incoming_damage // 2)
    sign = "+" if con_mod >= 0 else "-"
    return roll_check(f"1d20{sign}{abs(con_mod)}", dc)


def drop_concentration(combatant: Combatant) -> None:
    """Remove CONCENTRATING from a combatant. Idempotent: no-op if absent.

    Callers are responsible for clearing any spell effects (ongoing damage,
    auras, buffs) that depended on the concentration — this function only
    touches the condition itself.
    """
    if not is_concentrating(combatant.conditions):
        return
    indices = [
        i for i, c in enumerate(combatant.conditions)
        if c.condition_type == ConditionType.CONCENTRATING
    ]
    for i in reversed(indices):
        combatant.conditions.pop(i)
