"""Location repository — CRUD for locations."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.mappers import location_from_db, location_to_db
from db.models import LocationRow
from world.location import Location


def _copy_fields_to_row(row: LocationRow, location: Location) -> None:
    """Copy every mutable Location field onto an existing row.

    Single source of truth for the update/upsert write set — the two
    methods used to duplicate this block, and combat_triggers/npc_roles
    silently fell out of both (H6: ambushes and NPC roles were lost on
    the first save/reload).
    """
    row.description = location.description
    row.arrival_hook = location.arrival_hook
    row.connections = location.connections
    row.exit_aliases = location.exit_aliases
    row.npcs_present = location.npcs_present
    row.items_available = location.items_available
    row.item_descriptions = location.item_descriptions
    row.state_flags = location.state_flags
    row.unlocked_exits = location.unlocked_exits
    row.generated = location.generated
    row.combat_zones = [z.model_dump() for z in location.combat_zones]
    row.combat_triggers = {
        key: trigger.model_dump()
        for key, trigger in location.combat_triggers.items()
    }
    row.npc_roles = dict(location.npc_roles)


class LocationRepository:
    """Persistence operations for Location entities."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, location: Location, campaign_id: str) -> None:
        """Insert a new location."""
        row = location_to_db(location, campaign_id)
        self._session.add(row)

    def get_by_name(self, name: str, campaign_id: str) -> Location | None:
        """Fetch a location by name within a campaign, or None if not found."""
        stmt = select(LocationRow).where(
            LocationRow.campaign_id == campaign_id,
            LocationRow.name == name,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return location_from_db(row)

    def list_by_campaign(self, campaign_id: str) -> list[Location]:
        """List all locations in a campaign."""
        stmt = select(LocationRow).where(LocationRow.campaign_id == campaign_id)
        rows = self._session.execute(stmt).scalars().all()
        return [location_from_db(r) for r in rows]

    def update(self, location: Location, campaign_id: str) -> None:
        """Update an existing location (looked up by campaign_id + name)."""
        stmt = select(LocationRow).where(
            LocationRow.campaign_id == campaign_id,
            LocationRow.name == location.name,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            msg = f"Location '{location.name}' not found in campaign '{campaign_id}'"
            raise ValueError(msg)
        _copy_fields_to_row(row, location)

    def upsert(self, location: Location, campaign_id: str) -> None:
        """Insert the location, or update it in place if a row with the same
        (campaign_id, name) already exists.

        Used by the stubbing logic in ``bot/world_navigation.py`` where we need
        to create a placeholder row for every connection without worrying
        about whether another code path has already created it.
        """
        stmt = select(LocationRow).where(
            LocationRow.campaign_id == campaign_id,
            LocationRow.name == location.name,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            self._session.add(location_to_db(location, campaign_id))
            return
        _copy_fields_to_row(row, location)

    def delete(self, name: str, campaign_id: str) -> None:
        """Delete a location by name within a campaign."""
        stmt = select(LocationRow).where(
            LocationRow.campaign_id == campaign_id,
            LocationRow.name == name,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is not None:
            self._session.delete(row)
