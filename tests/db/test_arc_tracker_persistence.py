"""Tests for CampaignChannelRow.arc_tracker_message_id persistence."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Base, CampaignChannelRow
from db.repositories.campaign_channel_repo import CampaignChannelRepository


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _make_row(session: Session) -> CampaignChannelRow:
    """Build a minimal CampaignChannelRow."""
    row = CampaignChannelRow(
        channel_id=999,
        campaign_id="cmp_1",
        guild_id=42,
    )
    session.add(row)
    session.commit()
    return row


def test_arc_tracker_message_id_defaults_to_none(session: Session) -> None:
    _make_row(session)
    fetched = session.get(CampaignChannelRow, 999)
    assert fetched is not None
    assert fetched.arc_tracker_message_id is None


def test_repository_set_and_get_message_id(session: Session) -> None:
    _make_row(session)

    repo = CampaignChannelRepository(session)
    repo.update_arc_tracker_message_id(999, 12345)
    session.commit()
    assert repo.get_arc_tracker_message_id(999) == 12345

    repo.update_arc_tracker_message_id(999, None)
    session.commit()
    assert repo.get_arc_tracker_message_id(999) is None


def test_repository_methods_handle_missing_row(session: Session) -> None:
    repo = CampaignChannelRepository(session)
    # Should not raise, should return None / no-op.
    assert repo.get_arc_tracker_message_id(9999999) is None
    repo.update_arc_tracker_message_id(9999999, 12345)  # no-op
    session.commit()
