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
    from memory.coherence_rules import CoherenceSnapshot

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
        blocks: list[str] = []
        memory_block = getattr(session, "memory_context", None)
        if isinstance(memory_block, str) and memory_block:
            blocks.append(memory_block)
        facts_block = _render_locked_facts(session)
        if facts_block:
            blocks.append(facts_block)
        blocks.append(scene)
        return "\n\n".join(blocks)

    loc = location
    lines: list[str] = []
    if loc is not None:
        lines.append(f"## Location\n{loc.name}\n{loc.description}")
    lines.append(f"## Acting character\n{action.actor_name}")
    return "\n\n".join(lines)


def _render_locked_facts(session: "GameSession") -> str:
    """Render the [LOCKED FACTS] block from the arc's locked facts (H17).

    Pure in-memory read — safe on the event loop. Empty string when the
    session has no arc or no facts.
    """
    arc = getattr(session, "story_arc", None)
    facts = getattr(arc, "locked_facts", None) if arc is not None else None
    # isinstance guard: tests drive the pipeline with MagicMock sessions
    if not isinstance(facts, list) or not facts:
        return ""
    lines = ["[LOCKED FACTS]"]
    lines += [f"- [{fact.id}] {fact.text}" for fact in facts]
    return "\n".join(lines)


def build_coherence_snapshot(
    session: "GameSession",
    *,
    actor_name: str,
    inventory: "Inventory | None",
    moved_this_turn: bool,
) -> "CoherenceSnapshot":
    """Map the live session onto the coherence-rule input contract.

    isinstance guards mirror the rest of this module: tests drive the
    pipeline with MagicMock sessions."""
    from memory.coherence_rules import CoherenceSnapshot, LockedFactSnapshot

    npcs = getattr(session, "npcs", None)
    npcs = npcs if isinstance(npcs, dict) else {}
    characters = getattr(session, "characters", None)
    characters = characters if isinstance(characters, dict) else {}

    actor = next((c for c in characters.values() if c.name == actor_name), None)
    max_hp = getattr(actor, "max_hp", 0) if actor is not None else 0
    ratio = (actor.hp / max_hp) if actor is not None and max_hp else 1.0

    loc = getattr(session, "current_location", None)
    loc_name = getattr(loc, "name", None) if loc is not None else None
    connections = list(getattr(loc, "connections", []) or []) if loc is not None else []

    combat = getattr(session, "combat_state", None)
    combat_active = combat is not None and bool(getattr(combat, "is_active", False))
    zones: list[str] = []
    if combat_active and loc is not None:
        zones = [z.name for z in (getattr(loc, "zones", []) or [])]

    arc = getattr(session, "story_arc", None)
    raw_facts = getattr(arc, "locked_facts", None) if arc is not None else None
    facts = (
        [LockedFactSnapshot(id=f.id, text=f.text) for f in raw_facts]
        if isinstance(raw_facts, list) else []
    )

    inv_names: list[str] = []
    if inventory is not None:
        inv_names = [item.name for item in inventory.items]
        inv_names += [item.name for item in inventory.equipped.values()]

    return CoherenceSnapshot(
        dead_npcs=[n.name for n in npcs.values() if not n.is_alive],
        known_npc_names=[n.name for n in npcs.values()],
        player_names=[c.name for c in characters.values()],
        current_location=loc_name,
        known_locations=([loc_name, *connections] if loc_name else []),
        moved_this_turn=moved_this_turn,
        actor_inventory=inv_names,
        player_hp_ratio=ratio,
        combat_active=combat_active,
        combat_zones=zones,
        locked_facts=facts,
    )


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
    if session is None:
        return
    _sync_locked_facts(session)
    if narration:
        from memory import narration_guard
        narration_guard.record_narration(session.campaign.id, narration)
    if db_factory is None:
        return
    if not player_input and not narration:
        return
    campaign_id = session.campaign.id

    def _record_and_render() -> tuple[str, bool]:
        from memory.context_assembler import ContextAssembler
        from memory.models import ExchangeRole
        from memory.sliding_window import SlidingWindow
        from memory.summarizer import Summarizer

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
            prefix = assembler.assemble_memory_prefix(
                campaign_id, query_text=player_input,
            )
            needs_summary = Summarizer(db, None).should_summarize(campaign_id)
            return prefix, needs_summary
        finally:
            db.close()

    try:
        prefix, needs_summary = await asyncio.to_thread(_record_and_render)
        session.memory_context = prefix
    except Exception:
        logger.warning(
            "MEMORY record failed campaign=%s", campaign_id, exc_info=True,
        )
        return

    if needs_summary:
        _schedule_summarization(session, db_factory)


def _sync_locked_facts(session: "GameSession") -> None:
    """Refresh the narration guard and the arc's locked facts (H17).

    Pure in-memory pass, called at the END of each turn: an NPC killed
    this turn becomes a locked fact and enters the guard's dead set only
    for FUTURE turns — its death narration is never flagged.
    """
    from memory import narration_guard
    from world.story_arc import LockedFact

    # isinstance guards: tests drive the pipeline with MagicMock sessions
    npcs = getattr(session, "npcs", None)
    if not isinstance(npcs, dict):
        return
    dead = [npc.name for npc in npcs.values() if not npc.is_alive]
    narration_guard.set_dead_npcs(session.campaign.id, dead)

    arc = getattr(session, "story_arc", None)
    facts = getattr(arc, "locked_facts", None) if arc is not None else None
    if not isinstance(facts, list):
        return
    existing = {fact.id for fact in facts}
    for name in dead:
        fact_id = f"npc_dead:{name}"
        if fact_id not in existing:
            facts.append(LockedFact(
                id=fact_id,
                text=f"{name} est mort(e). Ce personnage ne peut plus parler ni agir.",
            ))


