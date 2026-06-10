"""Narrate stage — context assembly + Narrator call + refusal narrators.

Module-level functions extracted from ``ActionPipeline`` so they can be
unit-tested and reused without instantiating the full pipeline class.

The ``ActionPipeline`` facade keeps thin wrapper methods that delegate to
these functions, preserving the legacy call interface.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ai.models import InterpretedAction, MechanicsOutcome, NarrativeResult
from ai.entity_resolver import ResolutionResult
from bot.llm_retry import retry_llm_call
from engine.validators import ValidationResult
from world.location import Location

if TYPE_CHECKING:
    from ai.models import DirectorNote
    from ai.narrator import Narrator
    from bot.game_session import GameSession
    from engine.combat import CombatState
    from engine.inventory import Inventory

logger = logging.getLogger(__name__)


def build_player_intent(action: InterpretedAction) -> str:
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


def assemble_context(
    action: InterpretedAction,
    *,
    actor_name: str,
    location: Location | None,
    npcs: dict | None,
    session: "GameSession | None",
    combat_state: "CombatState | None",
    inventory: "Inventory | None",
    campaign_id: str,
    current_outcome_summary: str | None = None,
    ongoing_dialogue_with: str | None = None,
) -> str:
    """Build the narrator-facing context.

    Delegates to :func:`bot.scene_hydration.describe_scene_for_narrator`
    when a session is available; falls back to a minimal location-only
    snippet otherwise (used by unit tests that construct the pipeline
    without a full session).

    ``current_outcome_summary``, when given, is forwarded to the scene
    builder so the combat "Derniers événements mécaniques" block drops
    the event that represents THIS turn's outcome — prevents the narrator
    from seeing the current action twice.

    ``ongoing_dialogue_with`` is the NPC name for a Talk action that is
    a continuation of an existing dialogue. Triggers the scene builder
    to drop the verbose ## NPCs present block and emit a compact
    ## Dialogue in progress block instead.
    """
    if session is not None:
        from bot.scene_hydration import describe_scene_for_narrator
        scene = describe_scene_for_narrator(
            session,
            actor_name=action.actor_name,
            current_outcome_summary=current_outcome_summary,
            ongoing_dialogue_with=ongoing_dialogue_with,
        )
        # Memory layers (summaries + sliding window [+ lore]) precomputed
        # by update_memory_after_turn at the end of the previous turn —
        # the scene snapshot plays the Layer 1 (structured state) role.
        memory_block = getattr(session, "memory_context", None)
        if memory_block:
            return f"{memory_block}\n\n{scene}"
        return scene

    loc = location
    lines: list[str] = []
    if loc is not None:
        lines.append(f"## Location\n{loc.name}\n{loc.description}")
    lines.append(f"## Acting character\n{action.actor_name}")
    return "\n\n".join(lines)


async def update_memory_after_turn(
    *,
    session: "GameSession | None",
    db_factory: Callable[[], Any] | None,
    player_input: str,
    narration: str,
) -> None:
    """Record this turn's exchanges and refresh the cached memory context.

    Called once per resolved action, after narration (audit H9). The
    player input and the narrator's prose are persisted as two
    consecutive exchanges, then the memory prefix used by the NEXT
    turn's ``assemble_context`` is re-rendered and cached on the session.

    All I/O runs in ``asyncio.to_thread`` — never on the event loop.
    Failures are swallowed and logged: memory must never break gameplay.
    """
    if session is None or db_factory is None:
        return
    if not player_input and not narration:
        return
    campaign_id = session.campaign.id

    def _record_and_render() -> str:
        from memory.context_assembler import ContextAssembler
        from memory.models import ExchangeRole
        from memory.sliding_window import SlidingWindow

        db = db_factory()
        try:
            window = SlidingWindow(db)
            number = window.next_interaction_number(campaign_id)
            if player_input:
                window.add_exchange(
                    campaign_id, ExchangeRole.PLAYER, player_input, number,
                )
                number += 1
            if narration:
                window.add_exchange(
                    campaign_id, ExchangeRole.NARRATOR, narration, number,
                )
            db.commit()

            assembler = ContextAssembler(
                db,
                getattr(session, "semantic_memory", None),
                getattr(session, "ollama_client", None),
            )
            return assembler.assemble_memory_prefix(
                campaign_id, query_text=player_input,
            )
        finally:
            db.close()

    try:
        session.memory_context = await asyncio.to_thread(_record_and_render)
    except Exception:
        logger.warning(
            "MEMORY record failed campaign=%s", campaign_id, exc_info=True,
        )


async def call_narrator(
    narrator: "Narrator",
    outcome: MechanicsOutcome,
    context_prompt: str,
    language: str,
    campaign_id: str,
    has_npc_dialogue: bool = False,
    director_note: "DirectorNote | None" = None,
) -> NarrativeResult:
    """Call the Narrator LLM with retry logic and return the narrative result."""
    def _do() -> NarrativeResult:
        return narrator.narrate(
            action_result_text=outcome.summary,
            context_prompt=context_prompt,
            language=language,
            player_intent=outcome.player_intent,
            outcome_facts=outcome.outcome_facts,
            has_npc_dialogue=bool(outcome.npc_dialogue),
            director_note=director_note,
        )

    return await retry_llm_call(
        _do,
        log_label=f"ACTION campaign={campaign_id} narrate",
    )


async def narrate_unknown(
    narrator: "Narrator",
    action: InterpretedAction,
    resolution: ResolutionResult,
    actor_name: str,
    location: Location | None,
    language: str,
    campaign_id: str,
    session: "GameSession | None" = None,
) -> NarrativeResult:
    """Narrate an in-character refusal, grounded in the real scene.

    The narrator receives the actual ``npcs_present`` and ``connections``
    from the current location, plus an explicit no-hallucination clause,
    so it can suggest a real reformulation instead of inventing entities
    (Lot A — scene awareness).
    """
    loc = location
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
    context = assemble_context(
        action,
        actor_name=actor_name,
        location=location,
        npcs=None,
        session=session,
        combat_state=None,
        inventory=None,
        campaign_id=campaign_id,
    )
    return await call_narrator(
        narrator=narrator,
        outcome=MechanicsOutcome(summary=action_summary),
        context_prompt=context,
        language=language,
        campaign_id=campaign_id,
    )


async def narrate_rule_failure(
    narrator: "Narrator",
    action: InterpretedAction,
    validation: ValidationResult,
    actor_name: str,
    location: Location | None,
    language: str,
    campaign_id: str,
    session: "GameSession | None" = None,
) -> NarrativeResult:
    """Narrate an in-character refusal when the rules forbid the action.

    Like ``narrate_unknown``, the narrator receives the real scene
    context (npcs_present, connections) so its hesitation paragraph
    stays grounded in the world (Lot A — scene awareness).
    """
    loc = location
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
    context = assemble_context(
        action,
        actor_name=actor_name,
        location=location,
        npcs=None,
        session=session,
        combat_state=None,
        inventory=None,
        campaign_id=campaign_id,
    )
    return await call_narrator(
        narrator=narrator,
        outcome=MechanicsOutcome(summary=action_summary),
        context_prompt=context,
        language=language,
        campaign_id=campaign_id,
    )
