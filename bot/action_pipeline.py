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
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ai.entity_resolver import EntityCandidate, EntityResolver, ResolutionResult
from ai.interpreter import Interpreter
from ai.models import InterpretedAction, NarrativeResult
from ai.narrator import Narrator
from ai.scene_context import SceneContext, build_scene_context
from bot.llm_retry import retry_llm_call
from engine.character import Character
from engine.combat import CombatState, TrivialResolveResult, trivial_resolve
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
from world.npc import NPC, NPCDisposition
from world.story_arc import StoryBeat

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


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
    interpreted_action: InterpretedAction
    new_beat: StoryBeat | None = None


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
            interpreted = interpreted.model_copy(
                update={"target_name": resolution.resolved_entity},
            )
        elif (
            resolution.status == "resolved"
            and resolution.field_name == "item_name"
            and resolution.resolved_entity is not None
        ):
            interpreted = interpreted.model_copy(
                update={"item_name": resolution.resolved_entity},
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
        mechanics_text = await self._resolve_mechanics(interpreted)

        await self._emit(progress_callback, PipelinePhase.ASSEMBLING_CONTEXT)
        context_prompt = self._assemble_context(interpreted)

        await self._emit(progress_callback, PipelinePhase.NARRATING)
        narration = await self._call_narrator(
            mechanics_text=mechanics_text,
            context_prompt=context_prompt,
        )

        # Lot D — beat advancement check after every resolved action.
        new_beat: StoryBeat | None = None
        if self.session is not None and hasattr(
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

        await self._emit(progress_callback, PipelinePhase.DONE)
        return ActionPipelineResult(
            narrative=narration.narrative,
            tone=narration.tone,
            mechanics_text=mechanics_text,
            interpreted_action=interpreted,
            new_beat=new_beat,
        )

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
        mechanics_text: str,
        context_prompt: str,
    ) -> NarrativeResult:
        def _do() -> NarrativeResult:
            return self.narrator.narrate(
                action_result_text=mechanics_text,
                context_prompt=context_prompt,
                language=self.language,
            )

        return await retry_llm_call(
            _do,
            log_label=f"ACTION campaign={self.campaign_id} narrate",
        )

    def _validate(self, action: InterpretedAction) -> ValidationResult:
        """Convert InterpretedAction → Action and dispatch to the right validator."""
        eng_action = Action(
            actor_name=action.actor_name,
            action_type=action.action_type,
            target_name=action.target_name,
            weapon_name=action.weapon_name,
            spell_name=action.spell_name,
            item_name=action.item_name,
        )
        if action.action_type in EXPLORATION_ACTION_TYPES:
            return validate_exploration_action(eng_action)

        # Lot C: bootstrap a combat encounter when a player attacks an NPC
        # who is present in the scene but not yet in any combat state.
        if (
            action.action_type == ActionType.ATTACK
            and self.combat_state is None
            and action.target_name is not None
            and action.target_name in self.npcs
            and self.session is not None
        ):
            target_npc = self.npcs[action.target_name]
            if self._should_trivial_resolve(target_npc):
                self._trivial_kill(target_npc)
                # Skip the combat-state validation below — the action is
                # already fully resolved.
                return ValidationResult(is_valid=True)
            self.combat_state = self._bootstrap_combat_against(target_npc)
            self.session.combat_state = self.combat_state
            logger.info(
                "COMBAT bootstrapped from_action campaign=%s attacker=%s target=%s",
                self.campaign_id, self.actor_name, target_npc.name,
            )

        if self.combat_state is None:
            return ValidationResult(
                is_valid=False,
                error_message=(
                    f"'{action.action_type.value}' requires combat but no combat state"
                ),
            )
        return validate_action(eng_action, self.combat_state)

    def _should_trivial_resolve(self, npc: NPC) -> bool:
        """Decide whether an attack on ``npc`` skips the combat round system.

        Trivial resolution applies to peaceful, defenseless NPCs that an
        adventurer would obviously overpower in one swing. We deliberately
        exclude HOSTILE / UNFRIENDLY NPCs (they fight back) and anything
        with non-trivial HP.
        """
        if not npc.is_alive:
            return False
        if npc.disposition in (
            NPCDisposition.HOSTILE,
            NPCDisposition.UNFRIENDLY,
        ):
            return False
        if npc.max_hp >= 10:
            return False
        return True

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

    def _bootstrap_combat_against(self, target_npc: NPC) -> CombatState:
        """Build a fresh CombatState with the attacker first (surprise).

        All session PCs participate. The attacking PC is placed at index 0
        so they act before the target NPC, then the remaining PCs, then the
        NPC. Initiative is not rolled — the attacker's surprise stands in
        for an initiative roll for this MVP.
        """
        from bot.cogs.combat import build_npc_combatant, build_pc_combatants

        assert self.session is not None
        pcs = build_pc_combatants(self.session)
        attacker = [c for c in pcs if c.name == self.actor_name]
        others = [c for c in pcs if c.name != self.actor_name]
        enemy = build_npc_combatant(target_npc)
        ordered = attacker + others + [enemy]
        return CombatState(
            combatants=ordered,
            round_number=1,
            current_turn_index=0,
            is_active=True,
        )

    async def _resolve_mechanics(self, action: InterpretedAction) -> str:
        """Apply mechanical effects and return a human-readable summary.

        The MVP only renders a description — engine state mutations for
        exploration actions are out of scope. Combat actions are still
        handled by the existing combat cog (this pipeline routes ATTACK /
        CAST_SPELL through here only when called from a creative @mention,
        in which case we mark them IMPROVISE-ish behaviour).
        """
        if self._trivial_kill_mechanics is not None:
            return self._trivial_kill_mechanics
        at = action.action_type
        if at == ActionType.LOOK:
            loc = self.location
            return (
                f"{action.actor_name} observes {loc.name if loc else 'the area'}."
            )
        if at == ActionType.SEARCH:
            return (
                f"{action.actor_name} searches "
                f"{action.target_name or 'the surroundings'}."
            )
        if at == ActionType.TALK:
            return (
                f"{action.actor_name} approaches {action.target_name} to speak."
            )
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
                    return f"{action.actor_name} cannot reach {exc.destination}."
                # Sync pipeline-local references with the new state.
                self.location = dest
                self.npcs = self.session.npcs
                return f"{action.actor_name} arrives at {dest.name}."
            return f"{action.actor_name} moves toward {action.target_name}."
        if at == ActionType.INTERACT:
            return f"{action.actor_name} interacts with {action.target_name}."
        if at == ActionType.PICKUP:
            return await asyncio.to_thread(self._resolve_pickup, action)
        if at == ActionType.IMPROVISE:
            description = action.improvise_description or action.raw_input
            return (
                f"{action.actor_name} attempts an improvised action: {description}"
            )
        # Combat actions reaching this branch (ATTACK, etc.) are passed
        # through as-is for the narrator to describe.
        return f"{action.actor_name} performs {at.value}."

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
        """Build a small inline context for the narrator (no DB / RAG yet)."""
        loc = self.location
        lines: list[str] = []
        if loc is not None:
            lines.append(f"## Location\n{loc.name}\n{loc.description}")
        if self.npcs:
            present = [
                npc.name for npc in self.npcs.values()
                if loc is not None and npc.location_name == loc.name
            ]
            if present:
                lines.append("## NPCs present\n" + ", ".join(present))
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

        verb = action.action_type.value.lower()
        raw = resolution.raw_value or "cette cible"

        action_summary = (
            f"{action.actor_name} a tenté de {verb} '{raw}', "
            f"mais cette cible n'existe pas à {loc_name}.\n\n"
            f"Personnages réellement présents : {npcs_line}\n"
            f"Sorties réelles : {exits_line}\n\n"
            "Décris en UN court paragraphe la réalisation du personnage et "
            "propose-lui de reformuler en mentionnant un de ces "
            "personnages/sorties s'il y en a. "
            "**N'invente AUCUN autre personnage, lieu ou objet.** "
            "Reste strictement dans le monde décrit ci-dessus."
        )
        context = self._assemble_context(action)
        return await self._call_narrator(
            mechanics_text=action_summary,
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
            mechanics_text=action_summary,
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