def _schedule_summarization(
    session: "GameSession",
    db_factory: Callable[[], Any],
) -> None:
    """Fire-and-forget Layer 3 summarization (Ollama via to_thread).

    The task reference on the session doubles as a re-entrancy guard:
    while a summarization is in flight, no second one is scheduled.
    """
    client = getattr(session, "ollama_client", None)
    if client is None:
        return
    current = getattr(session, "memory_summarize_task", None)
    if current is not None and not current.done():
        return
    campaign_id = session.campaign.id

    def _summarize() -> None:
        from memory.summarizer import Summarizer

        db = db_factory()
        try:
            summary = Summarizer(db, client).summarize(campaign_id)
            if summary is not None:
                db.commit()
        finally:
            db.close()

    async def _run() -> None:
        try:
            await asyncio.to_thread(_summarize)
        except Exception:
            logger.warning(
                "MEMORY summarization failed campaign=%s",
                campaign_id, exc_info=True,
            )

    session.memory_summarize_task = asyncio.get_running_loop().create_task(_run())


async def call_narrator(
    narrator: "Narrator",
    outcome: MechanicsOutcome,
    context_prompt: str,
    language: str,
    campaign_id: str,
    has_npc_dialogue: bool = False,
    director_note: "DirectorNote | None" = None,
    guard: bool = True,
    snapshot: "CoherenceSnapshot | None" = None,
) -> NarrativeResult:
    """Call the Narrator LLM with retry logic and return the narrative result.

    When ``guard`` is True (the main action path), the result goes through
    the deterministic coherence gate: any BLOCKING violation (dead NPC
    resurrected, phantom item, near-verbatim repetition, ...) triggers ONE
    corrective retry listing each expected fact as an explicit constraint.
    If the retry still violates a blocking rule, the LLM is never called a
    third time — the result is a deterministic tier-3 template
    (``narrator.template_narration``), never a published contradiction.
    OBSERVE-mode violations never trigger a retry; they are only logged by
    ``check_narration``.
    """
    def _do(action_text: str) -> NarrativeResult:
        return narrator.narrate(
            action_result_text=action_text,
            context_prompt=context_prompt,
            language=language,
            player_intent=outcome.player_intent,
            outcome_facts=outcome.outcome_facts,
            has_npc_dialogue=bool(outcome.npc_dialogue),
            director_note=director_note,
        )

    result = await retry_llm_call(
        lambda: _do(outcome.summary),
        log_label=f"ACTION campaign={campaign_id} narrate",
    )
    if not guard:
        return result

    from memory import narration_guard

    def _inspect(res: NarrativeResult) -> tuple["narration_guard.GuardVerdict", str | None]:
        verdict = narration_guard.check_narration(
            campaign_id,
            narrative=res.narrative,
            snapshot=snapshot,
            npcs_mentioned=res.npcs_mentioned,
        )
        repeated = narration_guard.find_repetition(campaign_id, res.narrative)
        return verdict, repeated

    verdict, repeated = _inspect(result)
    if not verdict.blocking and repeated is None:
        if result.locked_facts_used:
            logger.info(
                "NARRATE locked_facts_used campaign=%s ids=%s",
                campaign_id, result.locked_facts_used,
            )
        return result

    constraints: list[str] = [
        "CONTRAINTE ABSOLUE (cohérence) : la narration contredit l'état du "
        f"jeu — {violation.expected}. Réécris en respectant strictement ce fait."
        for violation in verdict.blocking
    ]
    if repeated is not None:
        constraints.append(
            "CONTRAINTE DE VARIATION : ta narration répète presque mot pour "
            f"mot un passage récent (« {repeated[:120]} »). Reformule avec "
            "des images, un rythme et un vocabulaire différents, sans "
            "changer les faits."
        )
    logger.warning(
        "NARRATION guard: %d blocking violation(s) campaign=%s rules=%s — retrying once",
        len(verdict.blocking) + (1 if repeated is not None else 0),
        campaign_id,
        [violation.rule for violation in verdict.blocking],
    )
    amended = "\n\n".join([outcome.summary, *constraints])
    retry_result = await retry_llm_call(
        lambda: _do(amended),
        log_label=f"ACTION campaign={campaign_id} narrate-guard-retry",
    )

    verdict2, repeated2 = _inspect(retry_result)
    if not verdict2.blocking and repeated2 is None:
        return retry_result

    logger.error(
        "NARRATION guard: retry still violates campaign=%s rules=%s — template fallback",
        campaign_id, [violation.rule for violation in verdict2.blocking],
    )
    return narrator.template_narration(
        outcome.summary, outcome.outcome_facts, language,
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
        guard=False,
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
        guard=False,
    )
