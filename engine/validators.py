"""Action validation — checks legality before engine resolves.

Pure deterministic Python (no LLM).
"""

import logging
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from engine.combat import CombatState, Combatant
from engine.conditions import cannot_move, is_incapacitated, is_surprised
from engine.inventory import EquipmentSlot, Weapon, WeaponCategory, WeaponProperty
from engine.spells import SPELL_CATALOG, CastingTime, can_cast_spell

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ActionType(StrEnum):
    """The types of actions a player can take.

    Combat actions (ATTACK through USE_ITEM) are resolved by the combat engine
    with a CombatState. Exploration actions (LOOK through INTERACT) happen
    outside combat and use scene context. IMPROVISE is a catch-all for creative
    actions the narrator arbitrates (usable in both combat and exploration).
    """

    # Combat
    ATTACK = "Attack"
    CAST_SPELL = "Cast Spell"
    DEFEND = "Defend"
    DISENGAGE = "Disengage"
    FLEE = "Flee"
    USE_ITEM = "Use Item"
    # Exploration
    LOOK = "Look"
    SEARCH = "Search"
    TALK = "Talk"
    MOVE = "Move"
    INTERACT = "Interact"
    PICKUP = "Pick Up"
    # Catch-all (works in combat and exploration)
    IMPROVISE = "Improvise"
    # Meta
    QUESTION = "Question"


EXPLORATION_ACTION_TYPES: frozenset[ActionType] = frozenset({
    ActionType.LOOK,
    ActionType.SEARCH,
    ActionType.TALK,
    ActionType.MOVE,
    ActionType.INTERACT,
    ActionType.PICKUP,
    ActionType.IMPROVISE,
    ActionType.QUESTION,
})


# Exploration actions still permitted while a CombatState is active.
# MOVE, TALK, SEARCH, INTERACT and PICKUP are refused so a player cannot
# walk out of combat with ``(Move) j'explore le couloir`` — they must use
# Flee (combat path) or wait for the encounter to end.
_EXPLORATION_ALLOWED_IN_COMBAT: frozenset[ActionType] = frozenset({
    ActionType.LOOK,
    ActionType.QUESTION,
    ActionType.IMPROVISE,
})


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Action(BaseModel):
    """A player-requested action to be validated before resolution."""

    actor_name: str = Field(min_length=1)
    action_type: ActionType
    target_name: str | None = None
    weapon_name: str | None = None
    spell_name: str | None = None
    item_name: str | None = None


class ValidationResult(BaseModel):
    """The result of validating an action."""

    is_valid: bool
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _find_combatant(name: str, state: CombatState) -> Combatant | None:
    """Find a combatant by name."""
    for c in state.combatants:
        if c.name == name:
            return c
    return None


def _is_actors_turn(actor_name: str, state: CombatState) -> bool:
    """Check if it's the named actor's turn."""
    current = state.combatants[state.current_turn_index]
    return current.name == actor_name


def _validate_common(action: Action, state: CombatState) -> ValidationResult | None:
    """Common checks for all actions. Returns ValidationResult if failed, None if OK."""
    actor = _find_combatant(action.actor_name, state)
    if actor is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' is not in combat",
        )
    if not actor.is_alive:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' is dead",
        )
    if not _is_actors_turn(action.actor_name, state):
        return ValidationResult(
            is_valid=False,
            error_message=f"It is not {action.actor_name}'s turn",
        )
    if is_incapacitated(actor.conditions):
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' is incapacitated",
        )
    return None


def _check_range(
    attacker: Combatant,
    target: Combatant,
    weapon: Weapon | None,
) -> bool:
    """Zone-aware range check. Melee = same zone only; ranged/thrown = any zone.

    Returns True for zoneless combats (current_zone is None on either side).
    """
    if attacker.current_zone is None or target.current_zone is None:
        return True  # zoneless combat — everyone in range
    if attacker.current_zone == target.current_zone:
        return True  # point-blank, any weapon type

    if weapon is None:
        return False  # unarmed = melee only

    is_ranged = weapon.weapon_category in (
        WeaponCategory.SIMPLE_RANGED,
        WeaponCategory.MARTIAL_RANGED,
    )
    is_thrown = WeaponProperty.THROWN in weapon.properties
    return is_ranged or is_thrown


# ---------------------------------------------------------------------------
# Public validators
# ---------------------------------------------------------------------------


