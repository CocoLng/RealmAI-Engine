"""Background NPC sheet pre-generation (chantier I / finding H8).

Real sessions paid 18-27 s of lazy NPC sheet generation (qwen3.5:4b) in the
middle of the first TALK action — ``bot/pipeline/resolve.py`` only generates
a sheet when it finds ``npc.personality``/``npc.description`` empty. This
module fills those sheets ahead of time, in a background task scheduled
right after scene hydration, so the TALK path finds them populated and
skips its lazy call entirely.

Design constraints:
- the LLM call always runs through ``asyncio.to_thread`` — never on the
  event loop;
- background tasks are strongly referenced (module-level registry) and trap
  their own exceptions;
- graceful degradation: if a TALK action generated canon while our call was
  in flight, what the player saw wins and the prefetch result is dropped.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from db.repositories.npc_repo import NPCRepository
from engine.npc_archetypes import draw_archetypes
from bot.prefetch_gate import generation_gate, wait_player_idle

if TYPE_CHECKING:
    from bot.game_session import GameSession
    from world.npc import NPC

logger = logging.getLogger(__name__)

# Strong references to in-flight prefetch tasks — asyncio keeps only weak
# references to tasks, so without this registry a running prefetch could be
# garbage-collected mid-flight.
_TASKS: set[asyncio.Task[Any]] = set()

# (campaign_id, npc_name) pairs currently being generated — prevents two
# overlapping hydrations (e.g. a fast double MOVE) from paying the same
# LLM call twice.
_IN_FLIGHT: set[tuple[str, str]] = set()


def _sheet_is_empty(npc: "NPC") -> bool:
    """Mirror of the lazy-generation condition in bot/pipeline/resolve.py."""
    return not (npc.personality or npc.description)


async def prefetch_npc_sheets(
    session: "GameSession",
    *,
    db_factory: Callable[[], Any],
) -> int:
    """Generate sheets for every empty-sheet NPC in ``session.npcs``.

    NPCs are processed sequentially (one local model, no benefit in
    flooding the Ollama queue). A failure on one NPC is logged and does not
    block the others. Returns the number of sheets generated and kept.
    """
    generator = getattr(session, "npc_generator", None)
    if generator is None or not callable(getattr(generator, "generate", None)):
        return 0

    campaign_id = str(getattr(session.campaign, "id", ""))
    campaign_theme = str(getattr(session.campaign, "name", ""))
    language = getattr(session, "language", "fr")
    location = getattr(session, "current_location", None)
    location_ctx = ""
    if location is not None:
        location_ctx = f"{location.name} — {location.description}"

    generated = 0
    # One draw pool per batch: NPCs sharing a location never share an
    # archetype (spec npc-archetypes §1.3 — anti-doublon par lieu).
    used_archetypes: set[str] = set()
    for name in list(getattr(session, "npcs", None) or {}):
        npc = session.npcs.get(name)
        if npc is None or not _sheet_is_empty(npc):
            continue
        key = (campaign_id, name)
        if key in _IN_FLIGHT:
            continue
        _IN_FLIGHT.add(key)
        try:
            async with generation_gate():
                # H8: the fond always yields priority — never start a
                # background LLM call while a player action is in flight,
                # and never pay the call if a TALK filled the sheet while
                # we waited for the gate.
                await wait_player_idle(session)
                if not _sheet_is_empty(npc):
                    logger.info(
                        "NPC prefetch lost race for %r — result dropped", name,
                    )
                    continue
                archetype = draw_archetypes(1, exclude=used_archetypes)[0]
                used_archetypes.add(archetype.id)
                sheet = await asyncio.to_thread(
                    generator.generate,
                    npc_name=name,
                    location_context=location_ctx,
                    campaign_theme=campaign_theme,
                    language=language,
                    archetype=archetype,
                    campaign_id=campaign_id,
                )
            if not _sheet_is_empty(npc):
                # A TALK action filled the sheet while our call was in
                # flight — the canon already shown to the player wins.
                logger.info("NPC prefetch lost race for %r — result dropped", name)
                continue
            npc.personality = sheet.personality
            npc.description = sheet.description
            npc.secrets = list(sheet.secrets)
            npc.knowledge = list(sheet.knowledge)
            generated += 1
            _persist_sheet(npc, campaign_id, db_factory)
        except Exception:
            logger.exception("NPC prefetch failed for %r", name)
        finally:
            _IN_FLIGHT.discard(key)

    if generated:
        logger.info(
            "NPC prefetch campaign=%s generated=%d", campaign_id, generated,
        )
    return generated


def schedule_npc_prefetch(
    session: "GameSession",
    *,
    db_factory: Callable[[], Any],
) -> "asyncio.Task[int] | None":
    """Spawn :func:`prefetch_npc_sheets` as a background task.

    Returns ``None`` (no-op) when there is nothing to do: no generator on
    the session, no empty-sheet NPC that isn't already being generated, or
    no running event loop (sync callers — the prefetch is best-effort).
    """
    generator = getattr(session, "npc_generator", None)
    if generator is None or not callable(getattr(generator, "generate", None)):
        return None
    campaign_id = str(getattr(session.campaign, "id", ""))
    npcs = getattr(session, "npcs", None) or {}
    pending = [
        name for name, npc in npcs.items()
        if _sheet_is_empty(npc) and (campaign_id, name) not in _IN_FLIGHT
    ]
    if not pending:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    task = loop.create_task(
        prefetch_npc_sheets(session, db_factory=db_factory),
        name=f"npc-prefetch:{campaign_id}",
    )
    _TASKS.add(task)
    task.add_done_callback(_on_task_done)
    logger.info(
        "NPC prefetch scheduled campaign=%s npcs=%d", campaign_id, len(pending),
    )
    return task


def _on_task_done(task: asyncio.Task[Any]) -> None:
    _TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:  # pragma: no cover — prefetch traps its own errors
        logger.error("NPC prefetch task crashed", exc_info=exc)


def _persist_sheet(
    npc: "NPC", campaign_id: str, db_factory: Callable[[], Any],
) -> None:
    """Best-effort persistence — the in-memory canon is already set."""
    db_session = db_factory()
    try:
        NPCRepository(db_session).update(npc, campaign_id)
        db_session.commit()
    except Exception:
        logger.warning(
            "NPC prefetch persist failed for %r", npc.name, exc_info=True,
        )
        db_session.rollback()
    finally:
        db_session.close()
