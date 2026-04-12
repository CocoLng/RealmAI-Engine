"""Tests for CampaignChannelRepository — CRUD for channel mappings."""

import pytest
from sqlalchemy.orm import Session

from db.repositories.campaign_channel_repo import CampaignChannelRepository
from db.repositories.campaign_repo import CampaignRepository
from world.campaign import Campaign


@pytest.fixture()
def campaign(db_session: Session) -> Campaign:
    """Create and persist a campaign for FK references."""
    c = Campaign(id="camp-1", name="Test Campaign", player_names=["Alice"])
    repo = CampaignRepository(db_session)
    repo.save(c)
    db_session.flush()
    return c


CHANNEL_ID = 111222333444
GUILD_ID = 555666777888


class TestCampaignChannelRepositorySave:
    """Test save and get operations."""

    def test_save_and_get_by_channel(
        self, db_session: Session, campaign: Campaign,
    ) -> None:
        repo = CampaignChannelRepository(db_session)
        repo.save(CHANNEL_ID, campaign.id, GUILD_ID)
        db_session.flush()

        result = repo.get_by_channel(CHANNEL_ID)
        assert result is not None
        campaign_id, guild_id = result
        assert campaign_id == campaign.id
        assert guild_id == GUILD_ID

    def test_save_and_get_by_campaign(
        self, db_session: Session, campaign: Campaign,
    ) -> None:
        repo = CampaignChannelRepository(db_session)
        repo.save(CHANNEL_ID, campaign.id, GUILD_ID)
        db_session.flush()

        result = repo.get_by_campaign(campaign.id)
        assert result == CHANNEL_ID


class TestCampaignChannelRepositoryGet:
    """Test get operations for missing data."""

    def test_get_by_channel_nonexistent(self, db_session: Session) -> None:
        repo = CampaignChannelRepository(db_session)
        assert repo.get_by_channel(999) is None

    def test_get_by_campaign_nonexistent(self, db_session: Session) -> None:
        repo = CampaignChannelRepository(db_session)
        assert repo.get_by_campaign("nonexistent") is None


class TestCampaignChannelRepositoryDelete:
    """Test delete operations."""

    def test_delete_existing(
        self, db_session: Session, campaign: Campaign,
    ) -> None:
        repo = CampaignChannelRepository(db_session)
        repo.save(CHANNEL_ID, campaign.id, GUILD_ID)
        db_session.flush()

        repo.delete(CHANNEL_ID)
        db_session.flush()

        assert repo.get_by_channel(CHANNEL_ID) is None

    def test_delete_nonexistent_noop(self, db_session: Session) -> None:
        repo = CampaignChannelRepository(db_session)
        repo.delete(999)  # Should not raise