def validate_action(action: Action, combat_state: CombatState) -> ValidationResult:
    """Validate a combat action. Common checks + surprised guard + type dispatch.

    Adds a SURPRISED safety net before dispatching: a surprised combatant
    cannot act (the turn manager should already skip them, but the validator
    enforces it as a belt-and-suspenders check). Unknown action types are
    rejected with a clear message rather than raising KeyError.
    """
    actor = _find_combatant(action.actor_name, combat_state)
    if actor is not None and is_surprised(actor.conditions):
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' est surpris et ne peut rien faire ce tour.",
        )

    validators: dict[ActionType, Any] = {
        ActionType.ATTACK: validate_attack,
        ActionType.CAST_SPELL: validate_cast_spell,
        ActionType.DEFEND: validate_defend,
        ActionType.DISENGAGE: validate_disengage,
        ActionType.FLEE: validate_flee,
        ActionType.USE_ITEM: validate_use_item,
        ActionType.MOVE: validate_move_in_combat,
    }
    validator = validators.get(action.action_type)
    if validator is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.action_type.value}' n'est pas une action de combat valide.",
        )
    return validator(action, combat_state)


def validate_attack(action: Action, state: CombatState) -> ValidationResult:
    """Validate an attack action.

    Checks (in order): common checks, action budget, target exists and alive,
    no friendly fire, weapon equipped, zone-based range.
    """
    common = _validate_common(action, state)
    if common is not None:
        return common

    actor = _find_combatant(action.actor_name, state)
    assert actor is not None  # checked in _validate_common

    # Action economy
    if actor.action_budget.action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà utilisé son Action ce tour.",
        )

    # Target required
    if action.target_name is None:
        return ValidationResult(
            is_valid=False, error_message="Attack requires a target"
        )

    target = _find_combatant(action.target_name, state)
    if target is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"Target '{action.target_name}' is not in combat",
        )
    if not target.is_alive:
        return ValidationResult(
            is_valid=False,
            error_message=f"Target '{action.target_name}' is already dead",
        )

    # No friendly fire
    if target.side == actor.side:
        return ValidationResult(
            is_valid=False,
            error_message=f"Impossible d'attaquer l'allié '{action.target_name}'.",
        )

    # Weapon check: need weapon_name and it must be equipped
    if action.weapon_name is None:
        return ValidationResult(
            is_valid=False, error_message="Attack requires a weapon"
        )

    weapon_obj: Weapon | None = None
    for slot in (EquipmentSlot.MAIN_HAND, EquipmentSlot.OFF_HAND):
        item = actor.inventory.equipped.get(slot)
        if item is not None and isinstance(item, Weapon) and item.name == action.weapon_name:
            weapon_obj = item
            break

    if weapon_obj is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"Weapon '{action.weapon_name}' is not equipped",
        )

    # Zone-based range check
    if not _check_range(actor, target, weapon_obj):
        return ValidationResult(
            is_valid=False,
            error_message=(
                f"'{action.target_name}' est hors de portée de "
                f"'{action.weapon_name}' (zones différentes, arme de mêlée)."
            ),
        )

    # TODO: Check weapon proficiency once the proficiency system is implemented.
    # Character currently has no weapon_proficiencies field. When added, verify
    # the weapon's category is in the character's proficiency list and adjust
    # the attack roll accordingly (no proficiency bonus if not proficient).

    return ValidationResult(is_valid=True)


