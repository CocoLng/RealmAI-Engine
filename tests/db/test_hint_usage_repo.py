"""Tests for HintUsageRepository."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.database import Base
from db.models import CampaignRow
from db.repositories.hint_usage_repo import HintUsageRepository


@pytest.fixture
def db_session() -> Session:  # type: ignore[return]
    """In-memory SQLite session with a seeded campaign row."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    session.add(CampaignRow(
        id="c1",
        name="Test",
        created_at=datetime.now(UTC),
        player_names=[],
        current_location=None,
    ))
    session.commit()
    yield session  # type: ignore[misc]
    session.close()


def test_get_or_create_returns_zero_defaults(db_session: Session) -> None:
    repo = HintUsageRepository(db_session)
    row = repo.get_or_create(campaign_id="c1", beat_number=1)
    assert row.level1_uses == 0
    assert row.level2_used is False
    assert row.level3_last_used_turn is None


def test_increment_level1(db_session: Session) -> None:
    repo = HintUsageRepository(db_session)
    repo.increment_level1(campaign_id="c1", beat_number=1)
    repo.increment_level1(campaign_id="c1", beat_number=1)
    row = repo.get_or_create(campaign_id="c1", beat_number=1)
    assert row.level1_uses == 2


def test_set_level2_used(db_session: Session) -> None:
    repo = HintUsageRepository(db_session)
    repo.set_level2_used(campaign_id="c1", beat_number=1)
    row = repo.get_or_create(campaign_id="c1", beat_number=1)
    assert row.level2_used is True


def test_set_level3_last_used_turn(db_session: Session) -> None:
    repo = HintUsageRepository(db_session)
    repo.set_level3_last_used_turn(campaign_id="c1", beat_number=1, turn=42)
    row = repo.get_or_create(campaign_id="c1", beat_number=1)
    assert row.level3_last_used_turn == 42


def test_clear_for_beat(db_session: Session) -> None:
    repo = HintUsageRepository(db_session)
    repo.set_level2_used(campaign_id="c1", beat_number=1)
    repo.clear_for_beat(campaign_id="c1", beat_number=1)
    # Row deleted — get_or_create should recreate with fresh defaults.
    row = repo.get_or_create(campaign_id="c1", beat_number=1)
    assert row.level2_used is False
