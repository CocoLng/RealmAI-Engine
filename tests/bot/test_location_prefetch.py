"""Tests for bot/location_prefetch.py — background neighbor generation (H8).

A MOVE to a never-generated location pays ~57-80 s of synchronous
WorldGenerator inside the action pipeline. The prefetch generates the
current location's neighbors in the background so the next MOVE finds a
fully generated row in the DB.

Real in-memory SQLite repositories (mocking them would defeat the
persistence assertions); the LLM is faked by patching
``bot.world_navigation.generate_destination``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import bot.location_prefetch as location_prefetch
from bot.location_prefetch import (
    prefetch_neighbor_locations,
    schedule_location_prefetch,
    wait_for_started_job,
)
from bot.prefetch_gate import generation_gate, reset_generation_gate
from db.database import Base
from db.mappers import campaign_to_db
from db.repositories.location_repo import LocationRepository
from world.campaign import Campaign
from world.location import Location


@pytest.fixture()
def db_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def _fresh_registries():
    reset_generation_gate()
    location_prefetch.reset_for_tests()
    yield
    reset_generation_gate()
    location_prefetch.reset_for_tests()


class FakeDestinationFactory:
    """Async stand-in for bot.world_navigation.generate_destination."""

    def __init__(
        self,
        fail_for: set[str] | None = None,
        hold: "asyncio.Event | None" = None,
    ) -> None:
        self.calls: list[str] = []
        self.fail_for = fail_for or set()
        self.hold = hold

    async def __call__(
        self,
        session,
        destination_name: str,
        *,
        origin_name: str,
        required_connections: list[str],
    ) -> Location:
        self.calls.append(destination_name)
        # Snapshot at entry: clearing fail_for mid-flight only affects the
        # NEXT call (the sync retry), not the held in-flight one.
        should_fail = destination_name in self.fail_for
        if self.hold is not None:
            await self.hold.wait()
        if should_fail:
            raise RuntimeError("boom")
        return Location(
            name=destination_name,
            description=f"généré: {destination_name}",
            connections=[*required_connections, "Grotte oubliée"],
            generated=True,
        )


def _make_session(connections: list[str], *, story_arc=None) -> SimpleNamespace:
    location = Location(
        name="Place",
        description="Une place pavée balayée par le vent.",
        connections=connections,
        generated=True,
    )
    return SimpleNamespace(
        campaign=Campaign(name="Brumes du Nord", current_location="Place"),
        current_location=location,
        npcs={},
        ollama_client=object(),
        language="fr",
        story_arc=story_arc,
    )


def _persist_world(db_factory, session, *, stubs: list[str] = ()) -> None:
    """Persist campaign + current location + stub rows for ``stubs``."""
    db = db_factory()
    try:
        db.add(campaign_to_db(session.campaign))
        repo = LocationRepository(db)
        repo.save(session.current_location, session.campaign.id)
        for name in stubs:
            repo.save(
                Location(
                    name=name,
                    description="",
                    connections=["Place"],
                    generated=False,
                ),
                session.campaign.id,
            )
        db.commit()
    finally:
        db.close()


def _load(db_factory, name: str, campaign_id: str) -> Location | None:
    db = db_factory()
    try:
        return LocationRepository(db).get_by_name(name, campaign_id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# prefetch_neighbor_locations
# ---------------------------------------------------------------------------


async def test_prefetch_generates_and_persists_stub_neighbors(db_factory) -> None:
    fake = FakeDestinationFactory()
    session = _make_session(["Ruelle", "Marché"])
    _persist_world(db_factory, session, stubs=["Ruelle", "Marché"])

    with patch("bot.world_navigation.generate_destination", fake):
        count = await asyncio.wait_for(
            prefetch_neighbor_locations(session, db_factory=db_factory),
            timeout=5,
        )

    assert count == 2
    assert sorted(fake.calls) == ["Marché", "Ruelle"]
    for name in ("Ruelle", "Marché"):
        row = _load(db_factory, name, session.campaign.id)
        assert row is not None and row.generated
        assert "Place" in row.connections
    # Grandchild connections became stubs (rows only, no LLM call).
    grandchild = _load(db_factory, "Grotte oubliée", session.campaign.id)
    assert grandchild is not None and not grandchild.generated


async def test_prefetch_skips_already_generated_neighbors(db_factory) -> None:
    fake = FakeDestinationFactory()
    session = _make_session(["Ruelle", "Marché"])
    _persist_world(db_factory, session, stubs=["Ruelle"])
    db = db_factory()
    try:
        LocationRepository(db).save(
            Location(
                name="Marché",
                description="déjà là",
                connections=["Place"],
                generated=True,
            ),
            session.campaign.id,
        )
        db.commit()
    finally:
        db.close()

    with patch("bot.world_navigation.generate_destination", fake):
        count = await asyncio.wait_for(
            prefetch_neighbor_locations(session, db_factory=db_factory),
            timeout=5,
        )

    assert count == 1
    assert fake.calls == ["Ruelle"]


async def test_prefetch_failure_on_one_neighbor_continues(db_factory) -> None:
    fake = FakeDestinationFactory(fail_for={"Ruelle"})
    session = _make_session(["Ruelle", "Marché"])
    _persist_world(db_factory, session, stubs=["Ruelle", "Marché"])

    with patch("bot.world_navigation.generate_destination", fake):
        count = await asyncio.wait_for(
            prefetch_neighbor_locations(session, db_factory=db_factory),
            timeout=5,
        )

    assert count == 1
    assert sorted(fake.calls) == ["Marché", "Ruelle"]
    row = _load(db_factory, "Marché", session.campaign.id)
    assert row is not None and row.generated


async def test_prefetch_abandons_queue_when_party_moves_on(db_factory) -> None:
    hold = asyncio.Event()
    fake = FakeDestinationFactory(hold=hold)
    session = _make_session(["Ruelle", "Marché"])
    _persist_world(db_factory, session, stubs=["Ruelle", "Marché"])

    with patch("bot.world_navigation.generate_destination", fake):
        task = asyncio.create_task(
            prefetch_neighbor_locations(session, db_factory=db_factory),
        )
        while not fake.calls:
            await asyncio.sleep(0)
        # The party moves while the first job is in flight.
        session.current_location = Location(
            name="Ailleurs", description="loin", generated=True,
        )
        hold.set()
        count = await asyncio.wait_for(task, timeout=5)

    assert count == 1          # the in-flight job completes...
    assert fake.calls == ["Ruelle"]  # ...but the stale queue is dropped


async def test_prefetch_does_not_mutate_session(db_factory) -> None:
    fake = FakeDestinationFactory()
    session = _make_session(["Ruelle"])
    _persist_world(db_factory, session, stubs=["Ruelle"])
    location_before = session.current_location
    npcs_before = session.npcs

    with patch("bot.world_navigation.generate_destination", fake):
        await asyncio.wait_for(
            prefetch_neighbor_locations(session, db_factory=db_factory),
            timeout=5,
        )

    assert session.current_location is location_before
    assert session.npcs is npcs_before
    assert session.campaign.current_location == "Place"


async def test_prefetch_priority_follows_current_beat_hint(db_factory) -> None:
    beats = [
        SimpleNamespace(location_hint="Marché"),
        SimpleNamespace(location_hint="Crypte"),
    ]
    arc = SimpleNamespace(beats=beats, current_beat_index=0)
    fake = FakeDestinationFactory()
    session = _make_session(["Ruelle", "Marché"], story_arc=arc)
    _persist_world(db_factory, session, stubs=["Ruelle", "Marché"])

    with patch("bot.world_navigation.generate_destination", fake):
        await asyncio.wait_for(
            prefetch_neighbor_locations(session, db_factory=db_factory),
            timeout=5,
        )

    assert fake.calls == ["Marché", "Ruelle"]


async def test_prefetch_yields_to_player_action(db_factory) -> None:
    fake = FakeDestinationFactory()
    session = _make_session(["Ruelle"])
    session.action_lock = asyncio.Lock()
    _persist_world(db_factory, session, stubs=["Ruelle"])

    await session.action_lock.acquire()
    with patch("bot.world_navigation.generate_destination", fake):
        task = asyncio.create_task(
            prefetch_neighbor_locations(session, db_factory=db_factory),
        )
        for _ in range(10):
            await asyncio.sleep(0)
        assert fake.calls == []
        session.action_lock.release()
        count = await asyncio.wait_for(task, timeout=5)

    assert count == 1


async def test_prefetch_waits_for_generation_gate(db_factory) -> None:
    fake = FakeDestinationFactory()
    session = _make_session(["Ruelle"])
    _persist_world(db_factory, session, stubs=["Ruelle"])

    with patch("bot.world_navigation.generate_destination", fake):
        async with generation_gate():
            task = asyncio.create_task(
                prefetch_neighbor_locations(session, db_factory=db_factory),
            )
            for _ in range(10):
                await asyncio.sleep(0)
            assert fake.calls == []
        count = await asyncio.wait_for(task, timeout=5)

    assert count == 1


async def test_two_prefetch_runs_do_not_double_generate(db_factory) -> None:
    hold = asyncio.Event()
    fake = FakeDestinationFactory(hold=hold)
    session = _make_session(["Ruelle"])
    _persist_world(db_factory, session, stubs=["Ruelle"])

    with patch("bot.world_navigation.generate_destination", fake):
        first = asyncio.create_task(
            prefetch_neighbor_locations(session, db_factory=db_factory),
        )
        while not fake.calls:
            await asyncio.sleep(0)
        second = asyncio.create_task(
            prefetch_neighbor_locations(session, db_factory=db_factory),
        )
        for _ in range(10):
            await asyncio.sleep(0)
        hold.set()
        results = await asyncio.wait_for(
            asyncio.gather(first, second), timeout=5,
        )

    assert fake.calls == ["Ruelle"]
    assert sorted(results) == [0, 1]


# ---------------------------------------------------------------------------
# schedule_location_prefetch
# ---------------------------------------------------------------------------


async def test_schedule_noop_without_ollama_client(db_factory) -> None:
    session = _make_session(["Ruelle"])
    session.ollama_client = None
    _persist_world(db_factory, session, stubs=["Ruelle"])
    assert schedule_location_prefetch(session, db_factory=db_factory) is None


async def test_schedule_noop_without_pending_neighbors(db_factory) -> None:
    session = _make_session([])
    _persist_world(db_factory, session)
    assert schedule_location_prefetch(session, db_factory=db_factory) is None


async def test_schedule_runs_prefetch(db_factory) -> None:
    fake = FakeDestinationFactory()
    session = _make_session(["Ruelle"])
    _persist_world(db_factory, session, stubs=["Ruelle"])

    with patch("bot.world_navigation.generate_destination", fake):
        task = schedule_location_prefetch(session, db_factory=db_factory)
        assert task is not None
        count = await asyncio.wait_for(task, timeout=5)

    assert count == 1


# ---------------------------------------------------------------------------
# wait_for_started_job
# ---------------------------------------------------------------------------


async def test_wait_for_started_job_false_when_nothing_started(db_factory) -> None:
    session = _make_session(["Ruelle"])
    assert not await wait_for_started_job(session.campaign.id, "Ruelle")


async def test_wait_for_started_job_awaits_inflight_generation(db_factory) -> None:
    hold = asyncio.Event()
    fake = FakeDestinationFactory(hold=hold)
    session = _make_session(["Ruelle"])
    _persist_world(db_factory, session, stubs=["Ruelle"])

    with patch("bot.world_navigation.generate_destination", fake):
        task = asyncio.create_task(
            prefetch_neighbor_locations(session, db_factory=db_factory),
        )
        while not fake.calls:
            await asyncio.sleep(0)
        waiter = asyncio.create_task(
            wait_for_started_job(session.campaign.id, "Ruelle"),
        )
        await asyncio.sleep(0)
        assert not waiter.done()
        hold.set()
        assert await asyncio.wait_for(waiter, timeout=5) is True
        await asyncio.wait_for(task, timeout=5)

    row = _load(db_factory, "Ruelle", session.campaign.id)
    assert row is not None and row.generated
