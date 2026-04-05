"""Location repository — CRUD for locations."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.mappers import location_from_db, location_to_db
from db.models import LocationRow
from world.location import Location


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
        row.description = location.description
        row.connections = location.connections  # type: ignore[assignment]
        row.npcs_present = location.npcs_present  # type: ignore[assignment]
        row.items_available = location.items_available  # type: ignore[assignment]

    def delete(self, name: str, campaign_id: str) -> None:
        """Delete a location by name within a campaign."""
        stmt = select(LocationRow).where(
            LocationRow.campaign_id == campaign_id,
            LocationRow.name == name,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is not None:
            self._session.delete(row)
