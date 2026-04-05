"""Campaign repository — CRUD for campaigns."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.mappers import campaign_from_db, campaign_to_db
from db.models import CampaignRow
from world.campaign import Campaign


class CampaignRepository:
    """Persistence operations for Campaign entities."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, campaign: Campaign) -> None:
        """Insert a new campaign."""
        row = campaign_to_db(campaign)
        self._session.add(row)

    def get_by_id(self, campaign_id: str) -> Campaign | None:
        """Fetch a campaign by ID, or None if not found."""
        row = self._session.get(CampaignRow, campaign_id)
        if row is None:
            return None
        return campaign_from_db(row)

    def list_all(self) -> list[Campaign]:
        """List all campaigns."""
        rows = self._session.execute(select(CampaignRow)).scalars().all()
        return [campaign_from_db(r) for r in rows]

    def update(self, campaign: Campaign) -> None:
        """Update an existing campaign."""
        row = self._session.get(CampaignRow, campaign.id)
        if row is None:
            msg = f"Campaign '{campaign.id}' not found"
            raise ValueError(msg)
        row.name = campaign.name
        row.created_at = campaign.created_at
        row.player_names = campaign.player_names  # type: ignore[assignment]
        row.current_location = campaign.current_location
        row.interaction_count = campaign.interaction_count

    def delete(self, campaign_id: str) -> None:
        """Delete a campaign by ID."""
        row = self._session.get(CampaignRow, campaign_id)
        if row is not None:
            self._session.delete(row)
