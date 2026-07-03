# H8 (suite) — Lobby Pregen Status + Neighbor Location Prefetch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MOVE to an already-prefetched location instantaneous (DB read instead of a ~57-80 s synchronous 9b call) and show the world-generation phase live in the lobby embed.

**Architecture:** A new background prefetcher (`bot/location_prefetch.py`, mirroring `bot/npc_prefetch.py`) generates the *neighbors* of the current location after each arrival. A process-wide gate (`bot/prefetch_gate.py`) caps background generation at one LLM call in flight, and background jobs yield to player actions (`session.action_lock`). `change_location` awaits a *started* prefetch job for its destination (one generation instead of two) but never a queued one (anti-deadlock). Finally, `SessionCog` passes `pregen_status` to the lobby embed and refreshes it on every phase transition.

**Tech Stack:** Python 3.12, asyncio, discord.py 2.4+, SQLAlchemy + SQLite, pytest (asyncio_mode auto), Ollama (qwen3.5:9b via `ai/world_generator.py`).

**Spec:** `docs/superpowers/specs/2026-07-03-h8-location-prefetch-design.md` — read it before starting any task.

## Global Constraints

- Branch: `feat/h8-latency`. Conventional commits, **no AI attribution of any kind** (no `Co-Authored-By`, no Claude mention) — project "undercover mode".
- Run everything through `uv run` (`uv run pytest`, `uv run ruff check .`, `uv run mypy <files>`). Never activate the venv manually.
- `engine/` must never import `ai/` or call an LLM. (This plan only touches `bot/`, `tests/bot/` — keep it that way.)
- All new data models Pydantic v2 / dataclasses per existing file conventions; full type hints; docstrings on public functions.
- LLM calls always via `asyncio.to_thread` — never on the event loop.
- The prefetch writes the WORLD (location rows in DB), never the game state: `session.current_location`, `session.npcs`, `session.campaign` are untouched by prefetch code.
- Invariants from the spec: at most ONE background generation in flight process-wide; a background job never *starts* while a player action is in flight; `change_location` awaits only *started* jobs.
- Concurrency tests need real suspension points and deadline guards (`asyncio.wait_for` around awaited tasks, event-gated fakes) — see `tasks/lessons.md` 2026-06-10.

---

### Task 1: `bot/prefetch_gate.py` — gate + politeness primitives

**Files:**
- Create: `bot/prefetch_gate.py`
- Test: `tests/bot/test_prefetch_gate.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces (used by Tasks 2 and 4):
  - `generation_gate() -> asyncio.Lock` — lazy process-wide singleton.
  - `reset_generation_gate() -> None` — test hook (fresh lock per event loop).
  - `async wait_player_idle(session: Any) -> None` — parks until `session.action_lock` is free; immediate if the attribute is absent or `None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/bot/test_prefetch_gate.py`:

```python
"""Tests for bot/prefetch_gate.py — background-generation coordination (H8)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.prefetch_gate import (
    generation_gate,
    reset_generation_gate,
    wait_player_idle,
)


@pytest.fixture(autouse=True)
def _fresh_gate():
    reset_generation_gate()
    yield
    reset_generation_gate()


async def test_generation_gate_is_a_singleton() -> None:
    assert generation_gate() is generation_gate()


async def test_reset_generation_gate_drops_the_singleton() -> None:
    first = generation_gate()
    reset_generation_gate()
    assert generation_gate() is not first


async def test_gate_serializes_two_holders() -> None:
    order: list[str] = []

    async def hold(tag: str, delay: float) -> None:
        async with generation_gate():
            order.append(f"{tag}:in")
            await asyncio.sleep(delay)
            order.append(f"{tag}:out")

    await asyncio.wait_for(
        asyncio.gather(hold("a", 0.01), hold("b", 0.0)), timeout=5,
    )
    assert order == ["a:in", "a:out", "b:in", "b:out"]


async def test_wait_player_idle_without_action_lock_is_immediate() -> None:
    await asyncio.wait_for(wait_player_idle(SimpleNamespace()), timeout=1)
    await asyncio.wait_for(
        wait_player_idle(SimpleNamespace(action_lock=None)), timeout=1,
    )


async def test_wait_player_idle_waits_for_action_to_finish() -> None:
    session = SimpleNamespace(action_lock=asyncio.Lock())
    await session.action_lock.acquire()
    waiter = asyncio.create_task(wait_player_idle(session))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not waiter.done()
    session.action_lock.release()
    await asyncio.wait_for(waiter, timeout=5)


async def test_wait_player_idle_releases_the_lock_after_itself() -> None:
    session = SimpleNamespace(action_lock=asyncio.Lock())
    await asyncio.wait_for(wait_player_idle(session), timeout=1)
    assert not session.action_lock.locked()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/bot/test_prefetch_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.prefetch_gate'`

- [ ] **Step 3: Write the implementation**

Create `bot/prefetch_gate.py`:

```python
"""Coordination primitives for background LLM generation (chantier I / H8).

Two prefetchers generate content in the background (``bot/npc_prefetch.py``
for NPC sheets, ``bot/location_prefetch.py`` for neighbor locations). Ollama
serves one request at a time on the 18 GB budget, so uncoordinated
background jobs would stack in its queue in front of player actions. This
module enforces two invariants:

