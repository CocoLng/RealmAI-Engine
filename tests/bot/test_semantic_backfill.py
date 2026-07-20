"""Tests for bot/semantic_backfill — RAG re-seed for pre-fix campaigns.

Campaigns saved before the creation-time seeding fix (fix(rag), 2026-07-20)
have their world in SQLite but no ChromaDB collection. /resume must rebuild
the corpus from the DB when the collection is missing or empty.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bot.semantic_backfill import (
    backfill_semantic_index,
    schedule_semantic_backfill,
)
from engine.character import AbilityScores, Race
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC
from world.story_arc import StoryArc, StoryBeat


# ---------------------------------------------------------------------------
# Helpers — real Pydantic models (a MagicMock session would hide field
# mismatches; see lessons 2026-07-18).
# ---------------------------------------------------------------------------


def _make_arc(campaign_id: str) -> StoryArc:
    beats = [
        StoryBeat(
            beat_number=i,
            title=f"Beat {i}",
            description=f"Description du beat {i}.",
            location_hint=f"Lieu {i}",
            encounter_type="exploration",
        )
        for i in range(1, 9)
    ]
    return StoryArc(
        campaign_id=campaign_id,
        theme="Dark Fantasy",
        premise="Une menace ancienne se réveille sous la ville.",
        beats=beats,
        villain_name="L'Ombre",
        villain_motivation="Régner sur les ruines.",
    )


def _make_npc(name: str, *, personality: str = "", description: str = "") -> NPC:
    return NPC(
        name=name,
        race=Race.HUMAN,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=4,
        max_hp=4,
        ac=10,
        personality=personality,
        description=description,
    )


def _make_session(
    *,
    campaign_id: str = "camp-backfill",
    has_documents: bool = False,
    story_arc: StoryArc | None = None,
    npcs: dict[str, NPC] | None = None,
) -> SimpleNamespace:
    semantic = MagicMock()
    semantic.has_documents.return_value = has_documents
    return SimpleNamespace(
        campaign=Campaign(id=campaign_id, name="Dark Fantasy"),
        story_arc=story_arc,
        npcs=npcs or {},
        semantic_memory=semantic,
        semantic_indexer=MagicMock(),
    )


class _StubDBSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# backfill_semantic_index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_indexes_theme_beats_locations_and_npcs() -> None:
    """Empty collection → the whole DB-known corpus gets indexed."""
    arc = _make_arc("camp-backfill")
    npcs = {
        "Elie": _make_npc("Elie", personality="Méfiant.", description="Ermite."),
        "Stub": _make_npc("Stub"),  # unhydrated — must be skipped
    }
    session = _make_session(story_arc=arc, npcs=npcs)
    db_session = _StubDBSession()

    hydrated_loc = Location(name="Place", description="Une place pavée.")
    stub_loc = Location(name="Ruelle", description="")

    with patch("bot.semantic_backfill.LocationRepository") as loc_cls:
        loc_cls.return_value.list_by_campaign.return_value = [hydrated_loc, stub_loc]
        count = await backfill_semantic_index(
            session, db_factory=lambda: db_session,
        )

    indexer = session.semantic_indexer
    # Theme lore + 8 beats + 1 hydrated location + 1 hydrated NPC.
    assert count == 11
    indexer.index_lore.assert_called_once()
    assert "Dark Fantasy" in indexer.index_lore.call_args.kwargs["content"]
    assert indexer.index_beat.call_count == 8
    indexer.index_location.assert_called_once_with("camp-backfill", hydrated_loc)
    indexer.index_npc_entity.assert_called_once_with("camp-backfill", npcs["Elie"])
    assert db_session.closed


@pytest.mark.asyncio
async def test_backfill_skips_when_world_corpus_already_seeded() -> None:
    """Seeded collection → no re-embedding, no DB read.

    The check MUST filter on WORLD_LORE: live pre-fix campaigns have a
    collection holding only Director notes (past_event) — a bare
    is-the-collection-empty check would wrongly skip them.
    """
    from memory.models import SemanticDocumentType

    session = _make_session(has_documents=True, story_arc=_make_arc("camp-backfill"))

    with patch("bot.semantic_backfill.LocationRepository") as loc_cls:
        count = await backfill_semantic_index(
            session, db_factory=lambda: _StubDBSession(),
        )

    assert count == 0
    session.semantic_memory.has_documents.assert_called_once_with(
        "camp-backfill", doc_type=SemanticDocumentType.WORLD_LORE,
    )
    session.semantic_indexer.index_lore.assert_not_called()
    session.semantic_indexer.index_beat.assert_not_called()
    loc_cls.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_without_arc_still_indexes_locations_and_npcs() -> None:
    """A corrupt/absent arc must not block the rest of the corpus."""
    npcs = {"Elie": _make_npc("Elie", personality="Méfiant.")}
    session = _make_session(story_arc=None, npcs=npcs)
    hydrated_loc = Location(name="Place", description="Une place pavée.")

    with patch("bot.semantic_backfill.LocationRepository") as loc_cls:
        loc_cls.return_value.list_by_campaign.return_value = [hydrated_loc]
        count = await backfill_semantic_index(
            session, db_factory=lambda: _StubDBSession(),
        )

    assert count == 2
    session.semantic_indexer.index_lore.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_traps_exceptions() -> None:
    """A Chroma failure mid-backfill is logged, never raised to the caller."""
    session = _make_session(story_arc=_make_arc("camp-backfill"))
    session.semantic_indexer.index_lore.side_effect = RuntimeError("chroma boom")

    count = await backfill_semantic_index(
        session, db_factory=lambda: _StubDBSession(),
    )

    assert count == 0


# ---------------------------------------------------------------------------
# schedule_semantic_backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_creates_task_and_completes() -> None:
    session = _make_session(story_arc=None, npcs={})

    with patch("bot.semantic_backfill.LocationRepository") as loc_cls:
        loc_cls.return_value.list_by_campaign.return_value = []
        task = schedule_semantic_backfill(
            session, db_factory=lambda: _StubDBSession(),
        )
        assert task is not None
        await task


@pytest.mark.asyncio
async def test_schedule_noop_without_semantic_services() -> None:
    """Chroma down at create_ai_services → nothing to backfill into."""
    session = _make_session()
    session.semantic_memory = None
    session.semantic_indexer = None

    assert (
        schedule_semantic_backfill(session, db_factory=lambda: _StubDBSession())
        is None
    )


def test_schedule_without_running_loop_returns_none() -> None:
    session = _make_session()
    assert (
        schedule_semantic_backfill(session, db_factory=lambda: _StubDBSession())
        is None
    )
