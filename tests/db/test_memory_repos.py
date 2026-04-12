"""Tests for Exchange and Summary repositories — CRUD with in-memory SQLite."""

from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.summary_repo import SummaryRepository
from memory.models import CompressedSummary, ExchangeRole, NarrativeExchange
from world.campaign import Campaign


class TestExchangeRepository:
    def test_save_and_get_recent(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)
        for i in range(1, 4):
            repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id, role=ExchangeRole.PLAYER,
                content=f"Message {i}", interaction_number=i,
            ))
        db_session.commit()
        results = repo.get_recent(sample_campaign.id, limit=2)
        assert len(results) == 2
        assert results[0].interaction_number == 2
        assert results[1].interaction_number == 3

    def test_get_recent_returns_asc_order(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)
        for i in range(1, 6):
            repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id, role=ExchangeRole.NARRATOR,
                content=f"Narration {i}", interaction_number=i,
            ))
        db_session.commit()
        results = repo.get_recent(sample_campaign.id, limit=3)
        assert [r.interaction_number for r in results] == [3, 4, 5]

    def test_get_range(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)
        for i in range(1, 11):
            repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id, role=ExchangeRole.PLAYER,
                content=f"Msg {i}", interaction_number=i,
            ))
        db_session.commit()
        results = repo.get_range(sample_campaign.id, start=3, end=7)
        assert len(results) == 5
        assert results[0].interaction_number == 3
        assert results[-1].interaction_number == 7

    def test_get_unsummarized(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)
        for i in range(1, 26):
            repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id, role=ExchangeRole.PLAYER,
                content=f"Msg {i}", interaction_number=i,
            ))
        db_session.commit()
        results = repo.get_unsummarized(sample_campaign.id, last_summarized=10)
        assert len(results) == 15
        assert results[0].interaction_number == 11

    def test_count_unsummarized(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)
        for i in range(1, 26):
            repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id, role=ExchangeRole.PLAYER,
                content=f"Msg {i}", interaction_number=i,
            ))
        db_session.commit()
        count = repo.count_unsummarized(sample_campaign.id, last_summarized=10)
        assert count == 15

    def test_delete_before(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)
        for i in range(1, 6):
            repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id, role=ExchangeRole.PLAYER,
                content=f"Msg {i}", interaction_number=i,
            ))
        db_session.commit()
        repo.delete_before(sample_campaign.id, interaction_number=3)
        db_session.commit()
        results = repo.get_recent(sample_campaign.id, limit=10)
        assert len(results) == 3
        assert results[0].interaction_number == 3

    def test_campaign_scoping(self, db_session: Session) -> None:
        CampaignRepository(db_session).save(Campaign(id="c1", name="First"))
        CampaignRepository(db_session).save(Campaign(id="c2", name="Second"))
        repo = ExchangeRepository(db_session)
        repo.save(NarrativeExchange(campaign_id="c1", role=ExchangeRole.PLAYER, content="A", interaction_number=1))
        repo.save(NarrativeExchange(campaign_id="c2", role=ExchangeRole.PLAYER, content="B", interaction_number=1))
        db_session.commit()
        assert len(repo.get_recent("c1", limit=10)) == 1
        assert len(repo.get_recent("c2", limit=10)) == 1

    def test_cascade_delete(self, db_session: Session, sample_campaign: Campaign) -> None:
        camp_repo = CampaignRepository(db_session)
        camp_repo.save(sample_campaign)
        repo = ExchangeRepository(db_session)
        repo.save(NarrativeExchange(
            campaign_id=sample_campaign.id, role=ExchangeRole.PLAYER,
            content="Test", interaction_number=1,
        ))
        db_session.commit()
        camp_repo.delete(sample_campaign.id)
        db_session.commit()
        assert repo.get_recent(sample_campaign.id, limit=10) == []


class TestSummaryRepository:
    def test_save_and_get_recent(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = SummaryRepository(db_session)
        for i in range(3):
            repo.save(CompressedSummary(
                campaign_id=sample_campaign.id, summary_text=f"Summary {i + 1}",
                start_interaction=i * 20 + 1, end_interaction=(i + 1) * 20,
            ))
        db_session.commit()
        results = repo.get_recent(sample_campaign.id, limit=2)
        assert len(results) == 2
        assert results[0].start_interaction == 21
        assert results[1].start_interaction == 41

    def test_get_latest(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = SummaryRepository(db_session)
        repo.save(CompressedSummary(
            campaign_id=sample_campaign.id, summary_text="First",
            start_interaction=1, end_interaction=20,
        ))
        repo.save(CompressedSummary(
            campaign_id=sample_campaign.id, summary_text="Second",
            start_interaction=21, end_interaction=40,
        ))
        db_session.commit()
        latest = repo.get_latest(sample_campaign.id)
        assert latest is not None
        assert latest.summary_text == "Second"
        assert latest.end_interaction == 40

    def test_get_latest_empty(self, db_session: Session) -> None:
        repo = SummaryRepository(db_session)
        assert repo.get_latest("nonexistent") is None

    def test_list_by_campaign(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = SummaryRepository(db_session)
        repo.save(CompressedSummary(
            campaign_id=sample_campaign.id, summary_text="S1",
            start_interaction=1, end_interaction=20,
        ))
        repo.save(CompressedSummary(
            campaign_id=sample_campaign.id, summary_text="S2",
            start_interaction=21, end_interaction=40,
        ))
        db_session.commit()
        results = repo.list_by_campaign(sample_campaign.id)
        assert len(results) == 2

    def test_cascade_delete(self, db_session: Session, sample_campaign: Campaign) -> None:
        camp_repo = CampaignRepository(db_session)
        camp_repo.save(sample_campaign)
        repo = SummaryRepository(db_session)
        repo.save(CompressedSummary(
            campaign_id=sample_campaign.id, summary_text="Test",
            start_interaction=1, end_interaction=20,
        ))
        db_session.commit()
        camp_repo.delete(sample_campaign.id)
        db_session.commit()
        assert repo.list_by_campaign(sample_campaign.id) == []
