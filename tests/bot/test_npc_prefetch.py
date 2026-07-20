"""Tests for bot/npc_prefetch.py — background NPC sheet pre-generation (H8).

Real sessions showed 18-27 s of lazy NPC sheet generation in the middle of
the first TALK action. The prefetch fills empty sheets in a background task
right after scene hydration so the TALK path (bot/pipeline/resolve.py)
finds them already populated and skips its lazy call.

Mirrors the scene-hydration test setup: real in-memory SQLite repositories,
fake LLM generator (mocking the repos would defeat the persistence tests).
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ai.models import NPCSheet
from bot.npc_prefetch import prefetch_npc_sheets, schedule_npc_prefetch
from bot.prefetch_gate import generation_gate, reset_generation_gate
from db.database import Base
from db.mappers import campaign_to_db
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from engine.character import AbilityScores, Race
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC


@pytest.fixture()
def db_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class FakeGenerator:
    """Stands in for ai.npc_generator.NPCGenerator — records calls."""

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail_for = fail_for or set()

    def generate(self, npc_name: str, **kwargs) -> NPCSheet:
        self.calls.append(npc_name)
        if npc_name in self.fail_for:
            raise RuntimeError("boom")
        return NPCSheet(
            personality=f"personnalité de {npc_name}",
            description=f"description de {npc_name}",
            secrets=[f"secret de {npc_name}"],
            knowledge=[f"savoir de {npc_name}"],
        )


def _make_npc(name: str, location_name: str = "Place", **overrides) -> NPC:
    defaults = dict(
        name=name,
        race=Race.HUMAN,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=8,
        max_hp=8,
        ac=10,
        location_name=location_name,
    )
    defaults.update(overrides)
    return NPC(**defaults)


def _make_session(npcs: dict[str, NPC], generator: FakeGenerator | None):
    location = Location(
        name="Place",
        description="Une place pavée balayée par le vent.",
        npcs_present=list(npcs),
    )
    return SimpleNamespace(
        campaign=Campaign(name="Brumes du Nord"),
        current_location=location,
        npcs=npcs,
        npc_generator=generator,
        language="fr",
    )


def _persist_world(db_factory, session) -> None:
    """Persist campaign + location + NPC rows so repo updates have targets."""
    db = db_factory()
    try:
        db.add(campaign_to_db(session.campaign))
        LocationRepository(db).save(session.current_location, session.campaign.id)
        repo = NPCRepository(db)
        for npc in session.npcs.values():
            repo.save(npc, session.campaign.id)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# prefetch_npc_sheets
# ---------------------------------------------------------------------------


async def test_prefetch_passes_distinct_archetypes_per_batch(db_factory) -> None:
    """NPCs sharing a location never share an archetype (spec §1.3)."""

    class ArchetypeRecorder(FakeGenerator):
        def __init__(self) -> None:
            super().__init__()
            self.archetype_ids: list[str] = []

        def generate(self, npc_name: str, **kwargs) -> NPCSheet:
            archetype = kwargs.get("archetype")
            assert archetype is not None, "prefetch must always pass an archetype"
            self.archetype_ids.append(archetype.id)
            return super().generate(npc_name, **kwargs)

    gen = ArchetypeRecorder()
    npcs = {name: _make_npc(name) for name in ("A", "B", "C", "D", "E")}
    session = _make_session(npcs, gen)
    _persist_world(db_factory, session)

    count = await prefetch_npc_sheets(session, db_factory=db_factory)

    assert count == 5
    assert len(set(gen.archetype_ids)) == 5


async def test_prefetch_passes_campaign_id_for_indexing(db_factory) -> None:
    """Prefetched sheets must reach ChromaDB — generate() needs the campaign id."""

    class CampaignIdRecorder(FakeGenerator):
        def __init__(self) -> None:
            super().__init__()
            self.campaign_ids: list[str] = []

        def generate(self, npc_name: str, **kwargs) -> NPCSheet:
            self.campaign_ids.append(kwargs.get("campaign_id", ""))
            return super().generate(npc_name, **kwargs)

    gen = CampaignIdRecorder()
    npcs = {"A": _make_npc("A")}
    session = _make_session(npcs, gen)
    _persist_world(db_factory, session)

    await prefetch_npc_sheets(session, db_factory=db_factory)

    assert gen.campaign_ids == [str(session.campaign.id)]


async def test_prefetch_fills_empty_sheets_and_persists(db_factory) -> None:
    gen = FakeGenerator()
    npcs = {"Jeanne": _make_npc("Jeanne"), "Père Thomas": _make_npc("Père Thomas")}
    session = _make_session(npcs, gen)
    _persist_world(db_factory, session)

    count = await prefetch_npc_sheets(session, db_factory=db_factory)

    assert count == 2
    assert sorted(gen.calls) == ["Jeanne", "Père Thomas"]
    for npc in session.npcs.values():
        assert npc.personality
        assert npc.description
        assert npc.secrets
        assert npc.knowledge

    db = db_factory()
    try:
        stored = NPCRepository(db).get_by_name("Jeanne", session.campaign.id)
    finally:
        db.close()
    assert stored is not None
    assert stored.personality == "personnalité de Jeanne"


async def test_prefetch_skips_filled_sheets(db_factory) -> None:
    gen = FakeGenerator()
    npcs = {
        "Jeanne": _make_npc("Jeanne", personality="Déjà canon."),
        "Père Thomas": _make_npc("Père Thomas"),
    }
    session = _make_session(npcs, gen)
    _persist_world(db_factory, session)

    count = await prefetch_npc_sheets(session, db_factory=db_factory)

    assert count == 1
    assert gen.calls == ["Père Thomas"]
    assert session.npcs["Jeanne"].personality == "Déjà canon."


async def test_prefetch_does_not_overwrite_race_winner(db_factory) -> None:
    """If the TALK lazy path fills the sheet while our LLM call is in
    flight, the prefetch result is dropped — the canon already revealed to
    the player must win."""
    npcs = {"Jeanne": _make_npc("Jeanne")}

    class RacingGenerator(FakeGenerator):
        def generate(self, npc_name: str, **kwargs) -> NPCSheet:
            # Simulate the TALK path winning mid-generation.
            npcs["Jeanne"].personality = "canon du TALK"
            npcs["Jeanne"].description = "déjà décrite en jeu"
            return super().generate(npc_name, **kwargs)

    session = _make_session(npcs, RacingGenerator())
    _persist_world(db_factory, session)

    count = await prefetch_npc_sheets(session, db_factory=db_factory)

    assert count == 0
    assert session.npcs["Jeanne"].personality == "canon du TALK"


async def test_generator_failure_does_not_block_others(db_factory) -> None:
    gen = FakeGenerator(fail_for={"Jeanne"})
    npcs = {"Jeanne": _make_npc("Jeanne"), "Père Thomas": _make_npc("Père Thomas")}
    session = _make_session(npcs, gen)
    _persist_world(db_factory, session)

    count = await prefetch_npc_sheets(session, db_factory=db_factory)

    assert count == 1
    assert not session.npcs["Jeanne"].personality
    assert session.npcs["Père Thomas"].personality


async def test_prefetch_survives_missing_db_row(db_factory) -> None:
    """In-memory sheet still set when the NPC row was never persisted."""
    gen = FakeGenerator()
    npcs = {"Jeanne": _make_npc("Jeanne")}
    session = _make_session(npcs, gen)
    # Persist campaign+location only — NO NPC rows.
    db = db_factory()
    try:
        db.add(campaign_to_db(session.campaign))
        LocationRepository(db).save(session.current_location, session.campaign.id)
        db.commit()
    finally:
        db.close()

    count = await prefetch_npc_sheets(session, db_factory=db_factory)

    assert count == 1
    assert session.npcs["Jeanne"].personality


# ---------------------------------------------------------------------------
# schedule_npc_prefetch
# ---------------------------------------------------------------------------


async def test_schedule_creates_task_and_completes(db_factory) -> None:
    gen = FakeGenerator()
    npcs = {"Jeanne": _make_npc("Jeanne")}
    session = _make_session(npcs, gen)
    _persist_world(db_factory, session)

    task = schedule_npc_prefetch(session, db_factory=db_factory)

    assert isinstance(task, asyncio.Task)
    await asyncio.wait_for(task, timeout=5)
    assert session.npcs["Jeanne"].personality


def test_schedule_without_running_loop_returns_none(db_factory) -> None:
    gen = FakeGenerator()
    npcs = {"Jeanne": _make_npc("Jeanne")}
    session = _make_session(npcs, gen)

    assert schedule_npc_prefetch(session, db_factory=db_factory) is None
    assert gen.calls == []


async def test_schedule_no_generator_returns_none(db_factory) -> None:
    npcs = {"Jeanne": _make_npc("Jeanne")}
    session = _make_session(npcs, generator=None)

    assert schedule_npc_prefetch(session, db_factory=db_factory) is None


async def test_schedule_no_empty_sheets_returns_none(db_factory) -> None:
    gen = FakeGenerator()
    npcs = {"Jeanne": _make_npc("Jeanne", personality="Déjà canon.")}
    session = _make_session(npcs, gen)

    assert schedule_npc_prefetch(session, db_factory=db_factory) is None


async def test_concurrent_schedules_generate_each_npc_once(db_factory) -> None:
    """Two hydrations in quick succession must not double-generate."""
    gate = threading.Event()

    class BlockingGenerator(FakeGenerator):
        def generate(self, npc_name: str, **kwargs) -> NPCSheet:
            result = super().generate(npc_name, **kwargs)
            gate.wait(timeout=5)
            return result

    gen = BlockingGenerator()
    npcs = {"Jeanne": _make_npc("Jeanne")}
    session = _make_session(npcs, gen)
    _persist_world(db_factory, session)

    task1 = schedule_npc_prefetch(session, db_factory=db_factory)
    assert task1 is not None
    # Let the first task reach the blocking LLM call.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if gen.calls:
            break

    task2 = schedule_npc_prefetch(session, db_factory=db_factory)
    gate.set()
    await asyncio.wait_for(task1, timeout=5)
    if task2 is not None:
        await asyncio.wait_for(task2, timeout=5)

    assert gen.calls.count("Jeanne") == 1
    assert session.npcs["Jeanne"].personality


# ---------------------------------------------------------------------------
# Gate compliance tests (H8)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_gate():
    reset_generation_gate()
    yield
    reset_generation_gate()


async def test_prefetch_yields_to_player_action(db_factory) -> None:
    """No LLM call starts while session.action_lock is held (H8 gate)."""
    gen = FakeGenerator()
    npcs = {"Jeanne": _make_npc("Jeanne")}
    session = _make_session(npcs, gen)
    session.action_lock = asyncio.Lock()
    _persist_world(db_factory, session)

    await session.action_lock.acquire()
    task = asyncio.create_task(
        prefetch_npc_sheets(session, db_factory=db_factory),
    )
    for _ in range(10):
        await asyncio.sleep(0)
    assert gen.calls == []

    session.action_lock.release()
    count = await asyncio.wait_for(task, timeout=5)
    assert count == 1
    assert gen.calls == ["Jeanne"]


async def test_prefetch_waits_for_generation_gate(db_factory) -> None:
    """The NPC prefetch respects the process-wide generation gate."""
    gen = FakeGenerator()
    npcs = {"Jeanne": _make_npc("Jeanne")}
    session = _make_session(npcs, gen)
    _persist_world(db_factory, session)

    async with generation_gate():
        task = asyncio.create_task(
            prefetch_npc_sheets(session, db_factory=db_factory),
        )
        for _ in range(10):
            await asyncio.sleep(0)
        assert gen.calls == []

    count = await asyncio.wait_for(task, timeout=5)
    assert count == 1