- **one background generation in flight process-wide** — a player action
  queued behind background work waits for at most a single LLM call
  (~60 s worst case);
- **background jobs never start while a player action is in flight** —
  :func:`wait_player_idle` parks the job until ``session.action_lock`` is
  free (event-driven acquire/release, no polling).
"""

from __future__ import annotations

import asyncio
from typing import Any

_gate: asyncio.Lock | None = None


def generation_gate() -> asyncio.Lock:
    """Process-wide background-generation gate (lazy singleton).

    Lazy so the lock binds to the running event loop on first use; tests
    (one fresh loop per test) call :func:`reset_generation_gate` between
    runs.
    """
    global _gate
    if _gate is None:
        _gate = asyncio.Lock()
    return _gate


def reset_generation_gate() -> None:
    """Test hook — drop the singleton so the next caller gets a fresh lock."""
    global _gate
    _gate = None


async def wait_player_idle(session: Any) -> None:
    """Return once no player action is in flight on ``session``.

    Acquire/release of ``session.action_lock``: event-driven, held for a
    no-op instant so any player queued behind us pays nothing. Sessions
    without an ``action_lock`` (test doubles, pre-launch contexts) are
    considered idle.
    """
    lock = getattr(session, "action_lock", None)
    if lock is None:
        return
    async with lock:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/bot/test_prefetch_gate.py -v`
Expected: 6 PASS

- [ ] **Step 5: Lint + typecheck**

Run: `uv run ruff check bot/prefetch_gate.py tests/bot/test_prefetch_gate.py && uv run mypy bot/prefetch_gate.py`
Expected: clean / no new errors.

- [ ] **Step 6: Commit**

```bash
git add bot/prefetch_gate.py tests/bot/test_prefetch_gate.py
git commit -m "feat(prefetch): process-wide generation gate + player-idle wait"
```

---

### Task 2: `bot/npc_prefetch.py` goes through the gate

**Files:**
- Modify: `bot/npc_prefetch.py:83-89` (the `asyncio.to_thread` call inside `prefetch_npc_sheets`)
- Test: `tests/bot/test_npc_prefetch.py` (append)

**Interfaces:**
- Consumes: `generation_gate()`, `wait_player_idle(session)` from Task 1.
- Produces: unchanged public API (`prefetch_npc_sheets`, `schedule_npc_prefetch`) — now gate-compliant.

- [ ] **Step 1: Write the failing tests**

Append to `tests/bot/test_npc_prefetch.py` (imports at top of file: add `from bot.prefetch_gate import generation_gate, reset_generation_gate`; `asyncio` is already imported):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/bot/test_npc_prefetch.py -v -k "yields_to_player or generation_gate"`
Expected: both FAIL — `gen.calls == ["Jeanne"]` already at the first assert (no gate yet).

- [ ] **Step 3: Implement**

In `bot/npc_prefetch.py`, add the import after the existing `from db.repositories.npc_repo import NPCRepository`:

```python
from bot.prefetch_gate import generation_gate, wait_player_idle
```

Replace the body of the `try:` block inside the `for name in ...` loop (currently `sheet = await asyncio.to_thread(...)` followed by the race check) so the LLM call runs under the gate, after yielding to any in-flight player action, and re-checks the race *before* paying the call:

```python
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
                sheet = await asyncio.to_thread(
                    generator.generate,
                    npc_name=name,
                    location_context=location_ctx,
                    campaign_theme=campaign_theme,
                    language=language,
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
```

(Only the `async with generation_gate():` wrapper, `wait_player_idle` call, and the pre-call race check are new — the rest is the existing code, unchanged.)

- [ ] **Step 4: Run the full npc_prefetch suite**

Run: `uv run pytest tests/bot/test_npc_prefetch.py -v`
Expected: all PASS (old tests still green — the gate is uncontended there).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check bot/npc_prefetch.py tests/bot/test_npc_prefetch.py
uv run mypy bot/npc_prefetch.py
git add bot/npc_prefetch.py tests/bot/test_npc_prefetch.py
git commit -m "feat(npc-prefetch): route sheet generation through the shared gate"
```

---

### Task 3: extract `generate_destination` in `bot/world_navigation.py`

Pure refactor — the generation block of `change_location` (lines 141-182: WorldGenerator setup, arc hints, `to_thread` call, name forcing, back-link injection) becomes a shared module-level coroutine so the prefetcher (Task 4) reuses the exact same logic.

**Files:**
- Modify: `bot/world_navigation.py:129-187`
- Test: `tests/bot/test_world_navigation.py` (existing suite must stay green — it patches `ai.world_generator.WorldGenerator`, which still works because the lazy import moves with the code)

**Interfaces:**
- Consumes: nothing new.
- Produces (used by Task 4):
  ```python
  async def generate_destination(
      session: "GameSession",
      destination_name: str,
      *,
      origin_name: str,
      required_connections: list[str],
  ) -> Location
  ```
  Raises whatever the generator raises; callers wrap errors. Requires `session.ollama_client` to be set (callers check).

- [ ] **Step 1: Add the helper**

In `bot/world_navigation.py`, insert after `create_exit_stubs` (before `change_location`):

```python
async def generate_destination(
    session: "GameSession",
    destination_name: str,
    *,
    origin_name: str,
    required_connections: list[str],
) -> Location:
    """Generate (or hydrate) ``destination_name`` via :class:`WorldGenerator`.

    Shared by the synchronous MOVE path (:func:`change_location`) and the
    background neighbor prefetch (``bot/location_prefetch.py``) so both
    produce identical locations: arc hints from ``session.story_arc``, the
    requested name forced back on the result, and required back-links
    injected as a safety net. Raises whatever the generator raises —
    callers wrap errors. ``session.ollama_client`` must be set.
    """
    from ai.world_generator import WorldGenerator

    gen = WorldGenerator(session.ollama_client)
    # Pass arc location hints so generated names match the arc.
    arc_hints: list[str] | None = None
    story_arc = getattr(session, "story_arc", None)
    if story_arc is not None:
        arc_hints = [
            beat.location_hint
            for beat in story_arc.beats
            if beat.location_hint
        ]
    new_dest = await asyncio.to_thread(
        gen.generate,
        campaign_context=f"Moving from {origin_name} to {destination_name}",
        location_type="connected_area",
        location_name=destination_name,
        language=session.language,
        location_hints=arc_hints,
        required_connections=required_connections or None,
    )
    # Guarantee name stability even if the LLM rephrased it, since the
    # player asked for this exact destination and the DB row (when it's a
    # stub) is keyed by that name.
    new_dest.name = destination_name
    # Safety net: force-inject required back-links in case the
    # world_generator filter let something slip through.
    for req in required_connections:
        if req and req not in new_dest.connections:
            new_dest.connections = [*new_dest.connections, req]
    return new_dest
```

- [ ] **Step 2: Rewire `change_location` to use it**

Replace the `if needs_generation:` block of `change_location` (currently lines 133-187, from `if needs_generation:` down to the `raise LocationChangeError(... generation failed ...)`) with:

```python
    if needs_generation:
        if session.ollama_client is None:
            raise LocationChangeError(
                destination_name,
                "no DB entry and Ollama unavailable"
                if dest is None
                else "stub hydration needs Ollama",
            )
        try:
            # When hydrating a stub, preserve any back-links it already knows
            # about (at least the parent we came from). When creating from
            # scratch, enforce a back-link to the current location so the
            # player can always return.
            required: list[str] = []
            if dest is not None:
                required = list(dest.connections)
            elif current_name and current_name != "unknown":
                required = [current_name]
            dest = await generate_destination(
                session,
                destination_name,
                origin_name=current_name,
                required_connections=required,
            )
            created_stub_or_full = True
        except Exception as exc:  # noqa: BLE001
            raise LocationChangeError(
                destination_name, f"generation failed: {exc}",
            ) from exc
```

- [ ] **Step 3: Run the existing suite to prove the refactor is invisible**

Run: `uv run pytest tests/bot/test_world_navigation.py -v`
Expected: all PASS, zero test edits.

- [ ] **Step 4: Lint + typecheck + commit**

```bash
uv run ruff check bot/world_navigation.py
uv run mypy bot/world_navigation.py
git add bot/world_navigation.py
git commit -m "refactor(navigation): extract shared generate_destination helper"
```

---

### Task 4: `bot/location_prefetch.py` — the neighbor prefetcher

**Files:**
- Create: `bot/location_prefetch.py`
- Test: `tests/bot/test_location_prefetch.py`

**Interfaces:**
- Consumes: `generation_gate()` / `wait_player_idle()` (Task 1), `generate_destination()` / `create_exit_stubs()` (Task 3), `LocationRepository` (get_by_name/upsert).
- Produces (used by Tasks 5 and 6):
  - `schedule_location_prefetch(session, *, db_factory) -> asyncio.Task[int] | None`
  - `async prefetch_neighbor_locations(session, *, db_factory) -> int`
  - `async wait_for_started_job(campaign_id: str, location_name: str, *, timeout: float = 180.0) -> bool`
  - `reset_for_tests() -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/bot/test_location_prefetch.py`. Mirrors `tests/bot/test_npc_prefetch.py`: real in-memory SQLite repositories, fake generation by patching `bot.world_navigation.generate_destination` (the lazy import inside the prefetcher resolves through the module attribute, so the patch is seen by prefetch and MOVE alike).

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/bot/test_location_prefetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.location_prefetch'`

- [ ] **Step 3: Write the implementation**

Create `bot/location_prefetch.py`:

```python
"""Background neighbor-location pre-generation (chantier I / finding H8).

