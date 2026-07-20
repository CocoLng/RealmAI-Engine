"""Interpret stage — interpreter call + weapon resolution + validation.

Pure-function module. The validation step may set side-channel state
(MOVE→FLEE auto-conversion, combat bootstrap detection) — returned
on InterpretSideChannel.

Extracted from ``bot.action_pipeline.ActionPipeline`` so that interpret
logic can be unit-tested without instantiating the full pipeline class.

The ``ActionPipeline`` facade keeps thin wrappers for the three private
methods that round-trip side-channel state through ``InterpretSideChannel``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ai.client import LLMParseError
from ai.interpreter import Interpreter
from ai.models import InterpretedAction
from ai.scene_context import SceneContext
from bot.combat_entry import (
    CombatTrigger,
    CombatTriggerKind,
    consume_trigger_def,
    detect_combat_trigger,
    enter_combat,
)
from bot.llm_retry import retry_llm_call
from engine.combat import (
    CombatSide,
    start_combat,
)
from engine.inventory import EquipmentSlot, Inventory, Weapon
from engine.validators import (
    Action,
    ActionType,
    EXPLORATION_ACTION_TYPES,
    ValidationResult,
    validate_action,
    validate_exploration_action,
)
from world.location import Location
from world.npc import NPC

if TYPE_CHECKING:
    from engine.combat import CombatState
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)

FALLBACK_IMPROVISE_CONFIDENCE = 0.3
"""Confidence du IMPROVISE forgé après épuisement des retries interpreter.

