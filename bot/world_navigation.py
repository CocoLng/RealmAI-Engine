"""Shared location-change helper.

Used by both the legacy ``/move`` command and the free-text MOVE branch of
:class:`bot.action_pipeline.ActionPipeline`. Loads the destination from the
DB if it exists, generates it via :class:`ai.world_generator.WorldGenerator`
otherwise, then mutates the session and persists campaign + location + npcs.
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


async def change_location(
    session: "GameSession",
    destination_name: str,
    *,
    db_factory: Callable[[], Any],
) -> Location:
    """Move ``session`` to ``destination_name``.

    Loads the destination from the DB; if absent, generates it via
    :class:`WorldGenerator` (requires ``session.ollama_client``). Persists
    the new location, updates ``Campaign.current_location``, and reloads
    ``session.npcs`` from the destination.

    Raises :class:`LocationChangeError` if the destination cannot be obtained.
    """
    campaign_id = session.campaign.id
    current = session.current_location
    current_name = current.name if current is not None else "unknown"

    # 1. Try existing DB location.
    dest: Location | None = None
    db_session = db_factory()
    try:
        dest = LocationRepository(db_session).get_by_name(
            destination_name, campaign_id,
        )
    finally:
        db_session.close()

    generated = False

    # 2. Generate via LLM if absent and Ollama is available.
    if dest is None:
        if session.ollama_client is None:
            raise LocationChangeError(
                destination_name, "no DB entry and Ollama unavailable",
            )
        try:
            from ai.world_generator import WorldGenerator

            gen = WorldGenerator(session.ollama_client)
            # Pass arc location hints so generated names match the arc.
            arc_hints: list[str] | None = None
            if (
                hasattr(session, "story_arc")
                and session.story_arc is not None
            ):
                arc_hints = [
                    beat.location_hint
                    for beat in session.story_arc.beats
                    if beat.location_hint
                ]
            dest = await asyncio.to_thread(
                gen.generate,
                campaign_context=f"Moving from {current_name} to {destination_name}",
                location_type="connected_area",
                location_name=destination_name,
                language=session.language,
                location_hints=arc_hints,
            )
            generated = True
        except Exception as exc:  # noqa: BLE001
            raise LocationChangeError(
                destination_name, f"generation failed: {exc}",
            ) from exc

    assert dest is not None

    # 3. Persist generated location + campaign update + reload npcs.
    db_session = db_factory()
    try:
        loc_repo = LocationRepository(db_session)
        if generated:
            loc_repo.save(dest, campaign_id)

        # Mutate in-memory state.
        session.current_location = dest
        session.campaign.current_location = dest.name

        CampaignRepository(db_session).update(session.campaign)

        npcs = NPCRepository(db_session).list_by_location(dest.name, campaign_id)
        session.npcs = {n.name: n for n in npcs}

        db_session.commit()
    finally:
        db_session.close()

    # Lot G — hydrate npcs_present into real NPC rows for the new location.
    # Done AFTER the location was committed so the row exists.
    from bot.scene_hydration import hydrate_scene

    hydrate_scene(session, db_factory=db_factory)

    logger.info(
        "LOCATION changed campaign=%s from=%s to=%s generated=%s npcs=%d",
        campaign_id, current_name, dest.name, generated, len(session.npcs),
    )
    return dest