def validate_cast_spell(action: Action, state: CombatState) -> ValidationResult:
    """Validate a spell cast action.

    Checks: common + is spellcaster + spell known + has slot.
    """
    common = _validate_common(action, state)
    if common is not None:
        return common

    actor = _find_combatant(action.actor_name, state)
    assert actor is not None

    if actor.spellcaster is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' is not a spellcaster",
        )

    if action.spell_name is None:
        return ValidationResult(
            is_valid=False,
            error_message="Cast Spell requires a spell name",
        )

    spell = SPELL_CATALOG.get(action.spell_name)
    if spell is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"Spell '{action.spell_name}' does not exist",
        )

    # Action economy based on casting time
    if spell.casting_time == CastingTime.ACTION and actor.action_budget.action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà utilisé son Action ce tour.",
        )
    if spell.casting_time == CastingTime.BONUS_ACTION and actor.action_budget.bonus_action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà utilisé sa Bonus Action ce tour.",
        )
    if spell.casting_time == CastingTime.REACTION and actor.action_budget.reaction_used_this_round:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà utilisé sa Réaction ce round.",
        )

    if not can_cast_spell(actor.spellcaster, spell):
        return ValidationResult(
            is_valid=False,
            error_message=f"Cannot cast '{action.spell_name}' (unknown or no slots)",
        )

    # Non-Self spells that deal damage or apply conditions require a target
    needs_target = (
        spell.spell_range.value != "Self"
        and (spell.damage_dice is not None or spell.condition_applied is not None)
    )
    if needs_target and action.target_name is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"Spell '{spell.name}' requires a target",
        )

    # If a target is specified, verify it exists in combat
    if action.target_name is not None:
        target = _find_combatant(action.target_name, state)
        if target is None:
            return ValidationResult(
                is_valid=False,
                error_message=f"Target '{action.target_name}' is not in combat",
            )

    # Concentration info: casting a new concentration spell is always legal per
    # SRD, but the old concentration drops. Log for upstream awareness.
    if spell.concentration and actor.spellcaster.concentration_spell is not None:
        logger.info(
            "%s is already concentrating on '%s'; casting '%s' will end it",
            actor.name,
            actor.spellcaster.concentration_spell,
            spell.name,
        )

    return ValidationResult(is_valid=True)


def validate_defend(action: Action, state: CombatState) -> ValidationResult:
    """Validate a defend action. Common checks + action budget."""
    common = _validate_common(action, state)
    if common is not None:
        return common
    actor = _find_combatant(action.actor_name, state)
    assert actor is not None
    if actor.action_budget.action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà utilisé son Action ce tour.",
        )
    return ValidationResult(is_valid=True)


def validate_disengage(action: Action, state: CombatState) -> ValidationResult:
    """Validate the Disengage action.

    Common checks + action budget. Disengage suppresses OOA for
    the rest of the turn — see :func:`engine.combat.disengage`.
    """
    common = _validate_common(action, state)
    if common is not None:
        return common
    actor = _find_combatant(action.actor_name, state)
    assert actor is not None
    if actor.action_budget.action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' a déjà utilisé son Action ce tour.",
        )
    return ValidationResult(is_valid=True)


def validate_flee(action: Action, state: CombatState) -> ValidationResult:
    """Validate a flee action. Common + not restrained/grappled."""
    common = _validate_common(action, state)
    if common is not None:
        return common

    actor = _find_combatant(action.actor_name, state)
    assert actor is not None

    if cannot_move(actor.conditions):
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' cannot move",
        )

    return ValidationResult(is_valid=True)


def validate_move_in_combat(action: Action, state: CombatState) -> ValidationResult:
    """Validate a Move action in combat: movement budget + cannot_move conditions.

    Adjacency check (whether the target zone is actually adjacent) is deferred
    to resolution — the validator only verifies that the combatant *can* move
    and has movement left this turn. A target zone name is required.
    """
    common = _validate_common(action, state)
    if common is not None:
        return common

    actor = _find_combatant(action.actor_name, state)
    assert actor is not None  # checked in _validate_common

    if cannot_move(actor.conditions):
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' ne peut pas se déplacer (entravé/agrippé/etc.).",
        )
    if actor.action_budget.movement_remaining_feet <= 0:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' n'a plus de mouvement ce tour.",
        )
    if action.target_name is None:
        return ValidationResult(
            is_valid=False,
            error_message="Move nécessite un nom de zone cible.",
        )
    return ValidationResult(is_valid=True)


