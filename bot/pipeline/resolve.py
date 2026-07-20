"""Resolve stage — engine mechanics dispatch + combat helpers.

Pure-function module. Stage helpers return MechanicsOutcome and may
mutate the ResolveSideChannel they receive.

Extracted from ``bot.action_pipeline.ActionPipeline`` so that mechanics
dispatch can be unit-tested without instantiating the full pipeline class.

The ``ActionPipeline`` facade keeps a thin wrapper for ``_resolve_mechanics``
that round-trips side-channel state through ``ResolveSideChannel``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai.models import InterpretedAction, MechanicsOutcome, PublicEffects
from engine.character import (
    Character,
    SKILL_ABILITY,
    compute_modifier,
    compute_skill_modifier,
)
from engine.combat import (
    CombatEndReason,
    check_combat_end,
    trivial_resolve,
)
from engine.conditions import ActiveCondition, ConditionType, apply_condition
from engine.dice import RollOutcome, roll_check
from engine.inventory import EquipmentSlot, Weapon, equip_item, remove_item, unequip_item
from engine.skill_check import (
    compute_skill_check_dc,
    infer_skill_from_text,
)
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC, NPCDisposition

if TYPE_CHECKING:
    from engine.combat import Combatant, CombatState
    from engine.inventory import Inventory
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (moved from action_pipeline.py; re-exported there for compat)
# ---------------------------------------------------------------------------

TRIVIAL_RESOLVE_HP_THRESHOLD = 10
"""NPCs with ``max_hp`` below this value are auto-resolved on attack."""

TRIVIAL_RESOLVE_AC_THRESHOLD = 12
"""NPCs with ``ac`` above this value are *not* trivially defeatable."""

DEFENSIVE_CONDITIONS: frozenset[ConditionType] = frozenset({
    ConditionType.INVISIBLE,
    ConditionType.PETRIFIED,
    ConditionType.RESTRAINED,
    ConditionType.UNCONSCIOUS,
})
"""Conditions that make an NPC non-trivial to defeat outright."""


# ---------------------------------------------------------------------------
# Side-channel state
# ---------------------------------------------------------------------------

@dataclass
class ResolveSideChannel:
    """Mutable bag for state produced as a side effect of resolve helpers.

    The ``ActionPipeline`` facade builds one of these from the current
    ``self._pending_*`` attributes before calling :func:`resolve_mechanics`,
    and copies the updated values back afterward.
    """

    pending_flee_destination: str | None = None
    pending_dice_embeds: list[Any] = field(default_factory=list)
    trivial_kill_mechanics: str | None = None
    trivial_kill_target: str | None = None


# ---------------------------------------------------------------------------
# Module-level helper (moved from action_pipeline.py)
# ---------------------------------------------------------------------------

def is_trivially_defeatable(npc: NPC) -> bool:
    """Check whether an NPC can be auto-killed without a combat round.

    All three criteria must be met:
    - ``npc.max_hp`` is below :data:`TRIVIAL_RESOLVE_HP_THRESHOLD`
    - ``npc.ac`` is at or below :data:`TRIVIAL_RESOLVE_AC_THRESHOLD`
    - NPC has no active defensive conditions (forward-compatible; NPCs
      don't carry conditions today, but the check is ready for when they do)
    """
    if npc.max_hp >= TRIVIAL_RESOLVE_HP_THRESHOLD:
        return False
    if npc.ac > TRIVIAL_RESOLVE_AC_THRESHOLD:
        return False
    # NPC model does not have conditions yet; use getattr for
    # forward-compatibility.
    conditions: list[ActiveCondition] = getattr(npc, "conditions", [])
    if any(c.condition_type in DEFENSIVE_CONDITIONS for c in conditions):
        return False
    return True


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

async def resolve_mechanics(
    *,
    action: InterpretedAction,
    actor_name: str,
    location: Location | None,
    npcs: dict[str, NPC],
    combat_state: "CombatState | None",
    inventory: "Inventory | None",
    session: "GameSession | None",
    campaign_id: str,
    db_factory: Any,
    side: ResolveSideChannel,
) -> MechanicsOutcome:
    """Dispatcher — calls action-type-specific resolver.

    Apply mechanical effects and return a layered outcome.

    Returns a :class:`ai.models.MechanicsOutcome` carrying the short
    mechanical summary, the player's framing, and any state-change
    facts. The narrator consumes the three layers separately.
    """
    from bot.pipeline.narrate import build_player_intent
    intent = build_player_intent(action)

    if side.trivial_kill_mechanics is not None:
        return MechanicsOutcome(
            summary=side.trivial_kill_mechanics,
            player_intent=intent,
            outcome_facts=side.trivial_kill_mechanics,
            target_defeated=side.trivial_kill_target,
        )

    at = action.action_type

    if at == ActionType.EQUIP:
        return resolve_equip(
            action=action,
            actor_name=actor_name,
            combat_state=combat_state,
            inventory=inventory,
            side=side,
        )

    if at == ActionType.USE_ITEM:
        return resolve_use_item(
            action=action,
            actor_name=actor_name,
            combat_state=combat_state,
            inventory=inventory,
            side=side,
        )

    if at == ActionType.FLEE:
        return await resolve_flee(
            action=action,
            actor_name=actor_name,
            location=location,
            combat_state=combat_state,
            session=session,
            db_factory=db_factory,
            side=side,
        )

    if at == ActionType.LOOK:
        loc = location
        summary = (
            f"{action.actor_name} observes {loc.name if loc else 'the area'}."
        )
        return MechanicsOutcome(summary=summary, player_intent=intent)

    if at == ActionType.QUESTION:
        loc = location
        parts: list[str] = []
        if loc:
            parts.append(f"Location: {loc.name}. {loc.description}")
            all_exits = loc.connections + loc.unlocked_exits
            if all_exits:
                parts.append(f"Exits: {', '.join(all_exits)}.")
            if loc.items_available:
                parts.append(f"Visible items: {', '.join(loc.items_available)}.")
            if loc.npcs_present:
                parts.append(f"NPCs present: {', '.join(loc.npcs_present)}.")
            if loc.state_flags:
                active = [k for k, v in loc.state_flags.items() if v]
                if active:
                    parts.append(f"Environment state: {', '.join(active)}.")
        arc = getattr(session, "story_arc", None) if session else None
        if arc is not None:
            beat = arc.beats[arc.current_beat_index]
            parts.append(f"Current objective: {beat.title} — {beat.description}")
        summary = f"{action.actor_name} asks about the surroundings."
        return MechanicsOutcome(
            summary=summary,
            player_intent=intent,
            outcome_facts=" ".join(parts),
        )

    if at == ActionType.SEARCH:
        summary = (
            f"{action.actor_name} searches "
            f"{action.target_name or 'the surroundings'}."
        )
        return MechanicsOutcome(summary=summary, player_intent=intent)

    if at == ActionType.TALK:
        # TALK in combat is the TRUCE path (CHA check vs
        # aggression_threshold). Out of combat, it's the usual NPC
        # dialogue flow.
        _language = getattr(session, "language", "fr") if session is not None else "fr"
        if combat_state is not None and combat_state.is_active:
            return await asyncio.to_thread(
                resolve_talk_in_combat,
                action=action,
                actor_name=actor_name,
                combat_state=combat_state,
                session=session,
                side=side,
            )
        return await asyncio.to_thread(
            resolve_talk,
            action=action,
            actor_name=actor_name,
            location=location,
            npcs=npcs,
            session=session,
            campaign_id=campaign_id,
            db_factory=db_factory,
            side=side,
            language=_language,
        )

    if at == ActionType.MOVE:
        target = action.target_name or ""
        if (
            session is not None
            and db_factory is not None
            and target
        ):
            from bot.world_navigation import LocationChangeError, change_location
            try:
                dest = await change_location(
                    session, target, db_factory=db_factory,
                )
            except LocationChangeError as exc:
                logger.warning(
                    "MOVE change_location failed campaign=%s target=%r reason=%s",
                    campaign_id, target, exc.reason,
                )
                return MechanicsOutcome(
                    summary=f"{action.actor_name} cannot reach {exc.destination}.",
                    player_intent=intent,
                )
            # Caller must sync self.location / self.npcs from session after return.
            return MechanicsOutcome(
                summary=f"{action.actor_name} arrives at {dest.name}.",
                player_intent=intent,
                outcome_facts=f"{action.actor_name} moved to {dest.name}.",
                public_effects=PublicEffects(location_change=dest.name),
            )
        return MechanicsOutcome(
            summary=f"{action.actor_name} moves toward {action.target_name}.",
            player_intent=intent,
        )

    if at == ActionType.INTERACT:
        return MechanicsOutcome(
            summary=f"{action.actor_name} interacts with {action.target_name}.",
            player_intent=intent,
        )

    if at == ActionType.PICKUP:
        summary = await asyncio.to_thread(
            resolve_pickup,
            action=action,
            actor_name=actor_name,
            session=session,
            db_factory=db_factory,
        )
        facts = ""
        public = PublicEffects()
        if "picks up" in summary:
            facts = summary
            picked_name = action.target_name or action.item_name or ""
            if picked_name:
                public = PublicEffects(items_gained=[picked_name])
        return MechanicsOutcome(
            summary=summary,
            player_intent=intent,
            outcome_facts=facts,
            public_effects=public,
        )

    if at == ActionType.IMPROVISE:
        return resolve_improvise(
            action=action,
            actor_name=actor_name,
            npcs=npcs,
            session=session,
            side=side,
        )

    if at == ActionType.ATTACK:
        return resolve_pc_attack(
            action=action,
            actor_name=actor_name,
            combat_state=combat_state,
            side=side,
        )

    if at == ActionType.CAST_SPELL:
        return resolve_cast_spell(
            action=action,
            actor_name=actor_name,
            combat_state=combat_state,
            side=side,
        )

    if at == ActionType.DEFEND:
        return resolve_defend(
            action=action,
            actor_name=actor_name,
            combat_state=combat_state,
            side=side,
        )

    if at == ActionType.DISENGAGE:
        return resolve_disengage(
            action=action,
            actor_name=actor_name,
            combat_state=combat_state,
            side=side,
        )

    return MechanicsOutcome(
        summary=f"{action.actor_name} performs {at.value}.",
        player_intent=intent,
    )


# ---------------------------------------------------------------------------
# Per-action-type resolvers
# ---------------------------------------------------------------------------

def resolve_equip(
    *,
    action: InterpretedAction,
    actor_name: str,
    combat_state: "CombatState | None",
    inventory: "Inventory | None",
    side: ResolveSideChannel,
) -> MechanicsOutcome:
    """Swap equipped weapon — free action, no turn advance."""
    from bot.pipeline.narrate import build_player_intent
    intent = build_player_intent(action)
    if combat_state is None or action.item_name is None:
        return MechanicsOutcome(summary="Equip failed.", player_intent=intent)

    actor = next(
        (c for c in combat_state.combatants if c.name == action.actor_name),
        None,
    )
    if actor is None:
        return MechanicsOutcome(summary="Equip failed.", player_intent=intent)

    inv = actor.inventory

    # Unequip current MAIN_HAND if occupied
    if EquipmentSlot.MAIN_HAND in inv.equipped:
        unequip_item(inv, EquipmentSlot.MAIN_HAND)

    # Equip the new weapon
    equip_item(inv, action.item_name, EquipmentSlot.MAIN_HAND)
    actor.action_budget.weapon_swapped_this_turn = True

    return MechanicsOutcome(
        summary=f"{action.actor_name} dégaine {action.item_name}.",
        player_intent=intent,
    )


def resolve_use_item(
    *,
    action: InterpretedAction,
    actor_name: str,
    combat_state: "CombatState | None",
    inventory: "Inventory | None",
    side: ResolveSideChannel,
) -> MechanicsOutcome:
    """Use a healing potion — costs the action."""
    from bot.pipeline.narrate import build_player_intent
    intent = build_player_intent(action)
    if combat_state is None or action.item_name is None:
        return MechanicsOutcome(summary="Use item failed.", player_intent=intent)

    actor = next(
        (c for c in combat_state.combatants if c.name == action.actor_name),
        None,
    )
    if actor is None:
        return MechanicsOutcome(summary="Use item failed.", player_intent=intent)

    # Find the potion
    matching = [i for i in actor.inventory.items if i.name == action.item_name]
    if not matching:
        return MechanicsOutcome(
            summary=f"{action.item_name} not found.", player_intent=intent,
        )

    item = matching[0]
    heal_dice = getattr(item, "heal_dice", None)
    if not heal_dice:
        return MechanicsOutcome(
            summary=f"{action.actor_name} uses {action.item_name}.",
            player_intent=intent,
        )

    # Roll healing dice
    from engine.dice import roll as roll_dice

    dice_result = roll_dice(heal_dice)
    healed = dice_result.total
    old_hp = actor.character.hp
    actor.character.hp = min(old_hp + healed, actor.character.max_hp)
    actual_healed = actor.character.hp - old_hp

    # Remove potion from inventory
    remove_item(actor.inventory, action.item_name)

    # Mark action used
    actor.action_budget.action_used = True

    summary = (
        f"{action.actor_name} boit {action.item_name} "
        f"— récupère {actual_healed} PV ({dice_result.expression}: {dice_result.total})"
    )
    return MechanicsOutcome(
        summary=summary,
        player_intent=intent,
        outcome_facts=summary,
        public_effects=PublicEffects(
            hp_delta={action.actor_name: actual_healed},
        ),
    )


async def resolve_flee(
    *,
    action: InterpretedAction,
    actor_name: str,
    location: Location | None,
    combat_state: "CombatState | None",
    session: "GameSession | None",
    db_factory: Any,
    side: ResolveSideChannel,
) -> MechanicsOutcome:
    """Roll DEX check (Acrobatics) DC 12 to escape combat.

    Success: combatant marked fled=True, removed from turn rotation.
    Failure: action_used=True, combatant stays in combat.
    When all alive PCs have fled, ends combat with CombatEndReason.FLED
    and applies the stored flee destination (from MOVE auto-conversion).
    """
    from bot.pipeline.narrate import build_player_intent
    assert combat_state is not None
    combatant = next(
        (c for c in combat_state.combatants if c.name == action.actor_name),
        None,
    )
    if combatant is None:
        return MechanicsOutcome(
            summary=f"{action.actor_name} n'est pas en combat.",
            player_intent=build_player_intent(action),
        )

    dex_score = combatant.character.ability_scores.DEX
    dex_mod = compute_modifier(dex_score)
    expression = f"1d20+{dex_mod}" if dex_mod >= 0 else f"1d20{dex_mod}"
    check = roll_check(expression, dc=12)
    intent = build_player_intent(action)

    if check.outcome in (
        RollOutcome.NEAR_SUCCESS,
        RollOutcome.SUCCESS,
        RollOutcome.CRITICAL_SUCCESS,
    ):
        combatant.fled = True
        outcome_desc = (
            f"{action.actor_name} réussit à fuir "
            f"(DEX {check.total} vs DC 12) et s'échappe du combat."
        )
    else:
        combatant.action_budget.action_used = True
        outcome_desc = (
            f"{action.actor_name} échoue à fuir "
            f"(DEX {check.total} vs DC 12) et reste bloqué en combat."
        )

    # Store dice roll for the caller to display as an embed.
    side.pending_dice_embeds.append(("flee_check", check, action.actor_name))

    # Check if combat ends (all alive PCs have fled)
    end = check_combat_end(combat_state)
    if end == CombatEndReason.FLED:
        # Centralised finalisation. Local import to avoid the
        # bot.combat_end → ActionPipeline import cycle.
        if session is not None:
            from bot.combat_end import finalize_combat
            finalize_combat(session, CombatEndReason.FLED)
        else:
            # Session-less pipeline (shouldn't happen in live flow but
            # some tests build one): fall back to a minimal state flip.
            combat_state.is_active = False
            combat_state.end_reason = end
        destination_name: str | None = None
        if side.pending_flee_destination and session and db_factory:
            from bot.world_navigation import LocationChangeError, change_location
            try:
                dest = await change_location(
                    session,
                    side.pending_flee_destination,
                    db_factory=db_factory,
                )
                destination_name = dest.name
                outcome_desc += f" Le groupe s'échappe vers {dest.name}."
            except LocationChangeError:
                pass
        return MechanicsOutcome(
            summary=outcome_desc,
            player_intent=intent,
            outcome_facts=outcome_desc,
            public_effects=PublicEffects(location_change=destination_name)
            if destination_name
            else PublicEffects(),
        )

    return MechanicsOutcome(
        summary=outcome_desc,
        player_intent=intent,
        outcome_facts=outcome_desc,
    )


def resolve_talk(
    *,
    action: InterpretedAction,
    actor_name: str,
    location: Location | None,
    npcs: dict[str, NPC],
    session: "GameSession | None",
    campaign_id: str,
    db_factory: Any,
    side: ResolveSideChannel,
    language: str = "fr",
) -> MechanicsOutcome:
    """Run TALK through the NPC agent, persist state, build outcome."""
    from world.npc import DialogueExchange, NPCDisposition
    from bot.pipeline.narrate import build_player_intent

    intent = build_player_intent(action)
    target = action.target_name or ""

    if (
        session is None
        or not target
        or target not in (session.npcs or {})
    ):
        return MechanicsOutcome(
            summary=f"{action.actor_name} approaches {target} to speak.",
            player_intent=intent,
        )

    npc = session.npcs[target]
    agent = getattr(session, "npc_agent", None)
    generator = getattr(session, "npc_generator", None)

    # Lazy canon generation when the NPC sheet is empty.
    if (
        generator is not None
        and callable(getattr(generator, "generate", None))
        and not (npc.personality or npc.description)
    ):
        try:
            from engine.npc_archetypes import draw_archetypes

            location_ctx = ""
            if session.current_location is not None:
                loc = session.current_location
                location_ctx = f"{loc.name} — {loc.description}"
            campaign_theme = getattr(session.campaign, "name", "")
            # Rare race path (the prefetch normally wins): a single random
            # draw, no per-location dedup — spec npc-archetypes §1.3.
            sheet = generator.generate(
                npc_name=npc.name,
                location_context=location_ctx,
                campaign_theme=campaign_theme,
                language=language,
                archetype=draw_archetypes(1)[0],
                campaign_id=str(getattr(session.campaign, "id", "")),
            )
            npc.personality = sheet.personality
            npc.description = sheet.description
            npc.secrets = list(sheet.secrets)
            npc.knowledge = list(sheet.knowledge)
            logger.info(
                "NPC lazy-generated name=%s secrets=%d knowledge=%d",
                npc.name, len(npc.secrets), len(npc.knowledge),
            )
        except Exception:
            logger.exception(
                "NPC sheet generation failed for %s", npc.name,
            )

    if agent is None or not callable(getattr(agent, "respond", None)):
        return MechanicsOutcome(
            summary=f"{action.actor_name} speaks with {npc.name}.",
            player_intent=intent,
        )

    # Build a small scene context for the dialogue agent.
    try:
        from bot.scene_hydration import describe_scene_for_narrator
        agent_context = describe_scene_for_narrator(
            session, actor_name=action.actor_name,
        )
    except Exception:
        agent_context = ""

    try:
        response = agent.respond(
            npc=npc,
            player_input=action.raw_input,
            context_prompt=agent_context,
            language=language,
        )
    except Exception:
        logger.exception("NPC agent failed for %s", npc.name)
        return MechanicsOutcome(
            summary=f"{action.actor_name} speaks with {npc.name}.",
            player_intent=intent,
        )

    # Apply disposition delta (clamped to NPCDisposition order).
    if response.disposition_change:
        order = [
            NPCDisposition.HOSTILE, NPCDisposition.UNFRIENDLY,
            NPCDisposition.NEUTRAL, NPCDisposition.FRIENDLY,
            NPCDisposition.ALLIED,
        ]
        try:
            idx = order.index(npc.disposition) + response.disposition_change
            idx = max(0, min(len(order) - 1, idx))
            npc.disposition = order[idx]
        except ValueError:
            pass

    # Append the exchange to history.
    npc.dialogue_history.append(
        DialogueExchange(
            player_said=action.raw_input,
            npc_said=response.dialogue,
            revealed=list(response.revealed_info),
        ),
    )

    # Persist the mutated NPC.
    if db_factory is not None:
        try:
            from db.repositories.npc_repo import NPCRepository
            db_session = db_factory()
            try:
                NPCRepository(db_session).update(npc, campaign_id)
                db_session.commit()
            finally:
                db_session.close()
        except Exception:
            logger.exception("NPC persist failed for %s", npc.name)

    # Build the outcome facts the narrator will render.
    # NPC dialogue is passed separately so the narrator only describes
    # framing (body language, atmosphere) — the spoken words appear in
    # a dedicated embed field on Discord.
    facts_lines = [f"{npc.name} responds to the player."]
    if response.revealed_info:
        facts_lines.append(
            "Reveals: " + " ; ".join(response.revealed_info),
        )
    if response.disposition_change:
        facts_lines.append(
            f"Disposition shift: {response.disposition_change:+d}",
        )

    summary = f"{action.actor_name} speaks with {npc.name}."

    return MechanicsOutcome(
        summary=summary,
        player_intent=intent,
        outcome_facts="\n".join(facts_lines),
        npc_name=npc.name,
        npc_dialogue=response.dialogue,
        talk_reveals_count=len(response.revealed_info),
        talk_disposition_change=int(response.disposition_change),
    )


def resolve_talk_in_combat(
    *,
    action: InterpretedAction,
    actor_name: str,
    combat_state: "CombatState | None",
    session: "GameSession | None",
    side: ResolveSideChannel,
) -> MechanicsOutcome:
    """Route a TALK action in combat to the TRUCE resolver.

    Runs :func:`bot.combat_truce.attempt_truce` which rolls the CHA
    check and, on success, marks every enemy as fled. The result is
    then forwarded to :func:`bot.combat_end.finalize_combat` with
    ``CombatEndReason.TRUCE`` so the encounter closes cleanly and the
    TurnManager picks up an idempotent summary next tick.

    The dice embed is queued on ``side.pending_dice_embeds`` so the
    caller (ActionHandlerCog / TurnManager) can render the check in
    the existing dice embed infrastructure.
    """
    from bot.combat_truce import attempt_truce
    from engine.combat import CombatEndReason
    from engine.validators import _find_combatant
    from bot.pipeline.narrate import build_player_intent

    intent = build_player_intent(action)
    assert combat_state is not None

    actor = _find_combatant(action.actor_name, combat_state)
    target = _find_combatant(
        action.target_name or "", combat_state,
    )
    if actor is None or target is None:
        return MechanicsOutcome(
            summary=(
                f"{action.actor_name} tente de parler, mais la cible "
                "est introuvable."
            ),
            player_intent=intent,
        )

    succeeded, check, summary_text = attempt_truce(
        actor, target, combat_state,
    )

    if check is not None:
        # Queue the dice embed for the caller (task 60 rendering).
        side.pending_dice_embeds.append(
            ("truce_check", check, action.actor_name),
        )

    if succeeded and session is not None:
        # Finalise combat with TRUCE. finalize_combat is idempotent
        # so the TurnManager's post-advance_turn re-call is a no-op.
        from bot.combat_end import finalize_combat
        finalize_combat(session, CombatEndReason.TRUCE)

    return MechanicsOutcome(
        summary=summary_text,
        player_intent=intent,
        outcome_facts=summary_text,
    )


def resolve_pc_attack(
    *,
    action: InterpretedAction,
    actor_name: str,
    combat_state: "CombatState | None",
    side: ResolveSideChannel,
) -> MechanicsOutcome:
    """Resolve a player weapon attack in combat.

    Calls engine.combat.resolve_attack() which mutates defender HP in-place.
    Queues the AttackResult on side.pending_dice_embeds for the turn manager to
    render as a dice embed. Populates hp_delta in PublicEffects.
    """
    from engine.combat import consume_action, resolve_attack
    from engine.inventory import EquipmentSlot, Weapon
    from bot.pipeline.narrate import build_player_intent

    intent = build_player_intent(action)
    state = combat_state

    if state is None:
        return MechanicsOutcome(
            summary=f"{action.actor_name} performs Attack.",
            player_intent=intent,
        )

    attacker = next(
        (c for c in state.combatants if c.name == action.actor_name and c.is_alive),
        None,
    )
    target = next(
        (c for c in state.combatants if c.name == action.target_name and c.is_alive),
        None,
    )
    if attacker is None or target is None:
        return MechanicsOutcome(
            summary=f"{action.actor_name} performs Attack.",
            player_intent=intent,
        )

    weapon: Weapon | None = None
    for slot in (EquipmentSlot.MAIN_HAND, EquipmentSlot.OFF_HAND):
        item = attacker.inventory.equipped.get(slot)
        if item is not None and isinstance(item, Weapon) and item.name == action.weapon_name:
            weapon = item
            break

    if weapon is None:
        return MechanicsOutcome(
            summary=f"{action.actor_name} performs Attack.",
            player_intent=intent,
        )

    consume_action(attacker)
    result = resolve_attack(attacker, target, weapon)  # mutates target HP in-place

    side.pending_dice_embeds.append(("attack_roll", result, action.actor_name))

    if result.hit:
        summary = (
            f"{action.actor_name} touche {target.name} avec {weapon.name}"
            f" — {result.damage} dégâts"
        )
        facts = (
            f"{target.name} subit {result.damage} dégâts ({result.damage_type.value})."
            + (f" {target.name} est vaincu." if not target.is_alive else "")
        )
        public = PublicEffects(hp_delta={target.name: -result.damage})
    else:
        summary = f"{action.actor_name} rate {target.name} avec {weapon.name}"
        facts = ""
        public = PublicEffects()

    return MechanicsOutcome(
        summary=summary,
        player_intent=intent,
        outcome_facts=facts,
        public_effects=public,
        target_defeated=target.name if result.hit and not target.is_alive else None,
    )


def _find_live_combatant(
    name: str | None,
    combat_state: "CombatState",
) -> "Combatant | None":
    """Return the alive combatant whose name matches ``name``, if any."""
    if not name:
        return None
    return next(
        (
            c for c in combat_state.combatants
            if c.name == name and c.is_alive
        ),
        None,
    )


def resolve_cast_spell(
    *,
    action: InterpretedAction,
    actor_name: str,
    combat_state: "CombatState | None",
    side: ResolveSideChannel,
) -> MechanicsOutcome:
    """Resolve a spell cast through :func:`engine.combat.resolve_spell`.

    Consumes the spell slot and the right action-economy slot (Action /
    Bonus Action / Reaction per the spell's casting time), applies
    damage/healing/conditions in place, queues a ``("spell_cast",
    SpellCastResult, caster)`` dice embed, and reports HP deltas through
    ``PublicEffects``. The validator (``validate_cast_spell``) ran before
    this — unknown spells or missing casters fall back to a harmless
    no-op outcome instead of crashing.
    """
    from engine.combat import resolve_spell
    from engine.spells import SPELL_CATALOG, CastingTime
    from bot.pipeline.narrate import build_player_intent

    intent = build_player_intent(action)
    fallback = MechanicsOutcome(
        summary=f"{action.actor_name} performs Cast Spell.",
        player_intent=intent,
    )
    if combat_state is None or not action.spell_name:
        return fallback

    caster = _find_live_combatant(action.actor_name, combat_state)
    spell = SPELL_CATALOG.get(action.spell_name)
    if caster is None or caster.spellcaster is None or spell is None:
        return fallback

    target = None
    if action.target_name:
        target = _find_live_combatant(action.target_name, combat_state)
        if target is None:
            return MechanicsOutcome(
                summary=(
                    f"{action.actor_name} lance {spell.name}, mais la cible "
                    f"'{action.target_name}' est introuvable."
                ),
                player_intent=intent,
            )

    caster_hp_before = caster.character.hp
    target_hp_before = target.character.hp if target is not None else 0

    try:
        result = resolve_spell(caster, spell, target=target)
    except ValueError as exc:
        # Slot exhausted / spell unknown to the caster — the validator
        # should have caught this; report without mutating anything.
        return MechanicsOutcome(
            summary=(
                f"{action.actor_name} ne parvient pas à lancer "
                f"{spell.name} ({exc})."
            ),
            player_intent=intent,
        )

    budget = caster.action_budget
    if spell.casting_time == CastingTime.BONUS_ACTION:
        budget.bonus_action_used = True
    elif spell.casting_time == CastingTime.REACTION:
        budget.reaction_used_this_round = True
    else:
        budget.action_used = True

    side.pending_dice_embeds.append(("spell_cast", result, action.actor_name))

    target_label = f" sur {target.name}" if target is not None else ""
    effects: list[str] = []
    target_defeated: str | None = None

    # hp_delta carries the ACTUAL HP change, not the rolled amount —
    # healing clamps at max_hp and damage clamps at 0.
    hp_delta: dict[str, int] = {}
    if target is not None:
        target_delta = target.character.hp - target_hp_before
        if target_delta:
            hp_delta[target.name] = target_delta
    if target is None or target.name != caster.name:
        caster_delta = caster.character.hp - caster_hp_before
        if caster_delta:
            hp_delta[caster.name] = caster_delta

    if result.damage > 0 and target is not None:
        effects.append(f"{result.damage} dégâts")
        if not target.is_alive:
            target_defeated = target.name
    if result.healing > 0:
        heal_recipient = target if target is not None else caster
        actual_healed = max(0, hp_delta.get(heal_recipient.name, 0))
        effects.append(f"{actual_healed} PV rendus")
    if result.condition_applied and target is not None:
        effects.append(f"{target.name} est {result.condition_applied}")

    summary = f"{action.actor_name} lance {spell.name}{target_label}"
    if effects:
        summary += " — " + ", ".join(effects)
    summary += "."

    facts_lines = [summary]
    if result.save_outcome is not None and not result.target_failed_save:
        facts_lines.append(
            f"{target.name if target else 'La cible'} réussit son jet de "
            "sauvegarde — effet réduit."
        )
    if result.slot_used is not None:
        facts_lines.append(
            f"Emplacement de sort niveau {result.slot_used} consommé."
        )
    if target_defeated:
        facts_lines.append(f"{target_defeated} est vaincu.")

    return MechanicsOutcome(
        summary=summary,
        player_intent=intent,
        outcome_facts="\n".join(facts_lines),
        public_effects=PublicEffects(hp_delta=hp_delta),
        target_defeated=target_defeated,
    )


def resolve_defend(
    *,
    action: InterpretedAction,
    actor_name: str,
    combat_state: "CombatState | None",
    side: ResolveSideChannel,
) -> MechanicsOutcome:
    """Resolve the Defend (Dodge) action — consume Action, apply DODGING.

    DODGING gives attackers disadvantage until the start of the dodger's
    next turn (cleared by :func:`engine.combat.advance_turn`).
    """
    from engine.combat import consume_action
    from bot.pipeline.narrate import build_player_intent

    intent = build_player_intent(action)
    fallback = MechanicsOutcome(
        summary=f"{action.actor_name} performs Defend.",
        player_intent=intent,
    )
    if combat_state is None:
        return fallback
    actor = _find_live_combatant(action.actor_name, combat_state)
    if actor is None:
        return fallback

    try:
        consume_action(actor)
    except ValueError as exc:
        return MechanicsOutcome(summary=str(exc), player_intent=intent)

    apply_condition(
        actor.conditions,
        ActiveCondition(condition_type=ConditionType.DODGING, source="defend"),
    )
    summary = (
        f"{action.actor_name} se met en garde (Esquive) — les attaques "
        "contre lui ont un désavantage jusqu'à son prochain tour."
    )
    return MechanicsOutcome(
        summary=summary,
        player_intent=intent,
        outcome_facts=summary,
    )


def resolve_disengage(
    *,
    action: InterpretedAction,
    actor_name: str,
    combat_state: "CombatState | None",
    side: ResolveSideChannel,
) -> MechanicsOutcome:
    """Resolve the Disengage action — consume Action, suppress OOA.

    Delegates to :func:`engine.combat.disengage`, which flags
    ``disengaged_this_turn`` so zone moves skip opportunity attacks.
    """
    from engine.combat import disengage
    from bot.pipeline.narrate import build_player_intent

    intent = build_player_intent(action)
    fallback = MechanicsOutcome(
        summary=f"{action.actor_name} performs Disengage.",
        player_intent=intent,
    )
    if combat_state is None:
        return fallback
    actor = _find_live_combatant(action.actor_name, combat_state)
    if actor is None:
        return fallback

    try:
        disengage(actor)
    except ValueError as exc:
        return MechanicsOutcome(summary=str(exc), player_intent=intent)

    summary = (
        f"{action.actor_name} se désengage — ses déplacements ne "
        "provoquent plus d'attaques d'opportunité ce tour."
    )
    return MechanicsOutcome(
        summary=summary,
        player_intent=intent,
        outcome_facts=summary,
    )


def _find_actor_character(
    actor_name: str,
    session: "GameSession | None",
) -> Character | None:
    """Return the live ``Character`` whose ``name`` matches ``actor_name``.

    First searches the active combat state (where the combatant's character
    carries any in-fight HP delta), then the session character map. Returns
    ``None`` when neither contains a match — typical for tests that build a
    pipeline without a session.
    """
    if session is None:
        return None
    combat_state = getattr(session, "combat_state", None)
    if combat_state is not None:
        for combatant in getattr(combat_state, "combatants", []):
            if combatant.name == actor_name:
                return combatant.character
    chars = getattr(session, "characters", None) or {}
    for char in chars.values():
        if char.name == actor_name:
            return char
    return None


def resolve_improvise(
    *,
    action: InterpretedAction,
    actor_name: str,
    npcs: dict[str, NPC],
    session: "GameSession | None",
    side: ResolveSideChannel,
) -> MechanicsOutcome:
    """Resolve an IMPROVISE action — attempt to convert it to a skill check.

    Pipeline:
        1. Infer the relevant D&D 5e :class:`Skill` from the action text via
           :func:`engine.skill_check.infer_skill_from_text`. Both the
           interpreter-extracted ``improvise_description`` and the raw
           player text are scanned.
        2. Compose a difficulty class from
           :func:`engine.skill_check.compute_skill_check_dc` — when
           ``action.target_name`` resolves to an NPC in scene, the DC is
           contested by that NPC's relevant ability score (passive
           Perception for theft/stealth, passive Insight for deception,
           CHA for social skills) and adjusted by their disposition;
           otherwise a static DC is used and shaped by narrative
           qualifiers ("petit", "risqué", "héroïque").
        3. If the actor's :class:`Character` is reachable through
           ``session``, roll a ``1d20 + skill modifier`` (proficiency and
           Expertise applied via :func:`compute_skill_modifier`) against
           that DC. The :class:`D20CheckResult` is queued on
           ``side.pending_dice_embeds`` so the cog renders the visible
           "🎲 Test de Escamotage (DEX)" embed alongside the narration.
        4. The narrator receives the outcome tier through
           :attr:`MechanicsOutcome.outcome_facts` so it describes a
           success / near-failure / critical-failure faithfully.
        5. If no skill matches OR the character is missing, fall back to
           the legacy "narrator arbitrates without a roll" behaviour —
           preserved for purely flavour actions ("je m'assois", "I take
           a deep breath").
    """
    from bot.pipeline.narrate import build_player_intent

    intent = build_player_intent(action)
    description = action.improvise_description or action.raw_input or ""

    skill = infer_skill_from_text(
        description, extra_texts=(action.raw_input,) if action.raw_input else (),
    )

    legacy_summary = (
        f"{action.actor_name} attempts an improvised action: {description}"
    )

    if skill is None:
        return MechanicsOutcome(summary=legacy_summary, player_intent=intent)

    character = _find_actor_character(action.actor_name, session)
    if character is None:
        # No character to roll against — keep legacy behaviour rather than
        # fabricating a fake roll.
        logger.info(
            "IMPROVISE skill=%s but no character found for actor=%s — falling back",
            skill.value, action.actor_name,
        )
        return MechanicsOutcome(summary=legacy_summary, player_intent=intent)

    # Resolve a contest target if the player's action references an NPC
    # in scene. ``action.target_name`` is canonicalised by
    # EntityResolver, so a successful match means an NPC really exists.
    target_npc: NPC | None = None
    if action.target_name and action.target_name in npcs:
        target_npc = npcs[action.target_name]

    dc = compute_skill_check_dc(
        text=f"{description} {action.raw_input or ''}".strip(),
        skill=skill,
        target_npc=target_npc,
    )

    modifier = compute_skill_modifier(character, skill)
    expression = f"1d20+{modifier}" if modifier >= 0 else f"1d20{modifier}"
    check = roll_check(expression, dc=dc)
    ability = SKILL_ABILITY[skill]

    side.pending_dice_embeds.append(
        ("skill_check", check, action.actor_name, skill),
    )

    sign = "+" if modifier >= 0 else ""
    contest_note = (
        f" (contesté par {target_npc.name})" if target_npc is not None else ""
    )
    summary = (
        f"{action.actor_name} tente {description} — "
        f"{skill.value} ({ability.value}) {check.total} vs DC {check.dc}"
        f"{contest_note} → {check.outcome.value}"
    )
    facts = (
        f"Skill check: {skill.value} ({ability.value}{sign}{modifier}) — "
        f"d20={check.rolls[0]}, total={check.total}, DC={check.dc}"
        f"{contest_note}, outcome={check.outcome.value}, "
        f"margin={check.margin:+d}."
    )
    logger.info(
        "IMPROVISE skill_check actor=%s skill=%s ability=%s mod=%d "
        "nat=%d total=%d dc=%d contest=%s outcome=%s",
        action.actor_name, skill.value, ability.value, modifier,
        check.rolls[0], check.total, check.dc,
        target_npc.name if target_npc else "—",
        check.outcome.value,
    )
    return MechanicsOutcome(
        summary=summary,
        player_intent=intent,
        outcome_facts=facts,
    )


def resolve_pickup(
    *,
    action: InterpretedAction,
    actor_name: str,
    session: "GameSession | None",
    db_factory: Any,
) -> str:
    """Move a scene item into the acting player's inventory (Lot G)."""
    item_name = action.target_name or action.item_name or ""
    if not item_name or session is None or db_factory is None:
        return f"{action.actor_name} reaches for something, but cannot grasp it."

    # Find the discord user_id for the acting character.
    user_id: int | None = None
    for uid, char in session.characters.items():
        if char.name == action.actor_name:
            user_id = uid
            break
    if user_id is None:
        return f"{action.actor_name} reaches for {item_name}, but cannot grasp it."

    from bot.scene_hydration import take_scene_item

    item = take_scene_item(
        session,
        item_name=item_name,
        user_id=user_id,
        db_factory=db_factory,
    )
    if item is None:
        return (
            f"{action.actor_name} reaches for '{item_name}', but it is not"
            f" here."
        )
    return (
        f"{action.actor_name} picks up the {item_name} and stows it in"
        f" their pack."
    )


# ---------------------------------------------------------------------------
# Combat helpers
# ---------------------------------------------------------------------------

def should_trivial_resolve(
    *,
    npc: NPC,
    session: "GameSession | None",
    campaign_id: str,
) -> bool:
    """Decide whether an attack on ``npc`` skips the combat round system.

    Trivial resolution applies to peaceful, defenseless NPCs that an
    adventurer would obviously overpower in one swing. We deliberately
    exclude HOSTILE / UNFRIENDLY NPCs (they fight back), story-critical
    NPCs (villain, combat-beat foes — even if currently hydrated with
    commoner stats), and anything that :func:`is_trivially_defeatable`
    rejects (HP, AC, or defensive conditions).
    """
    if not npc.is_alive:
        return False

    # Story-critical NPCs are never trivially resolved, even if they were
    # hydrated with weak stats (commoner-style). They must go through the
    # full combat system once it's bootstrapped — otherwise a villain
    # could be one-shot via `trivial_kill` simply because scene
    # hydration gave them hp=4/ac=10. See tasks/combat/00_bugfix_*.
    story_arc = getattr(session, "story_arc", None) if session is not None else None
    if story_arc is not None:
        if npc.name == story_arc.villain_name:
            return False
        beats = getattr(story_arc, "beats", None)
        current_index = getattr(story_arc, "current_beat_index", 0)
        if beats and 0 <= current_index < len(beats):
            current_beat = beats[current_index]
            if (
                current_beat.encounter_type in ("combat", "boss")
                and npc.name in current_beat.npc_names
            ):
                return False

    if npc.disposition in (
        NPCDisposition.HOSTILE,
        NPCDisposition.UNFRIENDLY,
    ):
        return False
    return is_trivially_defeatable(npc)


def trivial_kill(
    *,
    target_npc: NPC,
    actor_name: str,
    location: Location | None,
    npcs: dict[str, NPC],
    session: "GameSession | None",
    campaign_id: str,
    db_factory: Any,
    side: ResolveSideChannel,
) -> None:
    """Auto-resolve an attack against ``target_npc`` and propagate death."""
    # Find the attacker Character object
    attacker_pc = find_attacker_character(actor_name=actor_name, session=session)
    if attacker_pc is None:
        # No matching PC — fall back to the regular bootstrap path by
        # leaving trivial_kill_mechanics unset and letting the caller
        # treat this as combat. Should not happen in practice.
        logger.warning(
            "TRIVIAL_KILL no attacker character matched campaign=%s actor=%s",
            campaign_id, actor_name,
        )
        return

    weapon = find_attacker_weapon(attacker_pc=attacker_pc, session=session)
    result = trivial_resolve(attacker_pc, target_npc, weapon=weapon)
    side.trivial_kill_mechanics = result.description
    if result.target_killed:
        side.trivial_kill_target = target_npc.name
    logger.info(
        "TRIVIAL_KILL campaign=%s attacker=%s target=%s hit=%s damage=%d killed=%s",
        campaign_id, attacker_pc.name, target_npc.name,
        result.hit, result.damage, result.target_killed,
    )
    if result.target_killed:
        handle_npc_death(
            npc=target_npc,
            killer=attacker_pc,
            location=location,
            npcs=npcs,
            session=session,
            campaign_id=campaign_id,
            db_factory=db_factory,
        )


def find_attacker_character(
    *,
    actor_name: str,
    session: "GameSession | None",
) -> Character | None:
    """Look up the Character object whose name matches ``actor_name``."""
    if session is None:
        return None
    for char in session.characters.values():
        if char.name == actor_name:
            return char
    return None


def find_attacker_weapon(
    *,
    attacker_pc: Character,
    session: "GameSession | None",
) -> Weapon | None:
    """Return the attacker's main-hand weapon if any."""
    if session is None:
        return None
    for user_id, char in session.characters.items():
        if char is attacker_pc:
            inv = session.inventories.get(user_id)
            if inv is None:
                return None
            weapon = inv.equipped.get(EquipmentSlot.MAIN_HAND)
            if isinstance(weapon, Weapon):
                return weapon
            return None
    return None


def handle_npc_death(
    *,
    npc: NPC,
    killer: Character | None,
    location: Location | None,
    npcs: dict[str, NPC],
    session: "GameSession | None",
    campaign_id: str,
    db_factory: Any,
    witnesses_turn_hostile: bool = True,
) -> None:
    """Propagate an NPC death across world state.

    Single shared propagator for both kill paths: the trivial-kill fast
    path (``killer`` is the attacking PC, witnesses flip HOSTILE — it is
    the murder of a peaceful NPC) and the full-combat path via
    :func:`bot.combat_end.finalize_combat` (``killer`` may be ``None``,
    ``witnesses_turn_hostile=False`` — bystanders don't blame the party
    for defending itself).
    """
    # 1. Idempotent kill (trivial_resolve already did it). Unbind the
    #    corpse from its location so no scene query rebinds it (audit H15).
    npc.kill()
    npc.location_name = None

    # 2. Remove from the live location's npcs_present and from the
    #    in-memory NPC dict so the next scene context doesn't list them.
    if location is not None:
        location.npcs_present = [
            n for n in location.npcs_present if n != npc.name
        ]
    npcs.pop(npc.name, None)

    # 3. Witnesses: friendly NPCs in the same location turn HOSTILE
    #    (trivial-kill path only — see docstring).
    witnesses_turned: list[NPC] = []
    if witnesses_turn_hostile:
        for other in list(npcs.values()):
            if not other.is_alive:
                continue
            if other.disposition in (
                NPCDisposition.FRIENDLY,
                NPCDisposition.ALLIED,
            ):
                other.disposition = NPCDisposition.HOSTILE
                witnesses_turned.append(other)

    # 4. Persist DB state if a db_factory is wired.
    if db_factory is not None:
        try:
            persist_death(
                npc=npc,
                location=location,
                witnesses_turned=witnesses_turned,
                campaign_id=campaign_id,
                db_factory=db_factory,
            )
        except Exception:
            logger.exception(
                "TRIVIAL_KILL persistence failed campaign=%s npc=%s",
                campaign_id, npc.name,
            )

    # 5. Append a world-fact line to the per-campaign markdown log.
    try:
        append_world_fact(killer=killer, victim=npc, location=location, campaign_id=campaign_id)
    except Exception:
        logger.exception(
            "TRIVIAL_KILL world-fact write failed campaign=%s",
            campaign_id,
        )

    # 6. Story bible event line. "MEURTRE" is the trivial-kill wording
    #    (cutting down a peaceful NPC); combat deaths are plain casualties.
    killer_label = killer.name if killer is not None else "Le groupe"
    event_label = "MEURTRE" if witnesses_turn_hostile else "MORT AU COMBAT"
    if (
        session is not None
        and session.story_bible is not None
    ):
        try:
            session.story_bible.log_event(
                f"⚔️ {event_label} — {killer_label} a tué {npc.name} "
                f"dans {location.name if location else 'un lieu inconnu'}.",
            )
        except Exception:
            logger.exception(
                "NPC_DEATH story bible log failed campaign=%s",
                campaign_id,
            )

    logger.info(
        "NPC killed campaign=%s npc=%s killer=%s witnesses_turned_hostile=%d",
        campaign_id, npc.name, killer_label, len(witnesses_turned),
    )


def persist_death(
    *,
    npc: NPC,
    location: Location | None,
    witnesses_turned: list[NPC],
    campaign_id: str,
    db_factory: Any,
) -> None:
    """Persist NPC death + location update + witness flips via repos."""
    from db.repositories.location_repo import LocationRepository
    from db.repositories.npc_repo import NPCRepository

    assert db_factory is not None
    db_session = db_factory()
    try:
        npc_repo = NPCRepository(db_session)
        npc_repo.update(npc, campaign_id)
        for witness in witnesses_turned:
            npc_repo.update(witness, campaign_id)
        if location is not None:
            loc_repo = LocationRepository(db_session)
            loc_repo.update(location, campaign_id)
        db_session.commit()
    finally:
        db_session.close()


def append_world_fact(
    *,
    killer: Character | None,
    victim: NPC,
    location: Location | None,
    campaign_id: str,
) -> None:
    """Append a one-line markdown fact to ``logs/campaigns/{id}_facts.md``."""
    if not campaign_id:
        return
    path = Path("logs/campaigns") / f"{campaign_id}_facts.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    loc_name = location.name if location is not None else "lieu inconnu"
    killer_label = killer.name if killer is not None else "Le groupe"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"- {killer_label} a tué {victim.name} dans {loc_name}.\n")