A MOVE to a never-generated location pays ~57-80 s of synchronous
WorldGenerator (9b) inside the action pipeline (``bot/world_navigation.py``).
This module generates the *neighbors* of the current location in a
background task scheduled on arrival (campaign launch, MOVE, /resume), so
the next MOVE finds a fully generated row in the DB and resolves instantly.

Design constraints (mirrors ``bot/npc_prefetch.py``):
- the LLM call always runs through ``asyncio.to_thread`` — never on the
  event loop;
- at most ONE background generation in flight process-wide
  (``bot/prefetch_gate.py``), and a job never *starts* while a player
  action is in flight on the session;
- the prefetch writes the WORLD (location rows), never the game state:
  ``session.current_location`` / ``session.npcs`` / ``session.campaign``
  are left untouched;
- graceful degradation: any failure leaves the synchronous MOVE path
  exactly as it is today.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from bot.prefetch_gate import generation_gate, wait_player_idle
from db.repositories.location_repo import LocationRepository

if TYPE_CHECKING:
    from bot.game_session import GameSession
    from world.location import Location
    from world.story_arc import StoryArc

logger = logging.getLogger(__name__)

# Strong references to in-flight prefetch loop tasks — asyncio keeps only
# weak references to tasks, so without this registry a running prefetch
# could be garbage-collected mid-flight.
_TASKS: set[asyncio.Task[Any]] = set()

