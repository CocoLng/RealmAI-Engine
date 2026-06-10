"""Tests for memory/sliding_window.py -- Layer 2 sliding window."""

from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from memory.models import ExchangeRole, NarrativeExchange
from memory.sliding_window import SlidingWindow
from memory.token_utils import estimate_tokens
from world.campaign import Campaign


class TestSlidingWindow:
    def test_add_and_get_window(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        sw = SlidingWindow(db_session, window_size=5)
        for i in range(1, 4):
            sw.add_exchange(sample_campaign.id, ExchangeRole.PLAYER, f"Msg {i}", i)
        db_session.commit()
        window = sw.get_window(sample_campaign.id)
        assert len(window) == 3
        assert window[0].interaction_number == 1

    def test_window_caps_at_size(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        sw = SlidingWindow(db_session, window_size=5)
        for i in range(1, 11):
            sw.add_exchange(sample_campaign.id, ExchangeRole.PLAYER, f"Msg {i}", i)
        db_session.commit()
        window = sw.get_window(sample_campaign.id)
        assert len(window) == 5
        assert window[0].interaction_number == 6
        assert window[-1].interaction_number == 10

    def test_add_returns_exchange(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        sw = SlidingWindow(db_session)
        result = sw.add_exchange(sample_campaign.id, ExchangeRole.NARRATOR, "A tale begins.", 1)
        assert isinstance(result, NarrativeExchange)
        assert result.role == ExchangeRole.NARRATOR
        assert result.content == "A tale begins."

    def test_render_output_format(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        sw = SlidingWindow(db_session)
        sw.add_exchange(sample_campaign.id, ExchangeRole.PLAYER, "I enter the tavern.", 1)
        sw.add_exchange(sample_campaign.id, ExchangeRole.NARRATOR, "The door creaks open.", 2)
        sw.add_exchange(sample_campaign.id, ExchangeRole.SYSTEM, "Perception check: 15.", 3)
        db_session.commit()
        window = sw.get_window(sample_campaign.id)
        text = sw.render(window)
        assert "[RECENT NARRATIVE]" in text
        assert "Player: I enter the tavern." in text
        assert "Narrator: The door creaks open." in text
        assert "System: Perception check: 15." in text

    def test_render_within_budget(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        sw = SlidingWindow(db_session)
        for i in range(1, 13):
            sw.add_exchange(
                sample_campaign.id, ExchangeRole.NARRATOR,
                f"This is a longer narration for exchange number {i} with extra words.", i,
            )
        db_session.commit()
        window = sw.get_window(sample_campaign.id)
        text = sw.render(window, max_tokens=50)
        assert estimate_tokens(text) <= 50

    def test_render_over_budget_keeps_most_recent(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        """When the window exceeds the budget, the OLDEST exchanges are
        dropped — the freshest narrative continuity must survive."""
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        sw = SlidingWindow(db_session)
        for i in range(1, 13):
            sw.add_exchange(
                sample_campaign.id, ExchangeRole.NARRATOR,
                f"This is a longer narration for exchange number {i} with extra words.", i,
            )
        db_session.commit()
        window = sw.get_window(sample_campaign.id)
        text = sw.render(window, max_tokens=80)
        assert estimate_tokens(text) <= 80
        assert "[RECENT NARRATIVE]" in text
        assert "exchange number 12" in text
        assert "exchange number 1 " not in text

    def test_render_empty(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        sw = SlidingWindow(db_session)
        text = sw.render([])
        assert text == ""

    def test_next_interaction_number_empty(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        sw = SlidingWindow(db_session)
        assert sw.next_interaction_number(sample_campaign.id) == 1

    def test_next_interaction_number_continues_from_max(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()
        sw = SlidingWindow(db_session)
        for i in (1, 2, 7):
            sw.add_exchange(sample_campaign.id, ExchangeRole.PLAYER, f"m{i}", i)
        db_session.commit()
        assert sw.next_interaction_number(sample_campaign.id) == 8