Volontairement sous CONFIDENCE_CLARIFY_THRESHOLD (orchestrator) : le fallback
passe TOUJOURS par le gate de confirmation — le joueur valide avant que le
tour soit consommé (leçon H11 : jamais de fallback silencieux).
"""


# ---------------------------------------------------------------------------
# Side-channel
# ---------------------------------------------------------------------------


@dataclass
class InterpretSideChannel:
    """Side-channel outputs from the interpret/validate stage.

    Callers initialise this from existing ``self._pending_*`` values, pass it
    into :func:`validate`, then copy any mutations back to the instance.

    Fields populated by ``validate``:
    - ``pending_flee_destination`` — set when MOVE is auto-converted to FLEE
    - ``pending_combat_start_embed`` — set when a new combat is bootstrapped
    - ``trivial_kill_mechanics`` — set when a trivial-kill path fires
    - ``pending_dice_embeds`` — threaded through to ResolveSideChannel on
      trivial-kill and copied back
    """

    pending_flee_destination: str | None = None
    pending_combat_start_embed: Any = None  # tuple[CombatState, CombatTrigger] | None
    trivial_kill_mechanics: str | None = None
    pending_dice_embeds: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assign_initial_zones(state: CombatState, location: Location) -> None:
    """Place combatants into starting zones when combat begins.

    PCs go to the first zone; enemies go to the last zone (same as the first
    when only one zone exists). Combatants that already have a zone are left
    untouched.
    """
    zones = location.combat_zones
    if not zones:
        return
    pc_zone = zones[0].name
    npc_zone = zones[-1].name
    for c in state.combatants:
        if c.current_zone is None:
            c.current_zone = pc_zone if c.side == CombatSide.PLAYER else npc_zone


def _charge_aggressor_to_target(
    state: CombatState,
    trigger: CombatTrigger,
    eng_action: Action,
) -> None:
    """Close the distance for the combat-triggering attack (audit H18).

    Default zone assignment puts PCs and enemies at opposite ends of the
    location, which guaranteed an "hors de portée" refusal for every
    melee attack that *starts* a combat. The triggering attack IS the
    charge: when the aggressor's weapon cannot reach the target from the
    starting zones, move the aggressor into the target's zone. Ranged
    and thrown attackers stay put.
    """
    from engine.validators import _check_range

    aggressor = next(
        (c for c in state.combatants if c.name == trigger.aggressor_name),
        None,
    )
    target = next(
        (c for c in state.combatants if c.name == eng_action.target_name),
        None,
    )
    if aggressor is None or target is None:
        return
    if aggressor.current_zone is None or target.current_zone is None:
        return  # zoneless combat — everyone already in range

    weapon: Weapon | None = None
    for slot in (EquipmentSlot.MAIN_HAND, EquipmentSlot.OFF_HAND):
        item = aggressor.inventory.equipped.get(slot)
        if (
            item is not None
            and isinstance(item, Weapon)
            and item.name == eng_action.weapon_name
        ):
            weapon = item
            break

    if not _check_range(aggressor, target, weapon):
        aggressor.current_zone = target.current_zone


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


async def call_interpreter(
    *,
    interpreter: Interpreter,
    player_text: str,
    scene: SceneContext,
    actor_name: str,
    language: str,
) -> InterpretedAction:
    """Call the Interpreter LLM and return its structured result.

    Moved verbatim from ``action_pipeline.py:_call_interpreter``.
    Retries are handled by :func:`bot.llm_retry.retry_llm_call`. When every
    retry fails on ``LLMParseError`` (sortie 4b inexploitable), un IMPROVISE
    de secours est forgé avec ``FALLBACK_IMPROVISE_CONFIDENCE`` — le gate de
    confiance de l'orchestrator le soumet alors à confirmation du joueur.
    ``OllamaUnavailableError`` propage toujours : serveur down = vraie panne.
    """
    campaign_id: str = getattr(interpreter, "campaign_id", "?")

    def _do() -> InterpretedAction:
        return interpreter.interpret(
            player_text=player_text,
            actor_name=actor_name,
            scene_context=scene,
            language=language,
        )

    try:
        return await retry_llm_call(
            _do,
            log_label=f"ACTION campaign={campaign_id} interpret",
        )
    except LLMParseError:
        logger.warning(
            "ACTION campaign=%s interpret fallback→IMPROVISE raw=%r",
            campaign_id, player_text[:100],
        )
        return InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=actor_name,
            improvise_description=player_text,
            raw_input=player_text,
            confidence=FALLBACK_IMPROVISE_CONFIDENCE,
        )


def auto_resolve_weapon_name(
    *,
    weapon_name: str | None,
    inventory: Inventory | None,
) -> str | None:
    """Return the canonical equipped weapon name, resolving player aliases.

    Moved verbatim from ``action_pipeline.py:_auto_resolve_weapon_name``.

    When weapon_name is None → return MAIN_HAND weapon as before.
    When weapon_name is given → try case-insensitive exact match first;
    if no match and only one weapon is equipped, assume the player meant
    that weapon (handles aliases like "épée", "sword", "mon arme").
    Falls back to MAIN_HAND when multiple weapons are equipped and none match.
    """
    if inventory is None:
        return None

    equipped_weapons: list[Weapon] = [
        item
        for slot in (EquipmentSlot.MAIN_HAND, EquipmentSlot.OFF_HAND)
        if (item := inventory.equipped.get(slot)) is not None
        and isinstance(item, Weapon)
    ]

    if weapon_name is None:
        main = inventory.equipped.get(EquipmentSlot.MAIN_HAND)
        if main is not None and isinstance(main, Weapon):
            return main.name
        return equipped_weapons[0].name if equipped_weapons else None

    # Case-insensitive exact match against equipped weapons.
    for w in equipped_weapons:
        if w.name.lower() == weapon_name.lower():
            return w.name

    # No match — if exactly one weapon is equipped, assume the player meant it.
    if len(equipped_weapons) == 1:
        return equipped_weapons[0].name

    # Ambiguous or no weapon equipped — fall back to MAIN_HAND.
    main = inventory.equipped.get(EquipmentSlot.MAIN_HAND)
    return main.name if main is not None and isinstance(main, Weapon) else None


def validate(
    *,
    action: InterpretedAction,
    actor_name: str,
    location: Location | None,
    npcs: dict[str, NPC],
    combat_state: CombatState | None,
    inventory: Inventory | None,
    session: GameSession | None,
    campaign_id: str,
    db_factory: Callable[[], Any] | None,
    side: InterpretSideChannel,
) -> ValidationResult:
    """Convert InterpretedAction → Action and dispatch to the right validator.

    Moved verbatim from ``action_pipeline.py:_validate``.

    Dispatch logic (in order):
    1. If combat active AND action is MOVE → auto-convert to FLEE, store destination.
    2. If no combat → try detect_combat_trigger; bootstrap if trigger found.
    3. If combat active → combat validators (validate_action or validate_exploration_action).
    4. If no combat → exploration validators, or trivial-kill check, or error.

    Side-channel writes:
      - MOVE→FLEE conversion: ``side.pending_flee_destination = destination``
      - Combat bootstrap:
        ``side.pending_combat_start_embed = (new_combat_state, trigger)``
      - Trivial kill: ``side.trivial_kill_mechanics``, ``side.pending_dice_embeds``
    """
    eng_action = Action(
        actor_name=action.actor_name,
        action_type=action.action_type,
        target_name=action.target_name,
        weapon_name=action.weapon_name,
        spell_name=action.spell_name,
        item_name=action.item_name,
    )

    # --- 1. Auto-convert MOVE → FLEE in active combat ---
    if (
        eng_action.action_type == ActionType.MOVE
        and combat_state is not None
        and combat_state.is_active
    ):
        logger.info(
            "MOVE auto-converted to FLEE campaign=%s actor=%s destination=%s",
            campaign_id, action.actor_name, eng_action.target_name,
        )
        side.pending_flee_destination = eng_action.target_name
        eng_action = eng_action.model_copy(
            update={"action_type": ActionType.FLEE, "target_name": None},
        )
        # Fall through to combat dispatch below

    # --- 2. If no combat, try to detect a trigger and bootstrap ---
    if combat_state is None or not combat_state.is_active:
        trigger: CombatTrigger | None = None
        if session is not None:
            trigger = detect_combat_trigger(action, session)

        if trigger is not None:
            # Build the PROSPECTIVE CombatState first (initiative,
            # surprise, zones, charge) and validate the triggering action
            # against it BEFORE committing it to the session (audit H18).
            previous_state = session.combat_state  # type: ignore[union-attr]
            try:
                pre_state = enter_combat(session, trigger)  # type: ignore[arg-type]
            except ValueError as exc:
                logger.warning("Combat bootstrap failed: %s", exc)
                return ValidationResult(is_valid=False, error_message=str(exc))
            new_state = start_combat(pre_state.combatants, trigger=trigger)
            if location is not None and location.has_combat_zones():
                _assign_initial_zones(new_state, location)
                _charge_aggressor_to_target(new_state, trigger, eng_action)

            # Probe the triggering action when the aggressor acts first
            # and the action is a combat action: a refused attack must
            # not leave an unwanted combat (and a burned surprise round)
            # on the session. Two cases skip the probe and always commit:
            # the enemy legitimately won initiative (BOTH_READY face-off
            # — the attack is merely deferred to the PC's turn), and
            # exploration-typed triggers (trap INTERACT, lethal
            # IMPROVISE — the trap is sprung / the intent declared).
            current = new_state.combatants[new_state.current_turn_index]
            if (
                current.name == eng_action.actor_name
                and eng_action.action_type not in EXPLORATION_ACTION_TYPES
            ):
                probe = validate_action(eng_action, new_state)
                if not probe.is_valid:
                    session.combat_state = previous_state  # type: ignore[union-attr]
                    logger.info(
                        "COMBAT bootstrap rolled back kind=%s campaign=%s "
                        "aggressor=%s reason=%s",
                        trigger.kind, campaign_id,
                        trigger.aggressor_name, probe.error_message,
                    )
                    return probe

            logger.info(
                "COMBAT bootstrapped kind=%s campaign=%s aggressor=%s enemies=%s",
                trigger.kind, campaign_id,
                trigger.aggressor_name, trigger.enemy_names,
            )
            combat_state = new_state
            session.combat_state = combat_state  # type: ignore[union-attr]
            side.pending_combat_start_embed = (combat_state, trigger)
            if trigger.kind == CombatTriggerKind.AMBUSH:
                # The mechanism fired — flag it so the same trigger can't
                # spawn the ambush twice (persists with the location).
                consume_trigger_def(location, eng_action.target_name)
            # Fall through to combat dispatch below

    # --- 3. Dispatch to the right validator ---
    if combat_state is not None and combat_state.is_active:
        if eng_action.action_type in EXPLORATION_ACTION_TYPES:
            return validate_exploration_action(
                eng_action, combat_state=combat_state,
            )
        return validate_action(eng_action, combat_state)

    # --- 4. No combat — exploration path or trivial kill ---
    if eng_action.action_type in EXPLORATION_ACTION_TYPES:
        return validate_exploration_action(eng_action, combat_state=None)

    # Combat action requested with no active combat → check trivial kill
    if (
        eng_action.action_type == ActionType.ATTACK
        and eng_action.target_name is not None
        and npcs.get(eng_action.target_name) is not None
    ):
        target_npc = npcs[eng_action.target_name]
        from bot.pipeline import resolve as _resolve_mod
        if _resolve_mod.should_trivial_resolve(
            npc=target_npc, session=session, campaign_id=campaign_id,
        ):
            resolve_side = _resolve_mod.ResolveSideChannel(
                pending_flee_destination=side.pending_flee_destination,
                pending_dice_embeds=list(side.pending_dice_embeds),
                trivial_kill_mechanics=side.trivial_kill_mechanics,
            )
            _resolve_mod.trivial_kill(
                target_npc=target_npc,
                actor_name=actor_name,
                location=location,
                npcs=npcs,
                session=session,
                campaign_id=campaign_id,
                db_factory=db_factory,
                side=resolve_side,
            )
            side.trivial_kill_mechanics = resolve_side.trivial_kill_mechanics
            side.pending_flee_destination = resolve_side.pending_flee_destination
            side.pending_dice_embeds = resolve_side.pending_dice_embeds
            return ValidationResult(is_valid=True)

    return ValidationResult(
        is_valid=False,
        error_message=(
            f"'{eng_action.action_type.value}' nécessite un combat actif."
        ),
    )