# (campaign_id, location_name) → the inner task actually running the LLM
# call + persistence. An entry exists ONLY between "generation started"
# (gate + player-idle acquired) and "row persisted or failed" — never while
# merely queued. ``change_location`` awaits these via
# :func:`wait_for_started_job`; queued-but-not-started jobs are invisible
# here on purpose: they wait on ``action_lock``, which a MOVE holds, so
# awaiting one would deadlock.
_STARTED: dict[tuple[str, str], "asyncio.Task[bool]"] = {}


def schedule_location_prefetch(
    session: "GameSession",
    *,
    db_factory: Callable[[], Any],
) -> "asyncio.Task[int] | None":
    """Spawn :func:`prefetch_neighbor_locations` as a background task.

    Returns ``None`` (no-op) when there is nothing to do: no Ollama client
    on the session, no current location, no pending neighbor, or no running
    event loop (sync callers — the prefetch is best-effort).
    """
    if getattr(session, "ollama_client", None) is None:
        return None
    location = getattr(session, "current_location", None)
    if location is None:
        return None
    try:
        pending = _pending_neighbors(session, db_factory=db_factory)
    except Exception:
        logger.warning("Location prefetch: pending scan failed", exc_info=True)
        return None
    if not pending:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    campaign_id = str(getattr(session.campaign, "id", ""))
    task = loop.create_task(
        prefetch_neighbor_locations(session, db_factory=db_factory),
        name=f"location-prefetch:{campaign_id}",
    )
    _TASKS.add(task)
    task.add_done_callback(_on_task_done)
    logger.info(
        "Location prefetch scheduled campaign=%s parent=%s pending=%d",
        campaign_id, location.name, len(pending),
    )
    return task


async def prefetch_neighbor_locations(
    session: "GameSession",
    *,
    db_factory: Callable[[], Any],
) -> int:
    """Generate every pending neighbor of the current location, one by one.

    Neighbors are processed sequentially (one local model, no benefit in
    flooding the Ollama queue), most-likely-destination first (arc beat
    hints). A failure on one neighbor is logged and does not block the
    others. Returns the number of locations generated and persisted.
    """
    parent = getattr(session, "current_location", None)
    if parent is None:
        return 0
    parent_name = parent.name
    campaign_id = str(getattr(session.campaign, "id", ""))
    generated = 0

    for name in _pending_neighbors(session, db_factory=db_factory):
        if not _still_at(session, parent_name):
            break  # the party moved on — this queue is stale
        key = (campaign_id, name)
        if key in _STARTED:
            continue  # another prefetch run is already generating it
        try:
            async with generation_gate():
                # A player action may have started while we waited for the
                # gate — the fond always yields priority (H8).
                await wait_player_idle(session)
                if not _still_at(session, parent_name):
                    break
                row = _load_row(name, campaign_id, db_factory)
                if row is not None and row.generated:
                    continue  # a sync MOVE generated it while we waited
                required = (
                    list(row.connections) if row is not None else [parent_name]
                )
                inner: asyncio.Task[bool] = asyncio.get_running_loop().create_task(
                    _generate_and_persist(
                        session,
                        name,
                        origin_name=parent_name,
                        required_connections=required,
                        db_factory=db_factory,
                    ),
                    name=f"location-prefetch-job:{campaign_id}:{name}",
                )
                _STARTED[key] = inner
                try:
                    if await inner:
                        generated += 1
                finally:
                    _STARTED.pop(key, None)
        except Exception:
            logger.exception("Location prefetch failed for %r", name)

    if generated:
        logger.info(
            "Location prefetch campaign=%s parent=%s generated=%d",
            campaign_id, parent_name, generated,
        )
    return generated


async def wait_for_started_job(
    campaign_id: str,
    location_name: str,
    *,
    timeout: float = 180.0,
) -> bool:
    """Await an in-flight prefetch generation for ``location_name``, if any.

    Returns ``True`` when a STARTED job existed and was awaited — even if
    it failed: the caller re-reads the DB and falls back to its own sync
    generation. Returns ``False`` when no job has started for this
    destination. Never raises; a timeout abandons the wait without
    cancelling the job (``asyncio.shield``).
    """
    inner = _STARTED.get((str(campaign_id), location_name))
    if inner is None:
        return False
    try:
        await asyncio.wait_for(asyncio.shield(inner), timeout=timeout)
    except Exception:  # noqa: BLE001 — job errors are the loop's to log
        logger.warning(
            "wait_for_started_job: job for %r did not complete cleanly",
            location_name,
        )
    return True


