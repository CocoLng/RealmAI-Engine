"""ActionPipeline — orchestrates the 6-step flow for free-text player actions.

Discord-agnostic on purpose. The cog (``ActionHandlerCog``) is responsible
for filtering messages and rendering progress / clarification UI; this module
only knows about the AI services and the deterministic engine.

Phases (also reported via the optional ``progress_callback``):

1. ``INTERPRETING``        — LLM classifies the player's intent (Interpreter)
2. ``RESOLVING_ENTITIES``  — pure-Python entity resolution (EntityResolver)
3. ``VALIDATING``          — pure-Python rule check (validators.py)
4. ``RESOLVING_ACTION``    — engine modules apply mechanical effects
5. ``ASSEMBLING_CONTEXT``  — build a small in-memory context for the narrator
6. ``NARRATING``           — LLM produces immersive prose (Narrator)

Three possible outcomes:

- :class:`ActionPipelineResult` — full success, narrative + mechanics ready
- :class:`AmbiguityResult`      — caller must ask the user to disambiguate
- :class:`UnknownEntityResult`  — caller posts the in-character refusal

The current MVP performs no DB writes during ``RESOLVING_ACTION`` for
exploration actions; the narrator describes outcomes the engine has already
chosen, and persistent state changes happen only when concrete world updates
are introduced (e.g. moving the party to a new location). The session lock
is still acquired so future state-mutating handlers do not race.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ai.entity_resolver import EntityCandidate, EntityResolver, ResolutionResult
from ai.interpreter import Interpreter
from ai.models import (
    InterpretedAction,
    MechanicsOutcome,
    NarrativeResult,
    PublicEffects,
)
from ai.narrator import Narrator
from ai.scene_context import SceneContext, build_scene_context
from bot.llm_retry import retry_llm_call
from bot.persistence import persist_session
from engine.character import Character, compute_modifier
from engine.combat import (
    CombatEndReason,
    CombatState,
    TrivialResolveResult,
    check_combat_end,
    record_combat_event,
    start_combat,
    trivial_resolve,
)
from engine.dice import RollOutcome, roll_check
from bot.combat_entry import CombatTrigger, detect_combat_trigger, enter_combat
from engine.conditions import (
    ActiveCondition,
    ConditionType,
)
from engine.inventory import EquipmentSlot, Inventory, Weapon, equip_item, remove_item, unequip_item
from engine.validators import (
    Action,
    ActionType,
    EXPLORATION_ACTION_TYPES,
    ValidationResult,
    validate_action,
    validate_exploration_action,
)
from world.location import Location
from world.npc import NPC, NPCDisposition
from world.story_arc import BeatEffects, StoryBeat

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)

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
# Phase enum + result types
# ---------------------------------------------------------------------------


class PipelinePhase(IntEnum):
    """Observability for the action pipeline progress."""

    PENDING            = 0
    INTERPRETING       = 1
    RESOLVING_ENTITIES = 2
    VALIDATING         = 3
    RESOLVING_ACTION   = 4
    ASSEMBLING_CONTEXT = 5
    NARRATING          = 6
    DONE               = 7
    FAILED             = 8


class ActionPipelineResult(BaseModel):
    """Successful pipeline run."""

    narrative: str
    tone: Literal["dramatic", "tense", "humorous", "somber"]
    mechanics_text: str
    public_effects: PublicEffects = Field(default_factory=PublicEffects)
    interpreted_action: InterpretedAction
    new_beat: StoryBeat | None = None
    npc_name: str | None = None
    npc_dialogue: str | None = None
    is_question: bool = False
    is_free_action: bool = False
    """True for EQUIP (free action) — TurnManager re-prompts instead of advancing."""


class AmbiguityResult(BaseModel):
    """Entity resolution found multiple candidates — caller must disambiguate."""

    field_name: Literal["target_name", "item_name"]
    raw_value: str
    candidates: list[EntityCandidate] = Field(default_factory=list)
    partial_action: InterpretedAction

    model_config = {"arbitrary_types_allowed": True}


class UnknownEntityResult(BaseModel):
    """Entity could not be resolved — narrator generated an in-character refusal."""

    field_name: str
    raw_value: str
    partial_action: InterpretedAction
    refusal_narrative: str
    tone: Literal["dramatic", "tense", "humorous", "somber"] = "somber"


PipelineOutput = ActionPipelineResult | AmbiguityResult | UnknownEntityResult

ProgressCallback = Callable[[PipelinePhase], Awaitable[None]]


def _persist_story_arc(db_factory: Callable[[], Any], arc: Any) -> None:
    """Persist a StoryArc update via :class:`StoryArcRepository`."""
    if arc is None:
        return
    from db.repositories.story_arc_repo import StoryArcRepository

    db_session = db_factory()
    try:
        StoryArcRepository(db_session).update(arc)
        db_session.commit()
    finally:
        db_session.close()


# ---------------------------------------------------------------------------
# ActionPipeline
# ---------------------------------------------------------------------------


@dataclass
class ActionPipeline:
    """Discord-agnostic orchestrator for one free-text player action.

    A new instance is created for each player message. State that must be
    shared across pipelines (the per-campaign asyncio lock) lives on the
    class itself.
    """

    interpreter: Interpreter
    narrator: Narrator
    location: Location | None
    npcs: dict[str, NPC]
    actor_name: str
    language: str = "fr"
    campaign_id: str = ""
    combat_state: CombatState | None = None
    inventory: Inventory | None = None
    session: "GameSession | None" = None
    db_factory: Callable[[], Any] | None = None

    _trivial_kill_mechanics: str | None = field(default=None, init=False)

    _pending_flee_destination: str | None = field(default=None, init=False)
    """Destination zone stored when MOVE is auto-converted to FLEE in combat.
    Consumed by _resolve_flee after a successful full-party escape."""

    _pending_combat_start_embed: "tuple[CombatState, CombatTrigger] | None" = field(
        default=None, init=False,
    )
    """Stored by _validate when a new combat is bootstrapped. The caller
    (ActionHandlerCog) reads this after _validate returns and posts the
    combat-start embed before narration."""

    _pending_dice_embeds: list[Any] = field(default_factory=list, init=False)
    """Dice roll results to display as embeds (task 60). Populated by
    _resolve_flee and future combat resolvers. Consumed by the caller."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(
        self,
        player_text: str,
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        """Run the full pipeline for a fresh player action."""
        scene = build_scene_context(
            location=self.location,
            npcs=self.npcs,
            combat_state=self.combat_state,
        )

        await self._emit(progress_callback, PipelinePhase.INTERPRETING)
        interpreted = await self._call_interpreter(player_text, scene)

        return await self._continue_from_resolution(
            interpreted, progress_callback,
        )

    async def resume_with_resolution(
        self,
        ambiguity: AmbiguityResult,
        chosen_entity_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        """Continue a paused pipeline after the user picked a candidate."""
        # Patch the partial action with the disambiguation choice.
        partial = ambiguity.partial_action
        if ambiguity.field_name == "target_name":
            patched = partial.model_copy(update={"target_name": chosen_entity_id})
        elif ambiguity.field_name == "item_name":
            patched = partial.model_copy(update={"item_name": chosen_entity_id})
        else:
            patched = partial

        return await self._continue_from_resolution(patched, progress_callback)

    async def process_interpreted_action(
        self,
        action: InterpretedAction,
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        """Run the pipeline starting from a pre-built :class:`InterpretedAction`.

        Used by the combat TurnManager (task 64) when a player clicks a
        button on the combat hub — the action is already fully structured,
        so we skip the interpreter phase and jump straight into entity
        resolution + validation + mechanics + narration. Auto-Dodge on
        timeout also routes through here.
        """
        return await self._continue_from_resolution(action, progress_callback)

    # ------------------------------------------------------------------
    # Internal flow
    # ------------------------------------------------------------------

    async def _continue_from_resolution(
        self,
        interpreted: InterpretedAction,
        progress_callback: ProgressCallback | None,
    ) -> PipelineOutput:
        await self._emit(progress_callback, PipelinePhase.RESOLVING_ENTITIES)
        resolution = EntityResolver.resolve(
            interpreted,
            location=self.location,
            npcs=self.npcs,
            combat_state=self.combat_state,
            inventory=self.inventory,
            interpreter=self.interpreter,
            language=self.language,
        )

        if resolution.status == "ambiguous":
            return AmbiguityResult(
                field_name=resolution.field_name or "target_name",  # type: ignore[arg-type]
                raw_value=resolution.raw_value or "",
                candidates=list(resolution.candidates),
                partial_action=interpreted,
            )

        if resolution.status == "unknown":
            refusal = await self._narrate_unknown(interpreted, resolution)
            return UnknownEntityResult(
                field_name=resolution.field_name or "target_name",
                raw_value=resolution.raw_value or "",
                partial_action=interpreted,
                refusal_narrative=refusal.narrative,
                tone=refusal.tone,
            )

        # status in {"resolved", "not_applicable"} — patch action with the
        # canonical entity id when one was found.
        if (
            resolution.status == "resolved"
            and resolution.field_name == "target_name"
            and resolution.resolved_entity is not None
        ):
            updates: dict[str, object] = {
                "target_name": resolution.resolved_entity,
            }
            if resolution.reclassified_action_type is not None:
                updates["action_type"] = resolution.reclassified_action_type
            interpreted = interpreted.model_copy(update=updates)
        elif (
            resolution.status == "resolved"
            and resolution.field_name == "item_name"
            and resolution.resolved_entity is not None
        ):
            interpreted = interpreted.model_copy(
                update={"item_name": resolution.resolved_entity},
            )

        # --- Auto-resolve weapon for ATTACK when player omitted weapon name ---
        if interpreted.action_type == ActionType.ATTACK:
            resolved_weapon = self._auto_resolve_weapon_name(
                interpreted.weapon_name, self.inventory,
            )
            if resolved_weapon != interpreted.weapon_name:
                interpreted = interpreted.model_copy(
                    update={"weapon_name": resolved_weapon},
                )

        await self._emit(progress_callback, PipelinePhase.VALIDATING)
        validation = self._validate(interpreted)
        if not validation.is_valid:
            refusal = await self._narrate_rule_failure(interpreted, validation)
            return UnknownEntityResult(
                field_name="rule",
                raw_value=validation.error_message or "",
                partial_action=interpreted,
                refusal_narrative=refusal.narrative,
                tone=refusal.tone,
            )

        await self._emit(progress_callback, PipelinePhase.RESOLVING_ACTION)
        outcome = await self._resolve_mechanics(interpreted)

        # Record a short narration hint for the narrator context.
        # Only in active combat: the narrator reads the tail of this list from
        # the COMBAT ACTIVE section of the scene prompt. The engine never touches
        # the list; the cap is enforced by ``record_combat_event`` itself.
        if self.combat_state is not None and self.combat_state.is_active:
            event_text = outcome.summary.strip()
            if event_text:
                record_combat_event(self.combat_state, event_text)

        # Beat completion check — deterministic trigger.
        beat_completed = False
        if (
            self.session is not None
            and interpreted.action_type != ActionType.QUESTION
            and self._check_beat_completion(interpreted, outcome)
        ):
            beat_completed = True
            arc = self.session.story_arc
            beat = arc.beats[arc.current_beat_index]
            hint = self._apply_beat_effects(beat.on_complete)
            if hint:
                outcome = outcome.model_copy(update={
                    "outcome_facts": (outcome.outcome_facts + " " + hint).strip(),
                })
            from world.story_arc import advance_beat
            self.session.story_arc = advance_beat(arc)
            logger.info(
                "BEAT trigger-complete campaign=%s beat=%d title=%r",
                self.campaign_id, beat.beat_number, beat.title,
            )
        elif (
            self.session is not None
            and getattr(self.session, "story_arc", None) is not None
            # Only IMPROVISE is eligible for creative-completion fallback.
            # Standard actions (TALK, ATTACK, PICKUP, MOVE, …) have
            # direct triggers via _check_beat_completion. If the direct
            # match failed, the beat is NOT done — we must not let the
            # 4B judge second-guess standard actions, otherwise players
            # skip ahead without narrative justification (observed
            # 2026-04-11: saying hi to an NPC advanced the interrogation
            # beat at confidence 0.95 via the LLM fallback).
            and interpreted.action_type == ActionType.IMPROVISE
        ):
            arc = self.session.story_arc
            beat = arc.beats[arc.current_beat_index]
            if (
                beat.completion_trigger is not None
                and self.location is not None
            ):
                from bot.game_session import _normalize_location
                loc_ratio = difflib.SequenceMatcher(
                    None,
                    _normalize_location(self.location.name),
                    _normalize_location(beat.location_hint),
                ).ratio()
                if loc_ratio >= 0.5:
                    judge = await self._llm_beat_fallback(interpreted, beat, outcome)
                    logger.info(
                        "BEAT fallback campaign=%s completed=%s confidence=%.2f",
                        self.campaign_id,
                        judge.get("completed"), judge.get("confidence"),
                    )
                    if judge.get("completed") and judge.get("confidence", 0) >= 0.85:
                        beat_completed = True
                        hint = self._apply_beat_effects(beat.on_complete)
                        if hint:
                            outcome = outcome.model_copy(update={
                                "outcome_facts": (outcome.outcome_facts + " " + hint).strip(),
                            })
                        from world.story_arc import advance_beat
                        self.session.story_arc = advance_beat(arc)
                        logger.info(
                            "BEAT fallback-complete campaign=%s beat=%d title=%r",
                            self.campaign_id, beat.beat_number, beat.title,
                        )

        await self._emit(progress_callback, PipelinePhase.ASSEMBLING_CONTEXT)
        context_prompt = self._assemble_context(interpreted)

        await self._emit(progress_callback, PipelinePhase.NARRATING)
        narration = await self._call_narrator(
            outcome=outcome,
            context_prompt=context_prompt,
        )

        # Lot D — beat advancement (trigger-based or location-based fallback).
        new_beat: StoryBeat | None = None
        if beat_completed and self.session and self.session.story_arc:
            new_beat = self.session.story_arc.beats[
                self.session.story_arc.current_beat_index
            ]
        elif self.session is not None and hasattr(
            self.session, "advance_beat_if_ready",
        ):
            try:
                candidate = self.session.advance_beat_if_ready()
            except Exception:
                logger.exception(
                    "BEAT advance check failed campaign=%s", self.campaign_id,
                )
                candidate = None
            if isinstance(candidate, StoryBeat):
                new_beat = candidate
        if new_beat is not None and self.db_factory is not None:
            try:
                await asyncio.to_thread(
                    _persist_story_arc,
                    self.db_factory,
                    self.session.story_arc,
                )
                logger.info(
                    "BEAT advanced campaign=%s to=%d title=%r",
                    self.campaign_id,
                    self.session.story_arc.current_beat_index
                    if self.session.story_arc is not None else -1,
                    new_beat.title,
                )
            except Exception:
                logger.exception(
                    "BEAT persist failed campaign=%s", self.campaign_id,
                )

        # Auto-checkpoint: persist full session state after every resolved action (B1).
        if self.db_factory is not None and self.session is not None:
            try:
                await asyncio.to_thread(
                    persist_session, self.db_factory, self.session,
                )
            except Exception:
                logger.exception("AUTO-CHECKPOINT failed campaign=%s", self.campaign_id)

        await self._emit(progress_callback, PipelinePhase.DONE)
        is_question = interpreted.action_type == ActionType.QUESTION
        is_free = interpreted.action_type == ActionType.EQUIP
        result = ActionPipelineResult(
            narrative=narration.narrative,
            tone=narration.tone,
            mechanics_text=outcome.summary,
            public_effects=outcome.public_effects,
            interpreted_action=interpreted,
            new_beat=new_beat,
            npc_name=outcome.npc_name,
            npc_dialogue=outcome.npc_dialogue,
            is_question=is_question,
            is_free_action=is_free,
        )
        logger.info(
            "ACTION complete campaign=%s actor=%s action=%s",
            self.campaign_id,
            interpreted.actor_name,
            interpreted.action_type.value,
            extra={"extra_payload": {
                "mechanics_summary": outcome.summary,
                "player_intent": outcome.player_intent,
                "outcome_facts": outcome.outcome_facts,
                "public_effects": outcome.public_effects.model_dump(),
                "narrative": narration.narrative,
                "tone": narration.tone,
            }},
        )
        return result

    # ------------------------------------------------------------------
    # Phase helpers
    # ------------------------------------------------------------------

    async def _call_interpreter(
        self,
        player_text: str,
        scene: SceneContext,
    ) -> InterpretedAction:
        def _do() -> InterpretedAction:
            return self.interpreter.interpret(
                player_text=player_text,
                actor_name=self.actor_name,
                scene_context=scene,
                language=self.language,
            )

        return await retry_llm_call(
            _do,
            log_label=f"ACTION campaign={self.campaign_id} interpret",
        )

    async def _call_narrator(
        self,
        outcome: MechanicsOutcome,
        context_prompt: str,
    ) -> NarrativeResult:
        def _do() -> NarrativeResult:
            return self.narrator.narrate(
                action_result_text=outcome.summary,
                context_prompt=context_prompt,
                language=self.language,
                player_intent=outcome.player_intent,
                outcome_facts=outcome.outcome_facts,
                has_npc_dialogue=bool(outcome.npc_dialogue),
            )

        return await retry_llm_call(
            _do,
            log_label=f"ACTION campaign={self.campaign_id} narrate",
        )

    @staticmethod
    def _auto_resolve_weapon_name(
        weapon_name: str | None,
        inventory: Inventory | None,
    ) -> str | None:
        """Return the canonical equipped weapon name, resolving player aliases.

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

    def _validate(self, action: InterpretedAction) -> ValidationResult:
        """Convert InterpretedAction → Action and dispatch to the right validator.

        Dispatch logic (in order):
        1. If combat active AND action is MOVE → auto-convert to FLEE, store destination.
        2. If no combat → try detect_combat_trigger; bootstrap if trigger found.
        3. If combat active → combat validators (validate_action or validate_exploration_action).
        4. If no combat → exploration validators, or trivial-kill check, or error.
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
            and self.combat_state is not None
            and self.combat_state.is_active
        ):
            logger.info(
                "MOVE auto-converted to FLEE campaign=%s actor=%s destination=%s",
                self.campaign_id, action.actor_name, eng_action.target_name,
            )
            self._pending_flee_destination = eng_action.target_name
            eng_action = eng_action.model_copy(
                update={"action_type": ActionType.FLEE, "target_name": None},
            )
            # Fall through to combat dispatch below

        # --- 2. If no combat, try to detect a trigger and bootstrap ---
        if self.combat_state is None or not self.combat_state.is_active:
            trigger: CombatTrigger | None = None
            if self.session is not None:
                trigger = detect_combat_trigger(action, self.session)

            if trigger is not None:
                logger.info(
                    "COMBAT bootstrapped kind=%s campaign=%s aggressor=%s enemies=%s",
                    trigger.kind, self.campaign_id,
                    trigger.aggressor_name, trigger.enemy_names,
                )
                # Build party-wide CombatState, roll initiative, apply surprise
                try:
                    pre_state = enter_combat(self.session, trigger)  # type: ignore[arg-type]
                except ValueError as exc:
                    logger.warning("Combat bootstrap failed: %s", exc)
                    return ValidationResult(is_valid=False, error_message=str(exc))
                self.combat_state = start_combat(pre_state.combatants, trigger=trigger)
                self.session.combat_state = self.combat_state  # type: ignore[union-attr]
                self._pending_combat_start_embed = (self.combat_state, trigger)
                # Fall through to combat dispatch below

        # --- 3. Dispatch to the right validator ---
        if self.combat_state is not None and self.combat_state.is_active:
            if eng_action.action_type in EXPLORATION_ACTION_TYPES:
                return validate_exploration_action(
                    eng_action, combat_state=self.combat_state,
                )
            return validate_action(eng_action, self.combat_state)

        # --- 4. No combat — exploration path or trivial kill ---
        if eng_action.action_type in EXPLORATION_ACTION_TYPES:
            return validate_exploration_action(eng_action, combat_state=None)

        # Combat action requested with no active combat → check trivial kill
        if (
            eng_action.action_type == ActionType.ATTACK
            and eng_action.target_name is not None
            and self.npcs.get(eng_action.target_name) is not None
        ):
            target_npc = self.npcs[eng_action.target_name]
            if self._should_trivial_resolve(target_npc):
                self._trivial_kill(target_npc)
                return ValidationResult(is_valid=True)

        return ValidationResult(
            is_valid=False,
            error_message=(
                f"'{eng_action.action_type.value}' nécessite un combat actif."
            ),
        )

    def _should_trivial_resolve(self, npc: NPC) -> bool:
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
        # could be one-shot via `_trivial_kill` simply because scene
        # hydration gave them hp=4/ac=10. See tasks/combat/00_bugfix_*.
        story_arc = getattr(self.session, "story_arc", None) if self.session is not None else None
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

    # ------------------------------------------------------------------
    # Lot E — trivial NPC death
    # ------------------------------------------------------------------

    def _trivial_kill(self, target_npc: NPC) -> None:
        """Auto-resolve an attack against ``target_npc`` and propagate death."""
        attacker_pc = self._find_attacker_character()
        if attacker_pc is None:
            # No matching PC — fall back to the regular bootstrap path by
            # leaving _trivial_kill_mechanics unset and letting the caller
            # treat this as combat. Should not happen in practice.
            logger.warning(
                "TRIVIAL_KILL no attacker character matched campaign=%s actor=%s",
                self.campaign_id, self.actor_name,
            )
            return

        weapon = self._find_attacker_weapon(attacker_pc)
        result = trivial_resolve(attacker_pc, target_npc, weapon=weapon)
        self._trivial_kill_mechanics = result.description
        logger.info(
            "TRIVIAL_KILL campaign=%s attacker=%s target=%s hit=%s damage=%d killed=%s",
            self.campaign_id, attacker_pc.name, target_npc.name,
            result.hit, result.damage, result.target_killed,
        )
        if result.target_killed:
            self._handle_npc_death(target_npc, killer=attacker_pc, result=result)

    def _find_attacker_character(self) -> Character | None:
        """Look up the Character object whose name matches ``actor_name``."""
        if self.session is None:
            return None
        for char in self.session.characters.values():
            if char.name == self.actor_name:
                return char
        return None

    def _find_attacker_weapon(self, attacker_pc: Character) -> Weapon | None:
        """Return the attacker's main-hand weapon if any."""
        if self.session is None:
            return None
        for user_id, char in self.session.characters.items():
            if char is attacker_pc:
                inv = self.session.inventories.get(user_id)
                if inv is None:
                    return None
                weapon = inv.equipped.get(EquipmentSlot.MAIN_HAND)
                if isinstance(weapon, Weapon):
                    return weapon
                return None
        return None

    def _handle_npc_death(
        self,
        npc: NPC,
        *,
        killer: Character,
        result: TrivialResolveResult,
    ) -> None:
        """Propagate an NPC death across world state."""
        # 1. Idempotent kill (trivial_resolve already did it).
        npc.kill()

        # 2. Remove from the live location's npcs_present and from the
        #    in-memory NPC dict so the next scene context doesn't list them.
        location = self.location
        if location is not None:
            location.npcs_present = [
                n for n in location.npcs_present if n != npc.name
            ]
        self.npcs.pop(npc.name, None)

        # 3. Witnesses: friendly NPCs in the same location turn HOSTILE.
        witnesses_turned: list[NPC] = []
        for other in list(self.npcs.values()):
            if other.disposition in (
                NPCDisposition.FRIENDLY,
                NPCDisposition.ALLIED,
            ):
                other.disposition = NPCDisposition.HOSTILE
                witnesses_turned.append(other)

        # 4. Persist DB state if a db_factory is wired.
        if self.db_factory is not None:
            try:
                self._persist_death(npc, location, witnesses_turned)
            except Exception:
                logger.exception(
                    "TRIVIAL_KILL persistence failed campaign=%s npc=%s",
                    self.campaign_id, npc.name,
                )

        # 5. Append a world-fact line to the per-campaign markdown log.
        try:
            self._append_world_fact(killer=killer, victim=npc, location=location)
        except Exception:
            logger.exception(
                "TRIVIAL_KILL world-fact write failed campaign=%s",
                self.campaign_id,
            )

        # 6. Story bible event line.
        if (
            self.session is not None
            and self.session.story_bible is not None
        ):
            try:
                self.session.story_bible.log_event(
                    f"⚔️ MEURTRE — {killer.name} a tué {npc.name} "
                    f"dans {location.name if location else 'un lieu inconnu'}.",
                )
            except Exception:
                logger.exception(
                    "TRIVIAL_KILL story bible log failed campaign=%s",
                    self.campaign_id,
                )

        logger.info(
            "NPC killed campaign=%s npc=%s killer=%s witnesses_turned_hostile=%d",
            self.campaign_id, npc.name, killer.name, len(witnesses_turned),
        )

    def _persist_death(
        self,
        npc: NPC,
        location: Location | None,
        witnesses_turned: list[NPC],
    ) -> None:
        """Persist NPC death + location update + witness flips via repos."""
        from db.repositories.location_repo import LocationRepository
        from db.repositories.npc_repo import NPCRepository

        assert self.db_factory is not None
        db_session = self.db_factory()
        try:
            npc_repo = NPCRepository(db_session)
            npc_repo.update(npc, self.campaign_id)
            for witness in witnesses_turned:
                npc_repo.update(witness, self.campaign_id)
            if location is not None:
                loc_repo = LocationRepository(db_session)
                loc_repo.update(location, self.campaign_id)
            db_session.commit()
        finally:
            db_session.close()

    def _append_world_fact(
        self,
        *,
        killer: Character,
        victim: NPC,
        location: Location | None,
    ) -> None:
        """Append a one-line markdown fact to ``logs/campaigns/{id}_facts.md``."""
        if not self.campaign_id:
            return
        path = Path("logs/campaigns") / f"{self.campaign_id}_facts.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        loc_name = location.name if location is not None else "lieu inconnu"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"- {killer.name} a tué {victim.name} dans {loc_name}.\n")

    def _resolve_equip(self, action: InterpretedAction) -> MechanicsOutcome:
        """Swap equipped weapon — free action, no turn advance."""
        intent = self._build_player_intent(action)
        if self.combat_state is None or action.item_name is None:
            return MechanicsOutcome(summary="Equip failed.", player_intent=intent)

        actor = next(
            (c for c in self.combat_state.combatants if c.name == action.actor_name),
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

    def _resolve_use_item(self, action: InterpretedAction) -> MechanicsOutcome:
        """Use a healing potion — costs the action."""
        intent = self._build_player_intent(action)
        if self.combat_state is None or action.item_name is None:
            return MechanicsOutcome(summary="Use item failed.", player_intent=intent)

        actor = next(
            (c for c in self.combat_state.combatants if c.name == action.actor_name),
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

    async def _resolve_mechanics(
        self, action: InterpretedAction,
    ) -> MechanicsOutcome:
        """Apply mechanical effects and return a layered outcome.

        Returns a :class:`ai.models.MechanicsOutcome` carrying the short
        mechanical summary, the player's framing, and any state-change
        facts. The narrator consumes the three layers separately.
        """
        intent = self._build_player_intent(action)

        if self._trivial_kill_mechanics is not None:
            return MechanicsOutcome(
                summary=self._trivial_kill_mechanics,
                player_intent=intent,
                outcome_facts=self._trivial_kill_mechanics,
            )

        at = action.action_type

        if at == ActionType.EQUIP:
            return self._resolve_equip(action)

        if at == ActionType.USE_ITEM:
            return self._resolve_use_item(action)

        if at == ActionType.FLEE:
            return await self._resolve_flee(action)

        if at == ActionType.LOOK:
            loc = self.location
            summary = (
                f"{action.actor_name} observes {loc.name if loc else 'the area'}."
            )
            return MechanicsOutcome(summary=summary, player_intent=intent)

        if at == ActionType.QUESTION:
            loc = self.location
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
            if self.session and self.session.story_arc:
                arc = self.session.story_arc
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
            if self.combat_state is not None and self.combat_state.is_active:
                return await asyncio.to_thread(
                    self._resolve_talk_in_combat, action,
                )
            return await asyncio.to_thread(self._resolve_talk, action)

        if at == ActionType.MOVE:
            target = action.target_name or ""
            if (
                self.session is not None
                and self.db_factory is not None
                and target
            ):
                from bot.world_navigation import LocationChangeError, change_location
                try:
                    dest = await change_location(
                        self.session, target, db_factory=self.db_factory,
                    )
                except LocationChangeError as exc:
                    logger.warning(
                        "MOVE change_location failed campaign=%s target=%r reason=%s",
                        self.campaign_id, target, exc.reason,
                    )
                    return MechanicsOutcome(
                        summary=f"{action.actor_name} cannot reach {exc.destination}.",
                        player_intent=intent,
                    )
                self.location = dest
                self.npcs = self.session.npcs
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
            summary = await asyncio.to_thread(self._resolve_pickup, action)
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
            description = action.improvise_description or action.raw_input
            return MechanicsOutcome(
                summary=(
                    f"{action.actor_name} attempts an improvised action: {description}"
                ),
                player_intent=intent,
            )

        return MechanicsOutcome(
            summary=f"{action.actor_name} performs {at.value}.",
            player_intent=intent,
        )

    async def _resolve_flee(self, action: InterpretedAction) -> MechanicsOutcome:
        """Roll DEX check (Acrobatics) DC 12 to escape combat.

        Success: combatant marked fled=True, removed from turn rotation.
        Failure: action_used=True, combatant stays in combat.
        When all alive PCs have fled, ends combat with CombatEndReason.FLED
        and applies the stored flee destination (from MOVE auto-conversion).
        """
        assert self.combat_state is not None
        combatant = next(
            (c for c in self.combat_state.combatants if c.name == action.actor_name),
            None,
        )
        if combatant is None:
            return MechanicsOutcome(
                summary=f"{action.actor_name} n'est pas en combat.",
                player_intent=self._build_player_intent(action),
            )

        dex_score = combatant.character.ability_scores.DEX
        dex_mod = compute_modifier(dex_score)
        expression = f"1d20+{dex_mod}" if dex_mod >= 0 else f"1d20{dex_mod}"
        check = roll_check(expression, dc=12)
        intent = self._build_player_intent(action)

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
        self._pending_dice_embeds.append(("flee_check", check, action.actor_name))

        # Check if combat ends (all alive PCs have fled)
        end = check_combat_end(self.combat_state)
        if end == CombatEndReason.FLED:
            # Centralised finalisation. Local import to avoid the
            # bot.combat_end → ActionPipeline import cycle.
            if self.session is not None:
                from bot.combat_end import finalize_combat
                finalize_combat(self.session, CombatEndReason.FLED)
            else:
                # Session-less pipeline (shouldn't happen in live flow but
                # some tests build one): fall back to a minimal state flip.
                self.combat_state.is_active = False
                self.combat_state.end_reason = end
            destination_name: str | None = None
            if self._pending_flee_destination and self.session and self.db_factory:
                from bot.world_navigation import LocationChangeError, change_location
                try:
                    dest = await change_location(
                        self.session,
                        self._pending_flee_destination,
                        db_factory=self.db_factory,
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

    # ------------------------------------------------------------------
    # Beat completion (deterministic triggers)
    # ------------------------------------------------------------------

    def _check_beat_completion(
        self,
        action: InterpretedAction,
        outcome: MechanicsOutcome | None = None,
    ) -> bool:
        """Check if the action satisfies the current beat's completion trigger.

        For most trigger types (interact, defeat, arrive, pickup, search)
        the action itself is atomic: doing it at all means the objective
        is met. **TALK is different** — addressing the right NPC does not
        automatically mean the conversation was productive. If the NPC
        refused to share anything or pushed back, the beat must NOT
        advance. That gate is applied here via ``outcome`` when provided.
        """
        if self.session is None or getattr(self.session, "story_arc", None) is None:
            return False
        arc = self.session.story_arc
        if arc.current_beat_index >= len(arc.beats):
            return False
        beat = arc.beats[arc.current_beat_index]
        trigger = beat.completion_trigger
        if trigger is None:
            return False

        type_map: dict[str, set[str]] = {
            "interact": {ActionType.INTERACT},
            "defeat": {ActionType.ATTACK},
            "talk": {ActionType.TALK},
            "arrive": {ActionType.MOVE},
            "search": {ActionType.SEARCH},
            "pickup": {ActionType.PICKUP},
        }
        allowed = type_map.get(trigger.type, set())
        if action.action_type not in allowed:
            return False

        target_matches = False
        if trigger.target and action.target_name:
            from bot.game_session import _normalize_location
            norm_target = _normalize_location(action.target_name)
            norm_trigger = _normalize_location(trigger.target)
            # Substring inclusion — robust to short-vs-long mismatches
            # (e.g. action target "Kaelen" vs trigger target "Kaelen, le
            # Gardien Blessé"). Without this, difflib.ratio() rejects the
            # pair and the LLM fallback starts guessing.
            if norm_target and norm_trigger:
                if norm_target in norm_trigger or norm_trigger in norm_target:
                    target_matches = True
            if not target_matches:
                ratio = difflib.SequenceMatcher(
                    None, norm_target, norm_trigger,
                ).ratio()
                target_matches = ratio >= 0.6
        if not target_matches:
            return False

        # Quality gate for TALK triggers — the conversation must have
        # actually produced something. A dialogue that revealed nothing
        # (NPC stonewalled the player) or made the NPC more hostile
        # (disposition_change < 0) does NOT complete the beat, even when
        # the player addressed the right character. Observed 2026-04-11:
        # the player talked to the guard, the NPC agent returned
        # disposition_change=-1 with a cold reveal, and the beat
        # advanced anyway — leaving the player confused.
        if trigger.type == "talk" and outcome is not None:
            if outcome.talk_reveals_count <= 0:
                logger.info(
                    "BEAT talk-gate blocked campaign=%s reason=no-reveals",
                    self.campaign_id,
                )
                return False
            if outcome.talk_disposition_change < 0:
                logger.info(
                    "BEAT talk-gate blocked campaign=%s reason=disposition-regressed delta=%d",
                    self.campaign_id, outcome.talk_disposition_change,
                )
                return False

        return True

    def _apply_beat_effects(self, effects: BeatEffects) -> str:
        """Apply beat completion effects to the current location.

        Returns a narrative hint string for the narrator.
        """
        loc = self.location
        if loc is None:
            return effects.narrative_hint

        for exit_name in effects.unlock_exits:
            if exit_name not in loc.unlocked_exits:
                loc.unlocked_exits.append(exit_name)
        for npc_name in effects.add_npcs:
            if npc_name not in loc.npcs_present:
                loc.npcs_present.append(npc_name)
        for item_name in effects.remove_items:
            if item_name in loc.items_available:
                loc.items_available.remove(item_name)
        for item_name in effects.add_items:
            if item_name not in loc.items_available:
                loc.items_available.append(item_name)
        loc.state_flags.update(effects.state_flags)

        return effects.narrative_hint

    async def _llm_beat_fallback(
        self,
        action: InterpretedAction,
        beat: "StoryBeat",
        outcome: MechanicsOutcome,
    ) -> dict:
        """Ask the 4b model if the player's creative action completes the beat.

        Returns {"completed": bool, "confidence": float}.
        Falls back to {"completed": False, "confidence": 0.0} on any error.
        """
        if self.interpreter is None:
            return {"completed": False, "confidence": 0.0}
        trigger_desc = ""
        if beat.completion_trigger:
            trigger_desc = f"{beat.completion_trigger.type} on \"{beat.completion_trigger.target}\""
        prompt = (
            f"Beat objective: \"{beat.description}\"\n"
            f"Expected trigger: {trigger_desc}\n"
            f"Player action: {action.action_type.value} on \"{action.target_name or 'nothing'}\"\n"
            f"Action summary: \"{outcome.summary}\"\n\n"
            f"Has the player achieved the beat objective through a creative approach?\n"
            f"Return JSON: {{\"completed\": true/false, \"confidence\": 0.0-1.0}}"
        )
        try:
            client = self.interpreter._client
            result = client.chat_json(
                "qwen3.5:4b",
                [
                    {"role": "system", "content": "You judge whether a player's action has completed a story beat objective. Respond with JSON only: {\"completed\": bool, \"confidence\": float}"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                think=False,
            )
            return {
                "completed": bool(result.get("completed", False)),
                "confidence": float(result.get("confidence", 0.0)),
            }
        except Exception:
            logger.warning(
                "BEAT LLM fallback failed campaign=%s", self.campaign_id,
                exc_info=True,
            )
            return {"completed": False, "confidence": 0.0}

    def _build_player_intent(self, action: InterpretedAction) -> str:
        """Concatenate raw input with any interpreter-extracted intent extras."""
        parts: list[str] = []
        if action.raw_input:
            parts.append(action.raw_input.strip())
        extras = []
        if action.search_detail:
            extras.append(f"search detail: {action.search_detail}")
        if action.talk_topic:
            extras.append(f"talk topic: {action.talk_topic}")
        if action.improvise_description:
            extras.append(f"improvise: {action.improvise_description}")
        if extras:
            parts.append("; ".join(extras))
        return " | ".join(parts)

    def _resolve_talk(self, action: InterpretedAction) -> MechanicsOutcome:
        """Run TALK through the NPC agent, persist state, build outcome."""
        from world.npc import DialogueExchange, NPCDisposition

        intent = self._build_player_intent(action)
        target = action.target_name or ""

        if (
            self.session is None
            or not target
            or target not in (self.session.npcs or {})
        ):
            return MechanicsOutcome(
                summary=f"{action.actor_name} approaches {target} to speak.",
                player_intent=intent,
            )

        npc = self.session.npcs[target]
        agent = getattr(self.session, "npc_agent", None)
        generator = getattr(self.session, "npc_generator", None)

        # Lazy canon generation when the NPC sheet is empty.
        if (
            generator is not None
            and callable(getattr(generator, "generate", None))
            and not (npc.personality or npc.description)
        ):
            try:
                location_ctx = ""
                if self.session.current_location is not None:
                    loc = self.session.current_location
                    location_ctx = f"{loc.name} — {loc.description}"
                campaign_theme = getattr(self.session.campaign, "name", "")
                sheet = generator.generate(
                    npc_name=npc.name,
                    location_context=location_ctx,
                    campaign_theme=campaign_theme,
                    language=self.language,
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
                self.session, actor_name=action.actor_name,
            )
        except Exception:
            agent_context = ""

        try:
            response = agent.respond(
                npc=npc,
                player_input=action.raw_input,
                context_prompt=agent_context,
                language=self.language,
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
        if self.db_factory is not None:
            try:
                from db.repositories.npc_repo import NPCRepository
                db_session = self.db_factory()
                try:
                    NPCRepository(db_session).update(npc, self.campaign_id)
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

    def _resolve_talk_in_combat(
        self, action: InterpretedAction,
    ) -> MechanicsOutcome:
        """Route a TALK action in combat to the TRUCE resolver.

        Runs :func:`bot.combat_truce.attempt_truce` which rolls the CHA
        check and, on success, marks every enemy as fled. The result is
        then forwarded to :func:`bot.combat_end.finalize_combat` with
        ``CombatEndReason.TRUCE`` so the encounter closes cleanly and the
        TurnManager picks up an idempotent summary next tick.

        The dice embed is queued on ``_pending_dice_embeds`` so the
        caller (ActionHandlerCog / TurnManager) can render the check in
        the existing dice embed infrastructure.
        """
        from bot.combat_truce import attempt_truce
        from engine.combat import CombatEndReason
        from engine.validators import _find_combatant

        intent = self._build_player_intent(action)
        assert self.combat_state is not None

        actor = _find_combatant(action.actor_name, self.combat_state)
        target = _find_combatant(
            action.target_name or "", self.combat_state,
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
            actor, target, self.combat_state,
        )

        if check is not None:
            # Queue the dice embed for the caller (task 60 rendering).
            self._pending_dice_embeds.append(
                ("truce_check", check, action.actor_name),
            )

        if succeeded and self.session is not None:
            # Finalise combat with TRUCE. finalize_combat is idempotent
            # so the TurnManager's post-advance_turn re-call is a no-op.
            from bot.combat_end import finalize_combat
            finalize_combat(self.session, CombatEndReason.TRUCE)

        return MechanicsOutcome(
            summary=summary_text,
            player_intent=intent,
            outcome_facts=summary_text,
        )

    def _resolve_pickup(self, action: InterpretedAction) -> str:
        """Move a scene item into the acting player's inventory (Lot G)."""
        item_name = action.target_name or action.item_name or ""
        if not item_name or self.session is None or self.db_factory is None:
            return f"{action.actor_name} reaches for something, but cannot grasp it."

        # Find the discord user_id for the acting character.
        user_id: int | None = None
        for uid, char in self.session.characters.items():
            if char.name == action.actor_name:
                user_id = uid
                break
        if user_id is None:
            return f"{action.actor_name} reaches for {item_name}, but cannot grasp it."

        from bot.scene_hydration import take_scene_item

        item = take_scene_item(
            self.session,
            item_name=item_name,
            user_id=user_id,
            db_factory=self.db_factory,
        )
        if item is None:
            return (
                f"{action.actor_name} reaches for '{item_name}', but it is not"
                f" here."
            )
        # Sync local references with the mutated session state.
        self.location = self.session.current_location
        self.inventory = self.session.inventories.get(user_id)
        return (
            f"{action.actor_name} picks up the {item_name} and stows it in"
            f" their pack."
        )

    def _assemble_context(self, action: InterpretedAction) -> str:
        """Build the narrator-facing context.

        Delegates to :func:`bot.scene_hydration.describe_scene_for_narrator`
        when a session is available; falls back to a minimal location-only
        snippet otherwise (used by unit tests that construct the pipeline
        without a full session).
        """
        if self.session is not None:
            from bot.scene_hydration import describe_scene_for_narrator
            return describe_scene_for_narrator(
                self.session, actor_name=action.actor_name,
            )

        loc = self.location
        lines: list[str] = []
        if loc is not None:
            lines.append(f"## Location\n{loc.name}\n{loc.description}")
        lines.append(f"## Acting character\n{action.actor_name}")
        return "\n\n".join(lines)

    async def _narrate_unknown(
        self,
        action: InterpretedAction,
        resolution: ResolutionResult,
    ) -> NarrativeResult:
        """Narrate an in-character refusal, grounded in the real scene.

        The narrator receives the actual ``npcs_present`` and ``connections``
        from the current location, plus an explicit no-hallucination clause,
        so it can suggest a real reformulation instead of inventing entities
        (Lot A — scene awareness).
        """
        loc = self.location
        loc_name = loc.name if loc is not None else "ce lieu"

        if loc is not None and loc.npcs_present:
            npcs_line = ", ".join(loc.npcs_present[:8])
        else:
            npcs_line = "aucun"

        if loc is not None and loc.connections:
            exits_line = ", ".join(loc.connections)
        else:
            exits_line = "aucune"

        if loc is not None and loc.items_available:
            items_line = ", ".join(loc.items_available[:8])
        else:
            items_line = "aucun"

        verb = action.action_type.value.lower()
        raw = resolution.raw_value or "cette cible"

        action_summary = (
            f"{action.actor_name} a tenté de {verb} '{raw}', "
            f"mais cette cible n'existe pas à {loc_name}.\n\n"
            f"Personnages réellement présents : {npcs_line}\n"
            f"Objets disponibles : {items_line}\n"
            f"Sorties réelles : {exits_line}\n\n"
            "Décris en UN court paragraphe la réalisation du personnage et "
            "propose-lui de reformuler en mentionnant un de ces "
            "personnages/objets/sorties s'il y en a. "
            "**N'invente AUCUN autre personnage, lieu ou objet.** "
            "Reste strictement dans le monde décrit ci-dessus."
        )
        context = self._assemble_context(action)
        return await self._call_narrator(
            outcome=MechanicsOutcome(summary=action_summary),
            context_prompt=context,
        )

    async def _narrate_rule_failure(
        self,
        action: InterpretedAction,
        validation: ValidationResult,
    ) -> NarrativeResult:
        """Narrate an in-character refusal when the rules forbid the action.

        Like ``_narrate_unknown``, the narrator receives the real scene
        context (npcs_present, connections) so its hesitation paragraph
        stays grounded in the world (Lot A — scene awareness).
        """
        loc = self.location
        loc_name = loc.name if loc is not None else "ce lieu"
        npcs_line = (
            ", ".join(loc.npcs_present[:8])
            if (loc is not None and loc.npcs_present)
            else "aucun"
        )
        exits_line = (
            ", ".join(loc.connections)
            if (loc is not None and loc.connections)
            else "aucune"
        )

        verb = action.action_type.value.lower()
        action_summary = (
            f"{action.actor_name} a tenté de {verb}, mais les règles "
            f"l'interdisent : {validation.error_message}.\n\n"
            f"Lieu : {loc_name}\n"
            f"Personnages présents : {npcs_line}\n"
            f"Sorties : {exits_line}\n\n"
            "Décris en UN court paragraphe l'hésitation du personnage, "
            "en t'appuyant uniquement sur les éléments ci-dessus. "
            "**N'invente AUCUN autre personnage, lieu ou objet.**"
        )
        context = self._assemble_context(action)
        return await self._call_narrator(
            outcome=MechanicsOutcome(summary=action_summary),
            context_prompt=context,
        )

    async def _emit(
        self,
        cb: ProgressCallback | None,
        phase: PipelinePhase,
    ) -> None:
        if cb is None:
            return
        try:
            await cb(phase)
        except Exception:
            logger.warning(
                "ACTION callback failed campaign=%s phase=%s",
                self.campaign_id, phase.name, exc_info=True,
            )
