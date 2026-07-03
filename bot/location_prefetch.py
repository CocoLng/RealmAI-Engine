"""Background neighbor-location pre-generation (chantier I / finding H8).

A MOVE to a never-generated location pays ~57-80 s of synchronous
WorldGenerator (9b) inside the action pipeline (``bot/world_navigation.py``).
This module generates the *neighbors* of the current location in a
background task scheduled on arrival (campaign launch, MOVE, /resume), so
the next MOVE finds a fully generated row in the DB and resolves instantly.

Design constraints (mirrors ``bot/npc_prefetch.py``):
- the LLM call runs through ``asyncio.to_thread`` inside
  ``generate_destination`` (``bot/world_navigation.py``), which this module
  delegates to for both the initial generation and stub hydration — never
  on the event loop;
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


def _combat_active(session: "GameSession") -> bool:
    """Whether ``session`` is currently in an active combat encounter.

    Mirrors the idiom used throughout ``bot/combat_turn_manager.py`` and
    ``bot/pipeline/resolve.py`` (``combat_state is not None and
    combat_state.is_active``). Tolerant of test doubles that don't set
    ``combat_state`` at all.
    """
    state = getattr(session, "combat_state", None)
    return state is not None and bool(getattr(state, "is_active", False))


def schedule_location_prefetch(
    session: "GameSession",
    *,
    db_factory: Callable[[], Any],
) -> "asyncio.Task[int] | None":
    """Spawn :func:`prefetch_neighbor_locations` as a background task.

    Returns ``None`` (no-op) when there is nothing to do: no Ollama client
    on the session, no current location, no pending neighbor, active combat,
    or no running event loop (sync callers — the prefetch is best-effort).
    """
    if getattr(session, "ollama_client", None) is None:
        return None
    location = getattr(session, "current_location", None)
    if location is None:
        return None
    if _combat_active(session):
        # Between combat turns action_lock is free, so a 9b prefetch job
        # could start and the next turn would queue behind it — the
        # arrival hook re-schedules on the next MOVE, so just skip for now.
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
        if _combat_active(session):
            # Combat can last minutes; abandon the queue rather than park
            # and poll. The arrival hook re-schedules on the next MOVE, so
            # nothing is lost — just deferred until combat ends.
            break
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
                if _combat_active(session):
                    # Combat may have started while we waited for the gate
                    # — same reasoning as above: break, don't park.
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
    except TimeoutError:
        # The job is still running (shield kept it alive) — the caller
        # re-reads the DB, finds it not yet generated, and sync-generates
        # itself. That sync call and this abandoned job now race to
        # persist; last upsert wins. Rare (180 s) and self-healing (the
        # row ends up generated either way), but worth naming explicitly.
        logger.warning(
            "wait_for_started_job: job for %r timed out after %.0fs — "
            "still running, caller falls back to sync generation",
            location_name, timeout,
        )
    except Exception:  # noqa: BLE001 — job errors are the loop's to log
        logger.warning(
            "wait_for_started_job: job for %r failed",
            location_name,
        )
    return True


def cancel_for_campaign(campaign_id: str) -> int:
    """Cancel every running background prefetch loop for ``campaign_id``.

    Called from ``/end_campaign`` right before the session is dropped: a
    stale loop's ``session.current_location`` never changes again, so left
    running it would keep burning the shared gate
    (``bot/prefetch_gate.py``) and Ollama capacity generating neighbors for
    a campaign nobody plays anymore, delaying prefetch for other active
    campaigns. Cancelling the loop task also cancels its in-flight
    generation job when one is running — :func:`prefetch_neighbor_locations`
    awaits that job directly (unshielded), so asyncio's normal task-chain
    cancellation reaches it too. Returns the number of tasks cancelled.
    """
    name = f"location-prefetch:{campaign_id}"
    cancelled = 0
    for task in list(_TASKS):
        if task.get_name() == name and not task.done():
            task.cancel()
            cancelled += 1
    return cancelled


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
    if location is None:
        return []
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
