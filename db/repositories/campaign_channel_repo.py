"""Persistence operations for campaign-channel mappings."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.mappers import campaign_channel_from_db, campaign_channel_to_db
from db.models import CampaignChannelRow


class CampaignChannelRepository:
    """CRUD operations for Discord channel ↔ campaign mappings."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, channel_id: int, campaign_id: str, guild_id: int) -> None:
        """Insert a new channel-campaign mapping."""
        row = campaign_channel_to_db(channel_id, campaign_id, guild_id)
        self._session.add(row)

    def get_by_channel(self, channel_id: int) -> tuple[str, int] | None:
        """Fetch (campaign_id, guild_id) from a channel ID, or None."""
        row = self._session.get(CampaignChannelRow, channel_id)
        if row is None:
            return None
        _, campaign_id, guild_id = campaign_channel_from_db(row)
        return campaign_id, guild_id

    def get_by_campaign(self, campaign_id: str) -> int | None:
        """Fetch channel_id from a campaign ID, or None."""
        stmt = select(CampaignChannelRow).where(
            CampaignChannelRow.campaign_id == campaign_id,
        )
        row = self._session.execute(stmt).scalars().first()
        if row is None:
            return None
        return row.channel_id

    def delete(self, channel_id: int) -> None:
        """Delete a channel mapping. No-op if not found."""
        row = self._session.get(CampaignChannelRow, channel_id)
        if row is not None:
            self._session.delete(row)

    def get_arc_tracker_message_id(self, channel_id: int) -> int | None:
        """Return the pinned Arc Tracker message ID, or None if unset/missing."""
        row = self._session.get(CampaignChannelRow, channel_id)
        return row.arc_tracker_message_id if row is not None else None

    def update_arc_tracker_message_id(
        self, channel_id: int, message_id: int | None,
    ) -> None:
        """Set the pinned Arc Tracker message ID. No-op if the row is missing."""
        row = self._session.get(CampaignChannelRow, channel_id)
        if row is None:
            return
        row.arc_tracker_message_id = message_id