def validate_truce_attempt(
    action: Action, state: CombatState,
) -> ValidationResult:
    """Validate a social TRUCE attempt (TALK in combat).

    TALK in combat is not a normal exploration dialogue: it's a check
    against the target NPC's ``aggression_threshold`` to end the
    encounter peacefully. This validator rules out targets that cannot
    be reasoned with **before** any dice roll, so the actor's Action
    stays unspent when the request is structurally invalid.

    Rejected cases:
    - Missing or unknown target.
    - Target is an ally (same side as actor).
    - Target has no stat block (commoner NPC — no negotiation layer).
    - Target is ``mindless`` (zombie, rage-beast, construct).

    Boss-phase-2 refusal is deferred to :func:`bot.combat_truce.attempt_truce`
    because it depends on runtime phase state — a validator run on a
    paused combat shouldn't be stricter than the resolver.
    """
    actor = _find_combatant(action.actor_name, state)
    if actor is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' n'est pas en combat.",
        )
    if action.target_name is None:
        return ValidationResult(
            is_valid=False,
            error_message="Parler en combat nécessite une cible.",
        )
    target = _find_combatant(action.target_name, state)
    if target is None:
        return ValidationResult(
            is_valid=False,
            error_message=(
                f"'{action.target_name}' n'est pas en combat."
            ),
        )
    if target.side == actor.side:
        return ValidationResult(
            is_valid=False,
            error_message="Impossible de négocier une trêve avec un allié.",
        )
    sb = target.stat_block
    if sb is None:
        return ValidationResult(
            is_valid=False,
            error_message=(
                f"{target.name} ne peut pas être raisonné "
                "(pas de profil de combat)."
            ),
        )
    if sb.mindless:
        return ValidationResult(
            is_valid=False,
            error_message=(
                f"{target.name} est trop bestial pour entendre raison."
            ),
        )
    return ValidationResult(is_valid=True)


def validate_use_item(action: Action, state: CombatState) -> ValidationResult:
    """Validate a use item action. Common + item in inventory."""
    common = _validate_common(action, state)
    if common is not None:
        return common

    actor = _find_combatant(action.actor_name, state)
    assert actor is not None

    if action.item_name is None:
        return ValidationResult(
            is_valid=False,
            error_message="Use Item requires an item name",
        )

    # Check item is in inventory (carried or equipped)
    all_items = [i.name for i in actor.inventory.items] + [
        i.name for i in actor.inventory.equipped.values()
    ]
    if action.item_name not in all_items:
        return ValidationResult(
            is_valid=False,
            error_message=f"Item '{action.item_name}' not found in inventory",
        )

    return ValidationResult(is_valid=True)


# ---------------------------------------------------------------------------
# Exploration validators — pure rule checks (entity existence is resolved
# upstream by the EntityResolver before the validator runs).
# ---------------------------------------------------------------------------


def validate_exploration_action(
    action: Action,
    combat_state: CombatState | None = None,
) -> ValidationResult:
    """Validate a non-combat action against its own rules.

    Entity resolution (does the NPC exist, is the exit reachable, etc.) is
    handled by the EntityResolver before this function is called. This
    validator only checks that the action carries the fields its type
    requires.

    If ``combat_state`` is provided and is currently active, most
    exploration actions are refused: only informational/catch-all actions
    (LOOK, QUESTION, IMPROVISE) remain permitted off-turn. Players must use
    Flee (combat path) to escape — MOVE/TALK/SEARCH/INTERACT/PICKUP are
    blocked with a clear in-game message. This is a safety net; the
    finer-grained action economy lives in the combat validators.

    Combat action types are rejected — route them through validate_action().
    """
    if action.action_type not in EXPLORATION_ACTION_TYPES:
        return ValidationResult(
            is_valid=False,
            error_message=(
                f"'{action.action_type.value}' is not an exploration action"
            ),
        )

    if combat_state is not None and combat_state.is_active:
        # TALK in combat is the social de-escalation (TRUCE) path. The
        # target must be a non-mindless, non-ally enemy with a stat block.
        # ``validate_truce_attempt`` does the full check; the pipeline
        # later dispatches to ``bot.combat_truce.attempt_truce``.
        if action.action_type == ActionType.TALK:
            return validate_truce_attempt(action, combat_state)
        if action.action_type not in _EXPLORATION_ALLOWED_IN_COMBAT:
            return ValidationResult(
                is_valid=False,
                error_message=(
                    f"Impossible de faire '{action.action_type.value}' "
                    "en plein combat. Utilisez Flee pour tenter de fuir, "
                    "ou attendez votre tour."
                ),
            )

    if action.action_type in (
        ActionType.LOOK,
        ActionType.SEARCH,
        ActionType.IMPROVISE,
        ActionType.QUESTION,
    ):
        return ValidationResult(is_valid=True)

    if action.action_type == ActionType.PICKUP:
        if action.target_name is None and action.item_name is None:
            return ValidationResult(
                is_valid=False,
                error_message="Pick Up requires an item",
            )
        return ValidationResult(is_valid=True)

    # MOVE, TALK, INTERACT all require a target_name.
    if action.target_name is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"{action.action_type.value} requires a target",
        )

    return ValidationResult(is_valid=True)
