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
from sqlalchemy.pool import StaticPool

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
from engine.combat import CombatState
from world.campaign import Campaign
from world.location import Location


@pytest.fixture()
def db_factory():
    # StaticPool + check_same_thread=False mirror db.database.create_db_engine:
    # persistence now runs off the event loop via asyncio.to_thread, and the
    # default pool would hand each thread its own (empty) in-memory database.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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


async def test_prefetch_generates_nothing_during_active_combat(db_factory) -> None:
    """F1: between combat turns action_lock is free, so an unguarded prefetch
    would start a 9b job that queues the next combat turn behind it. Active
    combat must short-circuit the loop before it ever reaches the gate."""
    fake = FakeDestinationFactory()
    session = _make_session(["Ruelle", "Marché"])
    session.combat_state = CombatState()  # is_active=True by default
    _persist_world(db_factory, session, stubs=["Ruelle", "Marché"])

    with patch("bot.world_navigation.generate_destination", fake):
        count = await asyncio.wait_for(
            prefetch_neighbor_locations(session, db_factory=db_factory),
            timeout=5,
        )

    assert count == 0
    assert fake.calls == []
    assert schedule_location_prefetch(session, db_factory=db_factory) is None

    # Combat ends and the arrival hook reschedules — the prefetch now runs.
    session.combat_state.is_active = False
    with patch("bot.world_navigation.generate_destination", fake):
        count = await asyncio.wait_for(
            prefetch_neighbor_locations(session, db_factory=db_factory),
            timeout=5,
        )

    assert count == 2
    assert sorted(fake.calls) == ["Marché", "Ruelle"]


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


# ---------------------------------------------------------------------------
# MOVE vs prefetch race (change_location integration)
# ---------------------------------------------------------------------------


def _patched_move_env():
    """change_location needs hydrate_scene patched out (it schedules NPC
    prefetch and needs a fuller session than these tests build)."""
    return patch("bot.scene_hydration.hydrate_scene")


async def test_move_awaits_started_job_and_pays_one_generation(db_factory) -> None:
    from bot.world_navigation import change_location

    hold = asyncio.Event()
    fake = FakeDestinationFactory(hold=hold)
    session = _make_session(["Ruelle"])
    _persist_world(db_factory, session, stubs=["Ruelle"])

    with patch("bot.world_navigation.generate_destination", fake), _patched_move_env():
        prefetch = asyncio.create_task(
            prefetch_neighbor_locations(session, db_factory=db_factory),
        )
        while not fake.calls:
            await asyncio.sleep(0)
        move = asyncio.create_task(
            change_location(session, "Ruelle", db_factory=db_factory),
        )
        await asyncio.sleep(0)
        assert not move.done()  # waiting on the started job, not regenerating
        hold.set()
        dest = await asyncio.wait_for(move, timeout=5)
        await asyncio.wait_for(prefetch, timeout=5)

    assert dest.generated
    assert fake.calls == ["Ruelle"]  # ONE generation total
    assert session.current_location.name == "Ruelle"