def reset_for_tests() -> None:
    """Drop module registries (tests run one event loop per test)."""
    _TASKS.clear()
    _STARTED.clear()


async def _generate_and_persist(
    session: "GameSession",
    destination_name: str,
    *,
    origin_name: str,
    required_connections: list[str],
    db_factory: Callable[[], Any],
) -> bool:
    """One LLM generation + upsert, as its own task so a MOVE to this exact
    destination can await it (see :func:`wait_for_started_job`)."""
    from bot.world_navigation import create_exit_stubs, generate_destination

    campaign_id = str(getattr(session.campaign, "id", ""))
    dest = await generate_destination(
        session,
        destination_name,
        origin_name=origin_name,
        required_connections=required_connections,
    )
    db_session = db_factory()
    try:
        repo = LocationRepository(db_session)
        repo.upsert(dest, campaign_id)
        create_exit_stubs(
            repo,
            dest.connections,
            parent_name=dest.name,
            campaign_id=campaign_id,
        )
        db_session.commit()
    finally:
        db_session.close()
    return True


def _still_at(session: "GameSession", parent_name: str) -> bool:
    current = getattr(session, "current_location", None)
    return current is not None and current.name == parent_name


def _load_row(
    name: str, campaign_id: str, db_factory: Callable[[], Any],
) -> "Location | None":
    db_session = db_factory()
    try:
        return LocationRepository(db_session).get_by_name(name, campaign_id)
    finally:
        db_session.close()


def _pending_neighbors(
    session: "GameSession",
    *,
    db_factory: Callable[[], Any],
) -> list[str]:
    """Connections of the current location whose row is absent or a stub."""
    location = session.current_location
    campaign_id = str(getattr(session.campaign, "id", ""))
    pending: list[str] = []
    db_session = db_factory()
    try:
        repo = LocationRepository(db_session)
        for raw in location.connections:
            name = (raw or "").strip()
            if not name or name == location.name or name in pending:
                continue
            row = repo.get_by_name(name, campaign_id)
            if row is None or not row.generated:
                pending.append(name)
    finally:
        db_session.close()
    return _priority_order(pending, getattr(session, "story_arc", None))


def _priority_order(
    pending: list[str], story_arc: "StoryArc | None",
) -> list[str]:
    """Current beat's ``location_hint`` first, then the next beat's, then
    the rest in connection order — the hinted exit is where the arc sends
    the party next."""
    if story_arc is None or len(pending) < 2:
        return pending
    beats = getattr(story_arc, "beats", None) or []
    idx = getattr(story_arc, "current_beat_index", 0)
    hinted: list[str] = []
    for i in (idx, idx + 1):
        if 0 <= i < len(beats):
            hint = getattr(beats[i], "location_hint", "") or ""
            if hint in pending and hint not in hinted:
                hinted.append(hint)
    return hinted + [n for n in pending if n not in hinted]


