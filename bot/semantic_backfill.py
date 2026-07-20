"""Semantic index backfill for campaigns saved before the RAG seed fix.

Campaigns created before ``fix(rag)`` (2026-07-20) have their world in
SQLite but no ChromaDB collection — the generators only started indexing
at creation time from that commit on. On ``/resume``, when the campaign's
collection is missing or empty, this module rebuilds the corpus from what
the session and the DB already hold: campaign theme, story beats, every
hydrated location, and every hydrated NPC sheet.

Design constraints (same as the other background fills):
- embedding work runs through ``asyncio.to_thread``, never on the event
  loop;
- the background task is strongly referenced and traps its own
  exceptions — a Chroma failure never breaks /resume;
- idempotent: indexer IDs are deterministic and ChromaDB ``add`` skips
  existing IDs, so racing a fresh generation never duplicates documents.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from db.repositories.location_repo import LocationRepository
from memory.models import SemanticDocumentType

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)

# Strong references to in-flight backfill tasks — asyncio keeps only weak
# references to tasks, so without this registry a running backfill could be
# garbage-collected mid-flight.
_TASKS: set[asyncio.Task[Any]] = set()


def _backfill_sync(
    session: "GameSession", db_factory: Callable[[], Any],
) -> int:
    """Blocking part: collection check, DB read, embedding writes.

    Returns the number of documents submitted for indexing (0 when the
    collection is already seeded).
    """
    semantic = getattr(session, "semantic_memory", None)
    indexer = getattr(session, "semantic_indexer", None)
    if semantic is None or indexer is None:
        return 0

    # Filter on WORLD_LORE, not mere collection existence: live pre-fix
    # campaigns have a collection holding only Director notes.
    campaign_id = str(session.campaign.id)
    if semantic.has_documents(campaign_id, doc_type=SemanticDocumentType.WORLD_LORE):
        return 0

    indexed = 0

    arc = getattr(session, "story_arc", None)
    if arc is not None:
        indexer.index_lore(
            campaign_id,
            content=f"Campaign theme: {arc.theme}",
            metadata={"source": "backfill", "category": "theme"},
        )
        indexed += 1
        for beat in arc.beats:
            indexer.index_beat(campaign_id, beat)
            indexed += 1

    db_session = db_factory()
    try:
        locations = LocationRepository(db_session).list_by_campaign(campaign_id)
    finally:
        db_session.close()
    for location in locations:
        if not (location.description or "").strip():
            continue  # stub — indexed at hydration time instead
        indexer.index_location(campaign_id, location)
        indexed += 1

    for npc in (getattr(session, "npcs", None) or {}).values():
        if not (npc.personality or npc.description):
            continue  # unhydrated — indexed at generation time instead
        indexer.index_npc_entity(campaign_id, npc)
        indexed += 1

    return indexed


async def backfill_semantic_index(
    session: "GameSession", *, db_factory: Callable[[], Any],
) -> int:
    """Re-seed the campaign's ChromaDB collection if it is missing or empty.

    Best-effort: traps every exception and returns 0 on failure — a broken
    Chroma degrades the RAG layer, never the resume.
    """
    campaign_id = str(session.campaign.id)
    try:
        indexed = await asyncio.to_thread(_backfill_sync, session, db_factory)
    except Exception:
        logger.exception("SEMANTIC backfill failed campaign=%s", campaign_id)
        return 0
    if indexed:
        logger.info(
            "SEMANTIC backfill campaign=%s docs=%d", campaign_id, indexed,
        )
    return indexed


def schedule_semantic_backfill(
    session: "GameSession", *, db_factory: Callable[[], Any],
) -> "asyncio.Task[int] | None":
    """Spawn :func:`backfill_semantic_index` as a background task.

    Returns ``None`` (no-op) when the session has no semantic services
    (Chroma down at init) or there is no running event loop.
    """
    if (
        getattr(session, "semantic_memory", None) is None
        or getattr(session, "semantic_indexer", None) is None
    ):
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    task = loop.create_task(
        backfill_semantic_index(session, db_factory=db_factory),
        name=f"semantic-backfill:{session.campaign.id}",
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task