async def test_move_rereads_db_when_started_job_finishes_mid_load(
    db_factory,
) -> None:
    """``wait_for_started_job`` → False can mean "the job just finished and
    was unregistered", not only "never started". The MOVE's initial DB read
    may predate that job's commit (slow worker-thread scheduling — seen on
    the 2-core CI runner), so it must re-read before deciding to regenerate:
    ONE generation total, never two (H8)."""
    from bot.world_navigation import change_location

    hold = asyncio.Event()
    fake = FakeDestinationFactory(hold=hold)
    session = _make_session(["Ruelle"])
    _persist_world(db_factory, session, stubs=["Ruelle"])

    real_to_thread = asyncio.to_thread
    straddled = False

    async def first_load_straddles_job_end(fn, *args, **kwargs):
        # First call = the MOVE's initial destination load. Return its
        # (stub) snapshot only after the started job has fully finished
        # AND been popped from the registry — the exact CI interleaving.
        nonlocal straddled
        result = await real_to_thread(fn, *args, **kwargs)
        if not straddled:
            straddled = True
            hold.set()
            await asyncio.wait_for(prefetch, timeout=5)
        return result

    with (
        patch("bot.world_navigation.generate_destination", fake),
        _patched_move_env(),
        # Global for the duration of the test: only change_location reaches
        # to_thread here (the prefetch job persists on the loop, the fake
        # never threads).
        patch("asyncio.to_thread", first_load_straddles_job_end),
    ):
        prefetch = asyncio.create_task(
            prefetch_neighbor_locations(session, db_factory=db_factory),
        )
        while not fake.calls:
            await asyncio.sleep(0)
        dest = await asyncio.wait_for(
            change_location(session, "Ruelle", db_factory=db_factory),
            timeout=5,
        )

    assert dest.generated
    assert fake.calls == ["Ruelle"]  # ONE generation total — never re-paid
    assert session.current_location.name == "Ruelle"

    row = _load(db_factory, "Ruelle", session.campaign.id)
    assert row is not None and row.generated


async def test_move_ignores_queued_job_and_generates_sync(db_factory) -> None:
    """A queued-but-not-started job is never awaited (anti-deadlock): the
    MOVE generates synchronously and the prefetch then skips the row."""
    from bot.world_navigation import change_location

    fake = FakeDestinationFactory()
    session = _make_session(["Ruelle"])
    _persist_world(db_factory, session, stubs=["Ruelle"])

    with patch("bot.world_navigation.generate_destination", fake), _patched_move_env():
        async with generation_gate():
            # The prefetch parks on the gate — queued, never started.
            prefetch = asyncio.create_task(
                prefetch_neighbor_locations(session, db_factory=db_factory),
            )
            for _ in range(10):
                await asyncio.sleep(0)
            assert fake.calls == []
            dest = await asyncio.wait_for(
                change_location(session, "Ruelle", db_factory=db_factory),
                timeout=5,
            )
        await asyncio.wait_for(prefetch, timeout=5)

    assert dest.generated
    assert fake.calls == ["Ruelle"]  # only the MOVE generated it


async def test_move_falls_back_when_started_job_fails(db_factory) -> None:
    from bot.world_navigation import change_location

    hold = asyncio.Event()
    fake = FakeDestinationFactory(fail_for={"Ruelle"}, hold=hold)
    session = _make_session(["Ruelle"])
    _persist_world(db_factory, session, stubs=["Ruelle"])

    with patch("bot.world_navigation.generate_destination", fake), _patched_move_env():
        prefetch = asyncio.create_task(
            prefetch_neighbor_locations(session, db_factory=db_factory),
        )
        while not fake.calls:
            await asyncio.sleep(0)
        fake.fail_for = set()  # the retry (sync path) succeeds
        move = asyncio.create_task(
            change_location(session, "Ruelle", db_factory=db_factory),
        )
        await asyncio.sleep(0)
        hold.set()
        dest = await asyncio.wait_for(move, timeout=5)
        await asyncio.wait_for(prefetch, timeout=5)

    assert dest.generated
    assert fake.calls == ["Ruelle", "Ruelle"]  # failed prefetch + sync fallback


