"""Scene hydration — turn `Location.npcs_present` strings into real NPC rows.

The world generator emits `Location.npcs_present` as a list of plain strings
(``["La Marchande de Brumes", "Le Gardien du Moulin"]``). Without this helper
those names never become persisted ``NPC`` entities, so the entity resolver
sees an empty ``session.npcs`` and refuses every TALK action.

This module is the bridge: at campaign launch and after every MOVE, we walk
``location.npcs_present`` and ensure each name corresponds to a row in the
``npcs`` table for the campaign. Already-persisted NPCs are left untouched
(idempotent).

Hydrated NPCs use the lightest possible commoner stats — they exist primarily
so the player can talk to them. They are deliberately fragile (``max_hp=4``)
so :func:`bot.action_pipeline.ActionPipeline._should_trivial_resolve` treats
them as one-shot kills if attacked.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from engine.character import AbilityScores, Race
from engine.inventory import Item, ItemType
from world.npc import NPC, NPCDisposition

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


def _build_default_npc(name: str, location_name: str) -> NPC:
    """Create a minimal commoner NPC for a name with no prior backstory."""
    return NPC(
        name=name,
        race=Race.HUMAN,
        char_class=None,
        level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=4,
        max_hp=4,
        ac=10,
        disposition=NPCDisposition.NEUTRAL,
        is_alive=True,
        description="",
        personality="",
        location_name=location_name,
        aliases=[],
    )


def hydrate_scene(
    session: "GameSession",
    *,
    db_factory: Callable[[], Any],
) -> None:
    """Ensure every name in ``location.npcs_present`` and ``items_available``
    is reachable by the entity resolver.

    - NPCs: missing names are inserted as commoner ``NPC`` rows tied to the
      current location, then ``session.npcs`` is reloaded.
    - Items: ``Location.items_available`` is the source of truth — no DB
      mutation is required, but we make sure the location row reflects the
      in-memory list (in case the launcher mutated it post-save).

    Idempotent and best-effort: a single failure logs a warning and returns
    without aborting the launch.
    """
    location = session.current_location
    if location is None:
        return
    campaign_id = session.campaign.id

    db_session = db_factory()
    try:
        npc_repo = NPCRepository(db_session)
        loc_repo = LocationRepository(db_session)

        # 1. Hydrate any missing NPC.
        created = 0
        for name in location.npcs_present:
            if not name or not name.strip():
                continue
            existing = npc_repo.get_by_name(name, campaign_id)
            if existing is None:
                npc_repo.save(_build_default_npc(name, location.name), campaign_id)
                created += 1
            elif existing.location_name != location.name:
                # NPC exists in DB but isn't tagged to this location yet —
                # rebind so the resolver picks them up.
                existing.location_name = location.name
                npc_repo.update(existing, campaign_id)

        # 2. Persist the items_available list (in case it was mutated in
        #    memory by a prior PICKUP and not yet flushed). The location row
        #    must exist; if it doesn't, swallow gracefully.
        try:
            loc_repo.update(location, campaign_id)
        except ValueError:
            pass  # Location not yet persisted — caller will save it.

        db_session.commit()

        # 3. Reload session.npcs from the canonical DB list.
        npcs = npc_repo.list_by_location(location.name, campaign_id)
        session.npcs = {n.name: n for n in npcs}

        if created:
            logger.info(
                "SCENE hydrated campaign=%s location=%s npcs_created=%d total=%d",
                campaign_id, location.name, created, len(session.npcs),
            )
    except Exception:
        logger.warning(
            "SCENE hydration failed campaign=%s location=%s",
            campaign_id, location.name, exc_info=True,
        )
    finally:
        db_session.close()


def take_scene_item(
    session: "GameSession",
    *,
    item_name: str,
    user_id: int,
    db_factory: Callable[[], Any],
) -> Item | None:
    """Move an item from the current location into the player's inventory.

    Returns the created :class:`engine.inventory.Item` on success, ``None``
    if no matching scene item exists. The location row and the player
    inventory row are persisted in a single transaction.
    """
    location = session.current_location
    if location is None:
        return None
    if item_name not in location.items_available:
        return None

    item = Item(
        name=item_name,
        item_type=ItemType.ADVENTURING_GEAR,
        weight=0.5,
        value_gp=0,
        description=f"Ramassé dans {location.name}.",
    )

    # Mutate in-memory state first.
    location.items_available = [
        n for n in location.items_available if n != item_name
    ]
    inventory = session.inventories.get(user_id)
    if inventory is not None:
        inventory.items.append(item)

    # Persist.
    db_session = db_factory()
    try:
        from db.mappers import player_character_to_db
        from db.models import PlayerCharacterRow
        from sqlalchemy import select

        loc_repo = LocationRepository(db_session)
        try:
            loc_repo.update(location, session.campaign.id)
        except ValueError:
            loc_repo.save(location, session.campaign.id)

        if inventory is not None and user_id in session.characters:
            stmt = select(PlayerCharacterRow).where(
                PlayerCharacterRow.discord_user_id == user_id,
                PlayerCharacterRow.campaign_id == session.campaign.id,
            )
            row = db_session.execute(stmt).scalar_one_or_none()
            if row is not None:
                row.inventory_json = inventory.model_dump_json()
            else:
                spell = session.spellcasters.get(user_id)
                db_session.add(
                    player_character_to_db(
                        user_id,
                        session.campaign.id,
                        session.characters[user_id],
                        inventory,
                        spell,
                    ),
                )

        db_session.commit()
    except Exception:
        logger.warning(
            "PICKUP persist failed campaign=%s item=%r user=%s",
            session.campaign.id, item_name, user_id, exc_info=True,
        )
        db_session.rollback()
    finally:
        db_session.close()

    logger.info(
        "PICKUP campaign=%s user=%s item=%r location=%s",
        session.campaign.id, user_id, item_name, location.name,
    )
    return item
