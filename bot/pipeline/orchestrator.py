"""Pipeline orchestrator — owns the 6-step flow logic.

The :class:`PipelineRunner` is a dataclass holding all per-action state and
implements the three public methods (``process``, ``resume_with_resolution``,
``process_interpreted_action``) plus their private helpers.

The legacy :class:`~bot.action_pipeline.ActionPipeline` facade in
``bot.action_pipeline`` wraps a ``PipelineRunner`` and forwards calls to it,
preserving backward compatibility.

Phases (also reported via the optional ``progress_callback``):

1. ``INTERPRETING``        — LLM classifies the player's intent (Interpreter)
2. ``RESOLVING_ENTITIES``  — pure-Python entity resolution (EntityResolver)
3. ``VALIDATING``          — pure-Python rule check (validators.py)
4. ``RESOLVING_ACTION``    — engine modules apply mechanical effects
5. ``ASSEMBLING_CONTEXT``  — build a small in-memory context for the narrator
6. ``NARRATING``           — LLM produces immersive prose (Narrator)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ai.entity_resolver import EntityCandidate, EntityResolver
from ai.interpreter import Interpreter
from ai.models import (
    InterpretedAction,
    PublicEffects,
)
from ai.narrator import Narrator
from ai.scene_context import build_scene_context
from bot.persistence import persist_session
from engine.combat import (
    CombatSide,
    CombatState,
    record_combat_event,
)
from bot.combat_entry import CombatTrigger
from engine.inventory import Inventory
from engine.validators import (
    ActionType,
)
from world.location import Location
from world.npc import NPC
from world.story_arc import BeatEffects, StoryBeat
from bot.pipeline.drift_tracker import Decision as DriftDecision, DriftTracker

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Drift tracking + Story Director cadence
# ---------------------------------------------------------------------------

_DRIFT_TRACKER = DriftTracker()


def get_drift_tracker() -> DriftTracker:
    """Module-level singleton DriftTracker.

    Tests reset state per-campaign via ``tracker.reset(campaign_id)``.
    """
    return _DRIFT_TRACKER


def should_run_director(
    *,
    interaction_count: int,
    combat_just_ended: bool,
    drift_detected: bool,
    force: bool,
) -> bool:
    """Decide whether the Story Director should run after this turn.

    Triggers (any one is sufficient):
    - ``force`` — caller explicitly requested (e.g. ``/story_catch_up``)
    - ``drift_detected`` — DriftTracker reports a stale narrator
    - ``combat_just_ended`` — the previous turn ended a combat
    - ``interaction_count`` is a positive multiple of 6
    """
    if force:
        return True
    if drift_detected:
        return True
    if combat_just_ended:
        return True
    if interaction_count > 0 and interaction_count % 6 == 0:
        return True
    return False


# ---------------------------------------------------------------------------
# Module-level helpers
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


# ---------------------------------------------------------------------------
# PipelineRunner
# ---------------------------------------------------------------------------


@dataclass
class PipelineRunner:
    """Holds all per-action state and implements the 6-step pipeline flow.

    A new instance is created for each player message via the
    :class:`~bot.action_pipeline.ActionPipeline` facade.
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
    force_director_run: bool = False
    """When True, the next pipeline run unconditionally schedules the Story Director."""
    semantic_indexer: Any = None
    """Optional SemanticIndexer — when set, beat completion indexes revealed facts."""

    beat_judge: Any = field(default=None, init=False)
    """BeatJudge instance, lazily wired when ollama_client is available on session."""

    _trivial_kill_mechanics: str | None = field(default=None, init=False)
    _last_combat_active: bool = field(default=False, init=False)

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

    def __post_init__(self) -> None:
        """Wire BeatJudge when an ollama_client is available on session."""
        _ollama = getattr(self.session, "ollama_client", None) if self.session is not None else None
        if _ollama is not None:
            from ai.beat_judge import BeatJudge
            self.beat_judge = BeatJudge(_ollama)

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
        from bot.pipeline import interpret
        interpreted = await interpret.call_interpreter(
            interpreter=self.interpreter,
            player_text=player_text,
            scene=scene,
            actor_name=self.actor_name,
            language=self.language,
        )

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
        from bot.pipeline import interpret, narrate
        from bot.pipeline import resolve as _resolve_mod

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
            refusal = await narrate.narrate_unknown(
                narrator=self.narrator,
                action=interpreted,
                resolution=resolution,
                actor_name=self.actor_name,
                location=self.location,
                language=self.language,
                campaign_id=self.campaign_id,
                session=self.session,
            )
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
            resolved_weapon = interpret.auto_resolve_weapon_name(
                weapon_name=interpreted.weapon_name,
                inventory=self.inventory,
            )
            if resolved_weapon != interpreted.weapon_name:
                interpreted = interpreted.model_copy(
                    update={"weapon_name": resolved_weapon},
                )

        await self._emit(progress_callback, PipelinePhase.VALIDATING)
        # Build InterpretSideChannel, call validate, copy back mutations.
        side_interp = interpret.InterpretSideChannel(
            pending_flee_destination=self._pending_flee_destination,
            pending_combat_start_embed=self._pending_combat_start_embed,
            trivial_kill_mechanics=self._trivial_kill_mechanics,
            pending_dice_embeds=list(self._pending_dice_embeds),
        )
        validation = interpret.validate(
            action=interpreted,
            actor_name=self.actor_name,
            location=self.location,
            npcs=self.npcs,
            combat_state=self.combat_state,
            inventory=self.inventory,
            session=self.session,
            campaign_id=self.campaign_id,
            db_factory=self.db_factory,
            side=side_interp,
        )
        self._pending_flee_destination = side_interp.pending_flee_destination
        self._pending_combat_start_embed = side_interp.pending_combat_start_embed
        self._trivial_kill_mechanics = side_interp.trivial_kill_mechanics
        self._pending_dice_embeds = side_interp.pending_dice_embeds
        if side_interp.pending_combat_start_embed is not None:
            self.combat_state = side_interp.pending_combat_start_embed[0]

        if not validation.is_valid:
            refusal = await narrate.narrate_rule_failure(
                narrator=self.narrator,
                action=interpreted,
                validation=validation,
                actor_name=self.actor_name,
                location=self.location,
                language=self.language,
                campaign_id=self.campaign_id,
                session=self.session,
            )
            return UnknownEntityResult(
                field_name="rule",
                raw_value=validation.error_message or "",
                partial_action=interpreted,
                refusal_narrative=refusal.narrative,
                tone=refusal.tone,
            )

        await self._emit(progress_callback, PipelinePhase.RESOLVING_ACTION)
        # Build ResolveSideChannel, call resolve_mechanics, copy back mutations.
        side_res = _resolve_mod.ResolveSideChannel(
            pending_flee_destination=self._pending_flee_destination,
            pending_dice_embeds=list(self._pending_dice_embeds),
            trivial_kill_mechanics=self._trivial_kill_mechanics,
        )
        outcome = await _resolve_mod.resolve_mechanics(
            action=interpreted,
            actor_name=self.actor_name,
            location=self.location,
            npcs=self.npcs,
            combat_state=self.combat_state,
            inventory=self.inventory,
            session=self.session,
            campaign_id=self.campaign_id,
            db_factory=self.db_factory,
            side=side_res,
        )
        self._pending_flee_destination = side_res.pending_flee_destination
        self._pending_dice_embeds = side_res.pending_dice_embeds
        self._trivial_kill_mechanics = side_res.trivial_kill_mechanics

        # MOVE syncs live location / npc dict from session after navigation.
        if (
            interpreted.action_type == ActionType.MOVE
            and outcome.public_effects is not None
            and outcome.public_effects.location_change is not None
            and self.session is not None
        ):
            self.location = self.session.current_location
            self.npcs = self.session.npcs

        # PICKUP syncs location + inventory refs after item transfer.
        if (
            interpreted.action_type == ActionType.PICKUP
            and "picks up" in outcome.summary
            and self.session is not None
        ):
            self.location = self.session.current_location
            for uid, char in self.session.characters.items():
                if char.name == self.actor_name:
                    self.inventory = self.session.inventories.get(uid)
                    break

        # Record a short narration hint for the narrator context.
        # Only in active combat: the narrator reads the tail of this list from
        # the COMBAT ACTIVE section of the scene prompt. The engine never touches
        # the list; the cap is enforced by ``record_combat_event`` itself.
        if self.combat_state is not None and self.combat_state.is_active:
            event_text = outcome.summary.strip()
            if event_text:
                record_combat_event(self.combat_state, event_text)

        # ----- Beat progression — single decision point -----
        new_beat: StoryBeat | None = None
        beat_completed = False
        engine_decision: DriftDecision = "STAY"
        beat_progress_snapshot: Any = None  # BeatProgress | None
        if self.session is not None and getattr(self.session, "story_arc", None) is not None:
            from typing import Any as _Any
            from engine.beat_progression import (
                BeatHistory,
                BeatProgressionEngine,
            )
            from world.story_arc import StoryArc as _StoryArc

            arc: _StoryArc = self.session.story_arc  # type: ignore[assignment]

            inventory_items: set[str] = set()
            if self.inventory is not None:
                inventory_items = {it.name for it in self.inventory.items}
            world_flags: dict[str, _Any] = {}
            if self.location is not None:
                world_flags = dict(self.location.state_flags)

            engine = BeatProgressionEngine()
            beat_eval = engine.evaluate(
                arc=arc,
                interpreted=interpreted,
                outcome=outcome,
                location=self.location,
                history=BeatHistory(),
                world_flags=world_flags,
                inventory=inventory_items,
            )

            engine_decision = beat_eval.decision
            beat_progress_snapshot = beat_eval.progress
            should_advance = beat_eval.decision == "ADVANCE"

            # If the engine asks for a judge, fire BeatJudge and re-decide.
            if beat_eval.decision == "NEEDS_JUDGE" and getattr(self, "beat_judge", None) is not None:
                judge = self.beat_judge
                if judge is not None and beat_eval.judge_request is not None:
                    judge.begin_turn(turn_id=str(id(interpreted)))
                    judge_resp = judge.evaluate(beat_eval.judge_request)
                    if judge_resp.passed and judge_resp.confidence >= 0.7:
                        should_advance = True
                        logger.info(
                            "BEAT advance via judge campaign=%s confidence=%.2f reasoning=%r",
                            self.campaign_id, judge_resp.confidence, judge_resp.reasoning,
                        )
                    else:
                        logger.info(
                            "BEAT judge declined campaign=%s passed=%s confidence=%.2f reasoning=%r",
                            self.campaign_id, judge_resp.passed, judge_resp.confidence, judge_resp.reasoning,
                        )

            # Production telemetry — one JSON line per evaluate() call.
            try:
                from engine.beat_progression import log_decision
                _beat_num = (
                    arc.beats[arc.current_beat_index].beat_number
                    if arc.current_beat_index < len(arc.beats)
                    else arc.beats[-1].beat_number
                )
                _judge_passed: bool | None = None
                _judge_confidence: float | None = None
                if beat_eval.decision == "NEEDS_JUDGE" and "judge_resp" in locals():
                    _judge_passed = judge_resp.passed  # type: ignore[name-defined]
                    _judge_confidence = judge_resp.confidence  # type: ignore[name-defined]
                log_decision(
                    campaign_id=self.campaign_id,
                    beat_number=_beat_num,
                    result=beat_eval,
                    judge_passed=_judge_passed,
                    judge_confidence=_judge_confidence,
                )
            except Exception:
                logger.debug("log_decision failed campaign=%s", self.campaign_id, exc_info=True)

            if should_advance:
                beat_completed = True
                old_beat = arc.beats[arc.current_beat_index]
                hint = self._apply_beat_effects(old_beat.on_complete)
                if hint:
                    outcome = outcome.model_copy(update={
                        "outcome_facts": (outcome.outcome_facts + " " + hint).strip(),
                    })
                from world.story_arc import advance_beat
                advanced_arc = advance_beat(arc)
                self.session.story_arc = advanced_arc
                # Reset /hint usage for the now-completed beat.
                if self.db_factory is not None:
                    try:
                        from db.repositories.hint_usage_repo import HintUsageRepository
                        _hint_db = self.db_factory()
                        try:
                            HintUsageRepository(_hint_db).clear_for_beat(
                                campaign_id=self.campaign_id,
                                beat_number=old_beat.beat_number,
                            )
                        finally:
                            _hint_db.close()
                    except Exception:
                        logger.exception("HINT cleanup failed campaign=%s", self.campaign_id)
                if advanced_arc.current_beat_index < len(advanced_arc.beats):
                    new_beat = advanced_arc.beats[advanced_arc.current_beat_index]
                else:
                    new_beat = None
                logger.info(
                    "BEAT advance campaign=%s to=%d title=%r reasons=%s",
                    self.campaign_id,
                    advanced_arc.current_beat_index,
                    new_beat.title if new_beat else "—",
                    beat_eval.reasons,
                )

        await self._emit(progress_callback, PipelinePhase.ASSEMBLING_CONTEXT)
        # Detect a Talk-action continuation: outcome.npc_dialogue is only set
        # by _resolve_talk, and outcome.npc_name is the NPC the player is
        # mid-conversation with. The scene builder will only switch to the
        # ongoing-dialogue layout if the NPC's history has a prior exchange.
        ongoing_dialogue_with = (
            outcome.npc_name
            if outcome.npc_dialogue is not None
            else None
        )
        context_prompt = narrate.assemble_context(
            action=interpreted,
            actor_name=self.actor_name,
            location=self.location,
            npcs=self.npcs,
            session=self.session,
            combat_state=self.combat_state,
            inventory=self.inventory,
            campaign_id=self.campaign_id,
            current_outcome_summary=outcome.summary,
            ongoing_dialogue_with=ongoing_dialogue_with,
        )

        await self._emit(progress_callback, PipelinePhase.NARRATING)
        from ai.story_director import cached_note_for
        director_note = cached_note_for(self.campaign_id)
        narration = await narrate.call_narrator(
            narrator=self.narrator,
            outcome=outcome,
            context_prompt=context_prompt,
            language=self.language,
            campaign_id=self.campaign_id,
            director_note=director_note,
        )

        # --- Drift tracking + Story Director scheduling ---
        tracker = get_drift_tracker()
        tracker.record(self.campaign_id, decision=engine_decision)

        combat_active_now = (
            self.combat_state is not None and self.combat_state.is_active
        )
        combat_just_ended = self._last_combat_active and not combat_active_now
        self._last_combat_active = combat_active_now

        # The turn counter lives on the Campaign model (incremented by
        # StoryBibleLogger), NOT on GameSession.
        _campaign = getattr(self.session, "campaign", None)
        interaction_count = getattr(_campaign, "interaction_count", 0) or 0

        if should_run_director(
            interaction_count=interaction_count,
            combat_just_ended=combat_just_ended,
            drift_detected=tracker.is_drifting(self.campaign_id),
            force=self.force_director_run,
        ):
            self.force_director_run = False  # consume the flag
            self._schedule_story_director(
                context_prompt=context_prompt,
                beat_progress=beat_progress_snapshot,
            )

        # Persist the arc if it advanced.
        if beat_completed and self.db_factory is not None and self.session is not None:
            session_arc = self.session.story_arc
            try:
                await asyncio.to_thread(
                    _persist_story_arc, self.db_factory, session_arc,
                )
            except Exception:
                logger.exception("BEAT persist failed campaign=%s", self.campaign_id)
        # ----- end beat progression -----

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

    def _apply_beat_effects(self, effects: BeatEffects) -> str:
        """Apply beat completion effects to the current location.

        Returns a narrative hint string for the narrator.
        """
        # Index revealed facts regardless of whether a location exists.
        if self.semantic_indexer is not None and self.campaign_id:
            if effects.narrative_hint:
                self.semantic_indexer.index_revealed_fact(
                    self.campaign_id, fact=effects.narrative_hint,
                )
            for flag, value in effects.state_flags.items():
                if value:
                    self.semantic_indexer.index_revealed_fact(
                        self.campaign_id,
                        fact=f"State flag set: {flag}",
                    )

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

    def _schedule_story_director(
        self,
        *,
        context_prompt: str,
        beat_progress: Any = None,  # BeatProgress | None — forward ref, avoid import cycle
    ) -> None:
        """Fire-and-forget Story Director run. Result lands in semantic memory.

        Uses ``self.narrator._client`` (existing pattern in this codebase) for
        the OllamaClient. The Story Director is sync; we run it via
        ``asyncio.to_thread`` to avoid blocking the event loop.

        Args:
            context_prompt: Assembled scene context for the director.
            beat_progress: Optional BeatProgress snapshot from the engine.
                Forwarded to check_coherence so the director sees authoritative
                beat state rather than inferring it from narrative text.
        """
        from ai.story_director import StoryDirector
        from memory.semantic import SemanticMemory

        async def _run() -> None:
            try:
                semantic = SemanticMemory()
                director = StoryDirector(self.narrator._client, semantic)
                await asyncio.to_thread(
                    director.check_coherence,
                    self.campaign_id,
                    context_prompt,
                    beat_progress,
                )
            except Exception:
                logger.warning(
                    "Background StoryDirector run failed for campaign=%s",
                    self.campaign_id, exc_info=True,
                )

        try:
            asyncio.create_task(_run())
        except RuntimeError:
            # No running event loop (e.g. during sync tests). Skip — drift
            # tracking still works for the next loop turn.
            logger.debug("No event loop, skipping Story Director schedule")

    async def _emit(
        self,
        cb: ProgressCallback | None,
        phase: PipelinePhase,
    ) -> None:
        """Fire the progress callback, swallowing exceptions."""
        if cb is None:
            return
        try:
            await cb(phase)
        except Exception:
            logger.warning(
                "ACTION callback failed campaign=%s phase=%s",
                self.campaign_id, phase.name, exc_info=True,
            )