def _on_task_done(task: asyncio.Task[Any]) -> None:
    _TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:  # pragma: no cover — the loop traps its own errors
        logger.error("Location prefetch task crashed", exc_info=exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/bot/test_location_prefetch.py -v`
Expected: 14 PASS

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check bot/location_prefetch.py tests/bot/test_location_prefetch.py
uv run mypy bot/location_prefetch.py
git add bot/location_prefetch.py tests/bot/test_location_prefetch.py
git commit -m "feat(location-prefetch): pre-generate neighbor locations in background"
```

---

### Task 5: `change_location` awaits started prefetch jobs

**Files:**
- Modify: `bot/world_navigation.py` (inside `change_location`, right after `needs_generation` is computed)
- Test: `tests/bot/test_location_prefetch.py` (append a `TestMoveRace` section)

**Interfaces:**
- Consumes: `wait_for_started_job()` from Task 4.
- Produces: `change_location` behavior — awaits a STARTED prefetch job for its destination, re-reads the DB, and only falls back to its own sync generation if the row still isn't generated.

- [ ] **Step 1: Write the failing tests**

Append to `tests/bot/test_location_prefetch.py`:

```python
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
```

Note: `change_location` in these tests runs against the real in-memory
repositories (`_persist_world` already saved the campaign row), unlike the
MagicMock style of `tests/bot/test_world_navigation.py` — the race is about
what lands in the DB, so the DB must be real.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/bot/test_location_prefetch.py -v -k "move_"`
Expected: `test_move_awaits_started_job...` FAILS (two generations: `fake.calls == ["Ruelle", "Ruelle"]` — change_location doesn't await the started job yet). The other two may already pass; confirm the first fails for the right reason.

- [ ] **Step 3: Implement**

In `bot/world_navigation.py`, inside `change_location`, insert between `needs_generation = dest is None or not dest.generated` / `created_stub_or_full = False` and the `if needs_generation:` block:

```python
    if needs_generation:
        # H8: a background neighbor prefetch may already be generating this
        # exact destination — await the STARTED job (one generation instead
        # of two queued in Ollama), then re-read the DB. Queued-but-not-
        # started jobs return False immediately: they wait on the very
        # action_lock this MOVE is holding.
        from bot.location_prefetch import wait_for_started_job

        if await wait_for_started_job(str(campaign_id), destination_name):
            db_session = db_factory()
            try:
                dest = LocationRepository(db_session).get_by_name(
                    destination_name, campaign_id,
                )
            finally:
                db_session.close()
            needs_generation = dest is None or not dest.generated
```

- [ ] **Step 4: Run the race tests + both navigation suites**

Run: `uv run pytest tests/bot/test_location_prefetch.py tests/bot/test_world_navigation.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check bot/world_navigation.py tests/bot/test_location_prefetch.py
uv run mypy bot/world_navigation.py
git add bot/world_navigation.py tests/bot/test_location_prefetch.py
git commit -m "feat(navigation): MOVE awaits an in-flight neighbor prefetch"
```

---

### Task 6: scheduling wiring — scene hydration + /resume

**Files:**
- Modify: `bot/scene_hydration.py` (import at top + one call after `schedule_npc_prefetch(...)` at the end of `hydrate_scene`, line 328)
- Modify: `bot/cogs/session.py` (resume path — right after `self.bot.sessions[channel_id] = session`, around line 1017)
- Test: `tests/bot/test_scene_hydration.py` (append), `tests/bot/test_cog_session.py` (append to `TestResume`)

**Interfaces:**
- Consumes: `schedule_location_prefetch()` from Task 4.
- Produces: prefetch fires on every arrival — launch and MOVE both flow through `hydrate_scene`; `/resume` gets its own call.

- [ ] **Step 1: Write the failing tests**

Append to `tests/bot/test_scene_hydration.py` (that file already defines the `db_factory` fixture, `_make_session`, and `_persist_campaign_and_location`; extend its `from unittest.mock import MagicMock` line to `from unittest.mock import MagicMock, patch`):

```python
def test_hydrate_schedules_location_prefetch_after_npc_prefetch(db_factory) -> None:
    """H8: every arrival pre-generates the neighbors, after the NPC sheets
    (4b jobs first through the shared gate, 9b jobs second — one model
    swap per arrival)."""
    location = Location(name="Place", npcs_present=[], connections=["Ruelle"])
    session = _make_session(location=location)
    _persist_campaign_and_location(db_factory, session)

    calls: list[str] = []
    with (
        patch(
            "bot.scene_hydration.schedule_npc_prefetch",
            side_effect=lambda *a, **k: calls.append("npc"),
        ),
        patch(
            "bot.scene_hydration.schedule_location_prefetch",
            side_effect=lambda *a, **k: calls.append("location"),
        ),
    ):
        hydrate_scene(session, db_factory=db_factory)

    assert calls == ["npc", "location"]
```

Append to the `TestResume` class in `tests/bot/test_cog_session.py` (uses that file's existing `cog` / `interaction` / `persisted_campaign` / `persisted_channel` fixtures; `patch`, `MagicMock`, `AsyncMock` are already imported there). Patch target is `bot.location_prefetch.schedule_location_prefetch` because session.py imports it lazily inside `resume`, so the module attribute is resolved at call time:

```python
    @pytest.mark.asyncio
    @patch("bot.location_prefetch.schedule_location_prefetch")
    @patch("bot.cogs.session.create_ai_services")
    async def test_resume_schedules_location_prefetch(
        self,
        mock_ai: MagicMock,
        mock_sched: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        persisted_channel: int,
    ) -> None:
        await cog.resume.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        mock_sched.assert_called_once()
        assert mock_sched.call_args.args[0] is cog.bot.sessions[CHANNEL_ID]
```

(Decorator stacking: the decorator closest to the function is the first
mock parameter — `mock_ai` before `mock_sched`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/bot/test_scene_hydration.py tests/bot/test_cog_session.py -v -k "location_prefetch"`
Expected: FAIL — `AttributeError`/`ImportError` (no `schedule_location_prefetch` in `bot.scene_hydration`) and `sched.called is False`.

- [ ] **Step 3: Implement**

In `bot/scene_hydration.py`:
- top of file, next to `from bot.npc_prefetch import schedule_npc_prefetch` (line 31), add:

```python
from bot.location_prefetch import schedule_location_prefetch
```

- at the end of `hydrate_scene`, right after `schedule_npc_prefetch(session, db_factory=db_factory)` (line 328), add:

```python
    # Chantier I (H8, suite): pre-generate this location's missing neighbors
    # so the next MOVE is a DB read instead of a ~57-80 s synchronous
    # WorldGenerator call. Scheduled after the NPC prefetch: NPC jobs (4b)
    # go through the shared gate first, location jobs (9b) after — one
    # model swap per arrival.
    schedule_location_prefetch(session, db_factory=db_factory)
```

In `bot/cogs/session.py`, in `resume`, right after `self.bot.sessions[channel_id] = session`:

```python
        # H8 (suite): the resumed location may still have stub neighbors.
        from bot.location_prefetch import schedule_location_prefetch

        schedule_location_prefetch(session, db_factory=self.bot.db_factory)
```

- [ ] **Step 4: Run the touched suites**

Run: `uv run pytest tests/bot/test_scene_hydration.py tests/bot/test_cog_session.py tests/bot/test_location_prefetch.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check bot/scene_hydration.py bot/cogs/session.py
uv run mypy bot/scene_hydration.py bot/cogs/session.py
git add bot/scene_hydration.py bot/cogs/session.py tests/bot/test_scene_hydration.py tests/bot/test_cog_session.py
git commit -m "feat(prefetch): schedule neighbor prefetch on arrival and /resume"
```

---

### Task 7: lobby embed shows the pregen phase live

**Files:**
- Modify: `bot/cogs/session.py:475-481` (`_refresh_lobby_embed`) and `:605-683` (`_pregenerate_campaign_world`), plus one new private method.
- Test: `tests/bot/test_cog_session.py` (new class)

**Interfaces:**
- Consumes: `build_lobby_embed(..., pregen_status=...)` (already shipped, `bot/embeds/lobby_embed.py:49`), `lobby.pregen_phase` (`bot/lobby_state.py:78`).
- Produces: lobby embed carries the « 🌍 Génération du monde » field on every refresh; the embed refreshes at every phase transition.

- [ ] **Step 1: Write the failing tests**

Append to `tests/bot/test_cog_session.py` (add to the file's imports if missing: `from types import SimpleNamespace`, `from unittest.mock import AsyncMock, MagicMock, patch`):

```python
class TestLobbyPregenStatus:
    """H8 — the lobby embed shows the world-generation phase live."""

    def _make_lobby(self):
        from bot.lobby_state import GenerationPhase, LobbyState

        message = MagicMock()
        message.guild = MagicMock()
        message.guild.get_member.return_value = None
        message.edit = AsyncMock()
        lobby = LobbyState(
            creator_id=42,
            language="fr",
            campaign_name="Brumes du Nord",
            theme="Brumes du Nord",
        )
        lobby.lobby_message = message
        lobby.pregen_phase = GenerationPhase.ARC
        return lobby, message

    async def test_refresh_lobby_embed_passes_pregen_status(self):
        from bot.cogs.session import SessionCog

        lobby, message = self._make_lobby()
        cog = SessionCog.__new__(SessionCog)  # method under test needs no bot
        await cog._refresh_lobby_embed(lobby, lobby.lobby_message.guild)
        embed = message.edit.call_args.kwargs["embed"]
        assert any(
            "Génération du monde" in (field.name or "")
            for field in embed.fields
        )

    async def test_pregen_status_refresh_is_best_effort(self):
        from bot.cogs.session import SessionCog

        lobby, message = self._make_lobby()
        message.edit.side_effect = RuntimeError("discord down")
        cog = SessionCog.__new__(SessionCog)
        await cog._refresh_lobby_pregen_status(lobby)  # must not raise

    async def test_pregen_status_refresh_noop_without_message(self):
        from bot.cogs.session import SessionCog

        lobby, _ = self._make_lobby()
        lobby.lobby_message = None
        cog = SessionCog.__new__(SessionCog)
        await cog._refresh_lobby_pregen_status(lobby)  # must not raise

    async def test_pregen_refreshes_lobby_on_each_phase(self):
        from bot.cogs.session import SessionCog
        from bot.lobby_state import GenerationPhase
        from world.campaign import Campaign
        from world.location import Location

        lobby, _ = self._make_lobby()
        lobby.pregen_phase = GenerationPhase.PENDING
        cog = SessionCog.__new__(SessionCog)
        phases: list[GenerationPhase] = []

        async def record(lb) -> None:
            phases.append(lb.pregen_phase)

        cog._refresh_lobby_pregen_status = record  # type: ignore[method-assign]

        fake_arc = MagicMock()
        fake_arc.model_copy.return_value = SimpleNamespace(
            campaign_id="c1", beats=[], villain_name="L'Ombre",
        )
        fake_loc = Location(name="Place", description="d", generated=True)
        with (
            patch("ai.client.OllamaClient"),
            patch("engine.arc_recipes.generate_recipe"),
            patch("ai.arc_generator.ArcGenerator") as arc_cls,
            patch("ai.world_generator.WorldGenerator") as world_cls,
        ):
            arc_cls.return_value.generate.return_value = fake_arc
            world_cls.return_value.generate.return_value = fake_loc
            await cog._pregenerate_campaign_world(
                lobby, Campaign(name="Brumes du Nord"), "fr",
            )

        assert phases == [
            GenerationPhase.ARC,
            GenerationPhase.LOCATION,
            GenerationPhase.READY,
        ]

    async def test_pregen_refreshes_lobby_on_failure(self):
        from bot.cogs.session import SessionCog
        from bot.lobby_state import GenerationPhase
        from world.campaign import Campaign

        lobby, _ = self._make_lobby()
        lobby.pregen_phase = GenerationPhase.PENDING
        cog = SessionCog.__new__(SessionCog)
        phases: list[GenerationPhase] = []

        async def record(lb) -> None:
            phases.append(lb.pregen_phase)

        cog._refresh_lobby_pregen_status = record  # type: ignore[method-assign]

        with (
            patch("ai.client.OllamaClient"),
            patch("engine.arc_recipes.generate_recipe"),
            patch("ai.arc_generator.ArcGenerator") as arc_cls,
            patch("ai.world_generator.WorldGenerator"),
        ):
            arc_cls.return_value.generate.side_effect = RuntimeError("boom")
            await cog._pregenerate_campaign_world(
                lobby, Campaign(name="Brumes du Nord"), "fr",
            )

        assert phases[-1] == GenerationPhase.FAILED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/bot/test_cog_session.py -v -k "LobbyPregen"`
Expected: FAIL — no « Génération du monde » field; `AttributeError: _refresh_lobby_pregen_status`.

- [ ] **Step 3: Implement**

In `bot/cogs/session.py`:

1. `_refresh_lobby_embed` (line 475): add the kwarg to the `build_lobby_embed` call:

```python
        new_embed = build_lobby_embed(
            campaign_name=lobby.campaign_name,
            theme=lobby.theme,
            host_name=host_name,
            roster=roster,
            language=lobby.language,
            pregen_status=lobby.pregen_phase,
        )
```

2. New method just below `_refresh_lobby_embed`:

```python
    async def _refresh_lobby_pregen_status(self, lobby: LobbyState) -> None:
        """Best-effort lobby embed refresh on a pregen phase transition.

        A Discord edit failure must never fail the pre-generation task —
        the phases keep advancing and the launch path stays intact.
        """
        message = lobby.lobby_message
        if message is None or message.guild is None:
            return
        try:
            await self._refresh_lobby_embed(lobby, message.guild)
        except Exception:
            logger.warning("PREGEN status refresh failed", exc_info=True)
```

3. In `_pregenerate_campaign_world`, add `await self._refresh_lobby_pregen_status(lobby)` immediately after **every** `lobby.pregen_phase = ...` assignment — six sites:
   - after `= GenerationPhase.FAILED` in the `OllamaClient()` except (line 624-625, before the `return`)
   - after `= GenerationPhase.ARC` (line 637)
   - after `= GenerationPhase.LOCATION` (line 649)
   - after `= GenerationPhase.READY` (line 673)
   - after `= GenerationPhase.FAILED` in the `OllamaUnavailableError` handler (line 675-676)
   - after `= GenerationPhase.FAILED` in the generic `Exception` handler (line 681-682)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/bot/test_cog_session.py -v`
Expected: all PASS (the new class plus the whole existing file).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check bot/cogs/session.py tests/bot/test_cog_session.py
uv run mypy bot/cogs/session.py
git add bot/cogs/session.py tests/bot/test_cog_session.py
git commit -m "feat(lobby): live world-generation progress in the lobby embed"
```

---

### Task 8: full verification + docs

**Files:**
- Modify: `docs/audits/2026-06-10-h8-latency-measurements.md` (replace the « Reste à câbler » section), `tasks/todo.md` (chantier I entry)

**Interfaces:** none — verification gate.

- [ ] **Step 1: Full quality gates**

```bash
uv run pytest
uv run ruff check .
uv run mypy bot/prefetch_gate.py bot/location_prefetch.py bot/npc_prefetch.py bot/world_navigation.py bot/scene_hydration.py bot/cogs/session.py
```

Expected: pytest fully green (baseline: 2578 passed, 1 skipped before this plan — expect ~+25); ruff clean; mypy 0 errors on the touched files (repo baseline of 362 pre-existing test-typing errors unchanged — none added). If the known flaky `tests/bot/test_cog_inventory.py::TestUseItem` fails, re-run once and note it (see todo.md 2026-06-10 note).

- [ ] **Step 2: Update the measurements doc**

In `docs/audits/2026-06-10-h8-latency-measurements.md`, replace the final « Reste à câbler (chantier session-cog) » section with:

```markdown
## Suite du chantier (2026-07-03)

Câblé : `bot/cogs/session.py` passe `pregen_status=lobby.pregen_phase` à
`build_lobby_embed` et rafraîchit l'embed à chaque transition de phase —
la progression est visible en live dans le lobby.

Prefetch des lieux voisins (`bot/location_prefetch.py` + gate global
`bot/prefetch_gate.py`) : après chaque arrivée (lancement, MOVE, /resume),
les voisins non générés du lieu courant sont générés en tâche de fond —
un MOVE vers un lieu préfetché devient une lecture DB (~57-80 s → <2 s
attendu). Au plus une génération de fond en vol (gate partagé avec le
prefetch NPC) ; un job ne démarre jamais pendant une action joueur ;
`change_location` attend un job déjà démarré pour sa destination (une
génération au lieu de deux). Mesures réelles à faire sur une session
Discord live (chantier discord-live-testing).
```

- [ ] **Step 3: Update tasks/todo.md**

Under « Plan de correction de l'audit — 9 chantiers spawnés (2026-06-10) », check the chantier I line and annotate:

```markdown
- [x] I. Latence (H8) — ai/arc_generator, world_generator, npc_generator, embeds progression
      (2026-07-03 : arc amaigri + pregen lobby + prefetch NPC déjà mergés ;
      cette passe ajoute le câblage pregen_status dans l'embed lobby et le
      prefetch des lieux voisins — spec docs/superpowers/specs/
      2026-07-03-h8-location-prefetch-design.md, plan docs/superpowers/
      plans/2026-07-03-h8-location-prefetch.md. Reste : mesure live Discord.)
```

- [ ] **Step 4: Commit**

```bash
git add docs/audits/2026-06-10-h8-latency-measurements.md tasks/todo.md
git commit -m "docs(h8): wire-up + neighbor prefetch measurements notes"
```
