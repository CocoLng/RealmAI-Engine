"""Shared location-change helper.

Used by both the legacy ``/move`` command and the free-text MOVE branch of
:class:`bot.action_pipeline.ActionPipeline`. Loads the destination from the
DB if it exists, generates (or hydrates) it via
:class:`ai.world_generator.WorldGenerator` otherwise, then mutates the session
and persists campaign + location + npcs.

Every location that is fully generated also triggers creation of
lightweight *stubs* for each of its ``connections`` that does not yet have a
row — see :func:`create_exit_stubs`. A stub is a ``LocationRow`` with
``generated=False`` whose only known connection is a back-link to its parent.
When the player first visits a stub, it is hydrated in place: the LLM is
called again with ``required_connections=[parent_name]`` so the back-link is
preserved, and the row is updated in place.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from world.location import Location

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


class LocationChangeError(RuntimeError):
    """Raised when ``change_location`` cannot reach the destination."""

    def __init__(self, destination: str, reason: str = "") -> None:
        self.destination = destination
        self.reason = reason
        super().__init__(
            f"Cannot change location to {destination!r}"
            + (f": {reason}" if reason else ""),
        )


def create_exit_stubs(
    loc_repo: LocationRepository,
    connections: list[str],
    *,
    parent_name: str,
    campaign_id: str,
) -> int:
    """Create a stub :class:`LocationRow` for every connection that does not
    already exist in the database.

    A stub is a ``Location`` with ``generated=False``, an empty description,
    and a single connection entry pointing back to ``parent_name``. If a row
    with the same name already exists in the campaign, it is left untouched
    except that the back-link to ``parent_name`` is added to its
    ``connections`` list if missing (this is how bidirectional navigation
    survives across generations).

    The caller is responsible for providing the ``LocationRepository``. This
    keeps the helper trivially patch-friendly in tests and lets callers
    reuse an already-open repo bound to an active session.

    Returns the number of brand-new stubs created.
    """
    if not connections:
        return 0

    created = 0
    for name in connections:
        clean = (name or "").strip()
        if not clean or clean == parent_name:
            continue
        existing = loc_repo.get_by_name(clean, campaign_id)
        if existing is None:
            stub = Location(
                name=clean,
                description="",
                connections=[parent_name],
                generated=False,
            )
            loc_repo.upsert(stub, campaign_id)
            created += 1
            continue
        # Row already exists — ensure the back-link is present so the
        # player can always return where they came from. Both stubs and
        # fully-generated rows benefit from this guarantee.
        if parent_name not in existing.connections:
            existing.connections = [*existing.connections, parent_name]
            loc_repo.upsert(existing, campaign_id)
    return created


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

    assert session.ollama_client is not None
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


async def change_location(
    session: "GameSession",
    destination_name: str,
    *,
    db_factory: Callable[[], Any],
) -> Location:
    """Move ``session`` to ``destination_name``.

    Loads the destination from the DB; if absent or present as a stub,
    generates (or hydrates) it via :class:`WorldGenerator` (requires
    ``session.ollama_client``). Persists the new location, updates
    ``Campaign.current_location``, and reloads ``session.npcs`` from the
    destination.

    Raises :class:`LocationChangeError` if the destination cannot be obtained.
    """
    campaign_id = session.campaign.id
    current = session.current_location
    current_name = current.name if current is not None else "unknown"

    # 1. Try existing DB location. Synchronous SQLAlchemy — keep it off
    # the event loop (M4).
    def _load_destination() -> Location | None:
        db_session = db_factory()
        try:
            return LocationRepository(db_session).get_by_name(
                destination_name, campaign_id,
            )
        finally:
            db_session.close()

    dest: Location | None = await asyncio.to_thread(_load_destination)

    # 2. Decide whether we need to call the LLM (brand-new OR stub).
    needs_generation = dest is None or not dest.generated
    created_stub_or_full = False

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

    assert dest is not None
    final_dest: Location = dest

    # Mutate in-memory state on the event loop — other coroutines may read
    # the session while the persist below runs in a worker thread.
    session.current_location = final_dest
    session.campaign.current_location = final_dest.name

    # 3. Persist (upsert) and update campaign / npcs / stubs — synchronous
    # SQLAlchemy, off the event loop (M4).
    def _persist_and_reload_npcs() -> list:
        db_session = db_factory()
        try:
            loc_repo = LocationRepository(db_session)
            if created_stub_or_full:
                loc_repo.upsert(final_dest, campaign_id)
                # Create/update stubs for every connection of the new location.
                create_exit_stubs(
                    loc_repo,
                    final_dest.connections,
                    parent_name=final_dest.name,
                    campaign_id=campaign_id,
                )

            CampaignRepository(db_session).update(session.campaign)

            npcs = NPCRepository(db_session).list_by_location(
                final_dest.name, campaign_id,
            )
            db_session.commit()
            return npcs
        finally:
            db_session.close()

    npcs = await asyncio.to_thread(_persist_and_reload_npcs)
    session.npcs = {n.name: n for n in npcs}

    # Lot G — hydrate npcs_present into real NPC rows for the new location.
    # Done AFTER the location was committed so the row exists.
    from bot.scene_hydration import hydrate_scene

    hydrate_scene(session, db_factory=db_factory)

    logger.info(
        "LOCATION changed campaign=%s from=%s to=%s generated=%s npcs=%d",
        campaign_id, current_name, dest.name, created_stub_or_full, len(session.npcs),
    )
    return dest
