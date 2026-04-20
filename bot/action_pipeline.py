"""Backward-compatible Facade for the action pipeline.

The actual implementation lives in :mod:`bot.pipeline.orchestrator`. This
module exists to preserve imports of the form
``from bot.action_pipeline import ActionPipeline`` that exist throughout the
codebase (cogs, views, tests).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from bot.pipeline.orchestrator import (
    ActionPipelineResult,
    AmbiguityResult,
    PipelineOutput,
    PipelinePhase,
    PipelineRunner,
    ProgressCallback,
    UnknownEntityResult,
    _assign_initial_zones,
    _persist_story_arc,
)
from bot.pipeline.resolve import (
    DEFENSIVE_CONDITIONS,
    TRIVIAL_RESOLVE_AC_THRESHOLD,
    TRIVIAL_RESOLVE_HP_THRESHOLD,
    is_trivially_defeatable,
)

if TYPE_CHECKING:
    from ai.interpreter import Interpreter
    from ai.models import InterpretedAction
    from ai.narrator import Narrator
    from engine.combat import CombatState
    from engine.inventory import Inventory
    from world.location import Location
    from world.npc import NPC

    from bot.game_session import GameSession


__all__ = [
    "ActionPipeline",
    "ActionPipelineResult",
    "AmbiguityResult",
    "DEFENSIVE_CONDITIONS",
    "PipelineOutput",
    "PipelinePhase",
    "ProgressCallback",
    "TRIVIAL_RESOLVE_AC_THRESHOLD",
    "TRIVIAL_RESOLVE_HP_THRESHOLD",
    "UnknownEntityResult",
    "_assign_initial_zones",
    "_persist_story_arc",
    "is_trivially_defeatable",
]


class ActionPipeline:
    """Legacy facade — instantiates a ``PipelineRunner`` per action.

    Preserves the historical signature and the three public methods.
    """

    def __init__(
        self,
        *,
        interpreter: "Interpreter",
        narrator: "Narrator",
        location: "Location | None" = None,
        npcs: "dict[str, NPC] | None" = None,
        actor_name: str,
        language: str = "fr",
        campaign_id: str = "",
        combat_state: "CombatState | None" = None,
        inventory: "Inventory | None" = None,
        session: "GameSession | None" = None,
        db_factory: "Callable[[], Any] | None" = None,
        semantic_indexer: Any = None,
        force_director_run: bool = False,
    ) -> None:
        self._runner = PipelineRunner(
            interpreter=interpreter,
            narrator=narrator,
            location=location,
            npcs=npcs if npcs is not None else {},
            actor_name=actor_name,
            language=language,
            campaign_id=campaign_id,
            combat_state=combat_state,
            inventory=inventory,
            session=session,
            db_factory=db_factory,
            semantic_indexer=semantic_indexer,
            force_director_run=force_director_run,
        )

    def _sync_overrides_to_runner(self) -> None:
        """Propagate any instance-level overrides from the facade to the runner.

        When tests (or other callers) use ``patch.object(pipeline, "_llm_beat_fallback",
        mock_fn)``, Python sets the attribute directly on the facade instance, shadowing
        the method descriptor. We copy it to the runner so the runner's
        ``_continue_from_resolution`` sees the same override.
        """
        override = self.__dict__.get("_llm_beat_fallback")
        if override is not None:
            self._runner._llm_beat_fallback = override  # type: ignore[method-assign]
        elif "_llm_beat_fallback" in self._runner.__dict__:
            # Clear any previously propagated override (e.g. after the patch context exits).
            del self._runner.__dict__["_llm_beat_fallback"]

    async def process(
        self, player_text: str, progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        """Run the full pipeline for a fresh player action."""
        self._sync_overrides_to_runner()
        return await self._runner.process(player_text, progress_callback)

    async def resume_with_resolution(
        self, ambiguity: AmbiguityResult, chosen_entity_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        """Continue a paused pipeline after the user picked a candidate."""
        self._sync_overrides_to_runner()
        return await self._runner.resume_with_resolution(
            ambiguity, chosen_entity_id, progress_callback,
        )

    async def process_interpreted_action(
        self, action: "InterpretedAction",
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        """Run the pipeline from a pre-built InterpretedAction."""
        self._sync_overrides_to_runner()
        return await self._runner.process_interpreted_action(action, progress_callback)

    # --- Passthrough properties for callers that read side-channel state ---
    @property
    def _pending_combat_start_embed(self) -> Any:
        return self._runner._pending_combat_start_embed

    @_pending_combat_start_embed.setter
    def _pending_combat_start_embed(self, value: Any) -> None:
        self._runner._pending_combat_start_embed = value

    @property
    def _pending_dice_embeds(self) -> list[Any]:
        return self._runner._pending_dice_embeds

    @property
    def _pending_flee_destination(self) -> str | None:
        return self._runner._pending_flee_destination

    @_pending_flee_destination.setter
    def _pending_flee_destination(self, value: str | None) -> None:
        self._runner._pending_flee_destination = value

    @property
    def _trivial_kill_mechanics(self) -> str | None:
        return self._runner._trivial_kill_mechanics

    # --- Passthrough properties for state that callers may read/write ---
    @property
    def location(self) -> "Location | None":
        return self._runner.location

    @property
    def npcs(self) -> "dict[str, NPC]":
        return self._runner.npcs

    @property
    def session(self) -> "GameSession | None":
        return self._runner.session

    @session.setter
    def session(self, value: "GameSession | None") -> None:
        self._runner.session = value

    @property
    def combat_state(self) -> "CombatState | None":
        return self._runner.combat_state

    @combat_state.setter
    def combat_state(self, value: "CombatState | None") -> None:
        self._runner.combat_state = value

    @property
    def inventory(self) -> "Inventory | None":
        return self._runner.inventory

    @inventory.setter
    def inventory(self, value: "Inventory | None") -> None:
        self._runner.inventory = value

    @property
    def actor_name(self) -> str:
        return self._runner.actor_name

    @property
    def interpreter(self) -> "Interpreter":
        return self._runner.interpreter

    @property
    def narrator(self) -> "Narrator":
        return self._runner.narrator

    @property
    def language(self) -> str:
        return self._runner.language

    @property
    def campaign_id(self) -> str:
        return self._runner.campaign_id

    @property
    def db_factory(self) -> "Callable[[], Any] | None":
        return self._runner.db_factory

    @db_factory.setter
    def db_factory(self, value: "Callable[[], Any] | None") -> None:
        self._runner.db_factory = value

    # --- Compat shims used by some tests directly on ActionPipeline ---

    @staticmethod
    def _auto_resolve_weapon_name(
        weapon_name: "str | None",
        inventory: "Inventory | None",
    ) -> "str | None":
        """Delegate to bot.pipeline.interpret.auto_resolve_weapon_name."""
        from bot.pipeline import interpret
        return interpret.auto_resolve_weapon_name(
            weapon_name=weapon_name,
            inventory=inventory,
        )

    def _validate(self, action: "InterpretedAction") -> Any:
        """Delegate to bot.pipeline.interpret.validate (with side-channel sync)."""
        from bot.pipeline import interpret
        side = interpret.InterpretSideChannel(
            pending_flee_destination=self._runner._pending_flee_destination,
            pending_combat_start_embed=self._runner._pending_combat_start_embed,
            trivial_kill_mechanics=self._runner._trivial_kill_mechanics,
            pending_dice_embeds=list(self._runner._pending_dice_embeds),
        )
        result = interpret.validate(
            action=action,
            actor_name=self.actor_name,
            location=self.location,
            npcs=self.npcs,
            combat_state=self.combat_state,
            inventory=self.inventory,
            session=self.session,
            campaign_id=self.campaign_id,
            db_factory=self.db_factory,
            side=side,
        )
        self._runner._pending_flee_destination = side.pending_flee_destination
        self._runner._pending_combat_start_embed = side.pending_combat_start_embed
        self._runner._trivial_kill_mechanics = side.trivial_kill_mechanics
        self._runner._pending_dice_embeds = side.pending_dice_embeds
        if side.pending_combat_start_embed is not None:
            self._runner.combat_state = side.pending_combat_start_embed[0]
        return result

    async def _resolve_mechanics(self, action: "InterpretedAction") -> Any:
        """Delegate to bot.pipeline.resolve.resolve_mechanics (with side-channel sync)."""
        from bot.pipeline import resolve as _resolve_mod
        side = _resolve_mod.ResolveSideChannel(
            pending_flee_destination=self._runner._pending_flee_destination,
            pending_dice_embeds=list(self._runner._pending_dice_embeds),
            trivial_kill_mechanics=self._runner._trivial_kill_mechanics,
        )
        outcome = await _resolve_mod.resolve_mechanics(
            action=action,
            actor_name=self.actor_name,
            location=self.location,
            npcs=self.npcs,
            combat_state=self.combat_state,
            inventory=self.inventory,
            session=self.session,
            campaign_id=self.campaign_id,
            db_factory=self.db_factory,
            side=side,
        )
        self._runner._pending_flee_destination = side.pending_flee_destination
        self._runner._pending_dice_embeds = side.pending_dice_embeds
        self._runner._trivial_kill_mechanics = side.trivial_kill_mechanics
        return outcome

    def _assemble_context(
        self,
        action: "InterpretedAction",
        *,
        current_outcome_summary: "str | None" = None,
        ongoing_dialogue_with: "str | None" = None,
    ) -> str:
        """Delegate to bot.pipeline.narrate.assemble_context."""
        from bot.pipeline import narrate
        return narrate.assemble_context(
            action=action,
            actor_name=self.actor_name,
            location=self.location,
            npcs=self.npcs,
            session=self.session,
            combat_state=self.combat_state,
            inventory=self.inventory,
            campaign_id=self.campaign_id,
            current_outcome_summary=current_outcome_summary,
            ongoing_dialogue_with=ongoing_dialogue_with,
        )

    async def _llm_beat_fallback(
        self,
        action: "InterpretedAction",
        beat: Any,
        outcome: Any,
    ) -> dict:
        """Delegate to PipelineRunner._llm_beat_fallback."""
        return await self._runner._llm_beat_fallback(action, beat, outcome)

    def _check_beat_completion(
        self,
        action: "InterpretedAction",
        outcome: Any = None,
    ) -> bool:
        """Delegate to PipelineRunner._check_beat_completion."""
        return self._runner._check_beat_completion(action, outcome)

    def _apply_beat_effects(self, effects: Any) -> str:
        """Delegate to PipelineRunner._apply_beat_effects."""
        return self._runner._apply_beat_effects(effects)

    def _should_trivial_resolve(self, npc: "NPC") -> bool:
        """Delegate to bot.pipeline.resolve.should_trivial_resolve."""
        from bot.pipeline import resolve as _resolve_mod
        return _resolve_mod.should_trivial_resolve(
            npc=npc, session=self.session, campaign_id=self.campaign_id,
        )

    def _trivial_kill(self, target_npc: "NPC") -> None:
        """Delegate to bot.pipeline.resolve.trivial_kill."""
        from bot.pipeline import resolve as _resolve_mod
        side = _resolve_mod.ResolveSideChannel(
            pending_flee_destination=self._runner._pending_flee_destination,
            pending_dice_embeds=list(self._runner._pending_dice_embeds),
            trivial_kill_mechanics=self._runner._trivial_kill_mechanics,
        )
        _resolve_mod.trivial_kill(
            target_npc=target_npc,
            actor_name=self.actor_name,
            location=self.location,
            npcs=self.npcs,
            session=self.session,
            campaign_id=self.campaign_id,
            db_factory=self.db_factory,
            side=side,
        )
        self._runner._pending_flee_destination = side.pending_flee_destination
        self._runner._pending_dice_embeds = side.pending_dice_embeds
        self._runner._trivial_kill_mechanics = side.trivial_kill_mechanics

    async def _resolve_flee(self, action: "InterpretedAction") -> Any:
        """Delegate to bot.pipeline.resolve.resolve_flee."""
        from bot.pipeline import resolve as _resolve_mod
        side = _resolve_mod.ResolveSideChannel(
            pending_flee_destination=self._runner._pending_flee_destination,
            pending_dice_embeds=list(self._runner._pending_dice_embeds),
            trivial_kill_mechanics=self._runner._trivial_kill_mechanics,
        )
        outcome = await _resolve_mod.resolve_flee(
            action=action,
            actor_name=self.actor_name,
            location=self.location,
            combat_state=self.combat_state,
            session=self.session,
            db_factory=self.db_factory,
            side=side,
        )
        self._runner._pending_flee_destination = side.pending_flee_destination
        self._runner._pending_dice_embeds = side.pending_dice_embeds
        self._runner._trivial_kill_mechanics = side.trivial_kill_mechanics
        return outcome