async def test_full_lock_topology_no_deadlock_across_two_jobs(db_factory) -> None:
    """F5: exercises the real production lock topology end to end.

    Job 1 (Ruelle) is STARTED and held. While it is in flight, a real
    ``action_lock`` is acquired — simulating a MOVE landing mid-prefetch.
    Releasing the hold lets job 1 complete; the loop must then find
    ``action_lock`` held and park on ``wait_player_idle`` BEFORE starting
    job 2 (Marché) — politeness, not a deadlock. Releasing ``action_lock``
    lets job 2 start and complete. Every wait is deadline-guarded so a
    regression here fails fast instead of hanging the suite.
    """
    hold = asyncio.Event()
    fake = FakeDestinationFactory(hold=hold)
    session = _make_session(["Ruelle", "Marché"])
    session.action_lock = asyncio.Lock()
    _persist_world(db_factory, session, stubs=["Ruelle", "Marché"])

    with patch("bot.world_navigation.generate_destination", fake):
        task = asyncio.create_task(
            prefetch_neighbor_locations(session, db_factory=db_factory),
        )

        # Job 1 (Ruelle) is STARTED and held mid-generation.
        while not fake.calls:
            await asyncio.sleep(0)
        assert fake.calls == ["Ruelle"]

        # A MOVE lands while job 1 is in flight: acquire the real lock.
        await asyncio.wait_for(session.action_lock.acquire(), timeout=5)

        # Let job 1 finish. The loop must now try job 2, hit
        # wait_player_idle, and park on the held action_lock instead of
        # starting a second generation.
        hold.set()
        for _ in range(10):
            await asyncio.sleep(0)
        assert fake.calls == ["Ruelle"]  # job 2 has NOT started — parked

        row = _load(db_factory, "Ruelle", session.campaign.id)
        assert row is not None and row.generated  # job 1 completed cleanly

        # Release the lock — job 2 (Marché) can now start and complete.
        session.action_lock.release()
        count = await asyncio.wait_for(task, timeout=5)

    assert count == 2
    assert sorted(fake.calls) == ["Marché", "Ruelle"]


# ---------------------------------------------------------------------------
# cancel_for_campaign
# ---------------------------------------------------------------------------


async def test_cancel_for_campaign_cancels_running_task(db_factory) -> None:
    """/end_campaign calls this so a dead campaign's loop stops burning the
    shared gate and Ollama capacity for a session nobody plays anymore."""
    hold = asyncio.Event()
    fake = FakeDestinationFactory(hold=hold)
    session = _make_session(["Ruelle"])
    _persist_world(db_factory, session, stubs=["Ruelle"])

    with patch("bot.world_navigation.generate_destination", fake):
        task = schedule_location_prefetch(session, db_factory=db_factory)
        assert task is not None
        while not fake.calls:  # wait until job 1 is genuinely mid-flight
            await asyncio.sleep(0)

        cancelled = location_prefetch.cancel_for_campaign(session.campaign.id)
        assert cancelled == 1

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)

    assert task.cancelled()
    # The in-flight inner job's own bookkeeping is unwound, not leaked.
    assert location_prefetch._STARTED == {}


async def test_cancel_for_campaign_returns_zero_when_nothing_running() -> None:
    assert location_prefetch.cancel_for_campaign("no-such-campaign") == 0


async def test_cancel_for_campaign_only_cancels_matching_campaign(db_factory) -> None:
    """Two campaigns share the process-wide gate; ending one must not touch
    the other's in-flight prefetch loop."""
    hold = asyncio.Event()
    fake = FakeDestinationFactory(hold=hold)
    session_a = _make_session(["Ruelle"])
    session_b = _make_session(["Marché"])
    session_b.campaign = Campaign(name="Autre Campagne", current_location="Place")
    _persist_world(db_factory, session_a, stubs=["Ruelle"])
    _persist_world(db_factory, session_b, stubs=["Marché"])

    with patch("bot.world_navigation.generate_destination", fake):
        task_a = schedule_location_prefetch(session_a, db_factory=db_factory)
        task_b = schedule_location_prefetch(session_b, db_factory=db_factory)
        assert task_a is not None and task_b is not None

        cancelled = location_prefetch.cancel_for_campaign(session_a.campaign.id)
        assert cancelled == 1

        hold.set()  # let task_b's generation (whichever job it reaches) finish
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task_a, timeout=5)
        count_b = await asyncio.wait_for(task_b, timeout=5)

    assert task_a.cancelled()
    assert count_b == 1
    assert fake.calls == ["Marché"]
