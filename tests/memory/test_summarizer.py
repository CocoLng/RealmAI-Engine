"""Tests for memory/summarizer.py — Layer 3 compressed summaries.

Ollama is mocked via a MagicMock OllamaClient.
"""

from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.summary_repo import SummaryRepository
from memory.models import CompressedSummary, ExchangeRole, NarrativeExchange
from memory.summarizer import Summarizer
from memory.token_utils import estimate_tokens
from world.campaign import Campaign


def _make_mock_client(summary_text: str | None = None) -> MagicMock:
    """Create a mock OllamaClient that returns a summary response."""
    client = MagicMock()
    if summary_text is not None:
        client.chat_json.return_value = {"summary": summary_text}
    return client


def _seed_exchanges(db_session: Session, campaign_id: str, count: int) -> None:
    """Insert N exchanges into the DB."""
    repo = ExchangeRepository(db_session)
    for i in range(1, count + 1):
        repo.save(NarrativeExchange(
            campaign_id=campaign_id,
            role=ExchangeRole.PLAYER if i % 2 else ExchangeRole.NARRATOR,
            content=f"Exchange content number {i}",
            interaction_number=i,
        ))
    db_session.commit()


class TestSummarizer:
    def test_should_summarize_false_when_few_exchanges(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 10)
        summarizer = Summarizer(db_session, _make_mock_client())
        assert summarizer.should_summarize(sample_campaign.id) is False

    def test_should_summarize_true_when_enough(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        """Triggers once INTERVAL exchanges have left the sliding window
        (window_size + INTERVAL unsummarized exchanges in total)."""
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 32)
        summarizer = Summarizer(db_session, _make_mock_client())
        assert summarizer.should_summarize(sample_campaign.id) is True

    def test_should_summarize_false_while_only_window_unsummarized(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        """Exchanges still rendered in the window must NOT count toward
        the cadence — summarizing them would duplicate layers 2 and 3."""
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 31)
        summarizer = Summarizer(db_session, _make_mock_client())
        assert summarizer.should_summarize(sample_campaign.id) is False

    def test_should_summarize_accounts_for_existing_summaries(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 30)
        SummaryRepository(db_session).save(CompressedSummary(
            campaign_id=sample_campaign.id, summary_text="Previous",
            start_interaction=1, end_interaction=20,
        ))
        db_session.commit()
        summarizer = Summarizer(db_session, _make_mock_client())
        assert summarizer.should_summarize(sample_campaign.id) is False

    def test_summarize_calls_ollama(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 40)

        mock_client = _make_mock_client("The party explored the dungeon and defeated goblins.")
        summarizer = Summarizer(db_session, mock_client)
        result = summarizer.summarize(sample_campaign.id)

        assert result is not None
        assert "goblins" in result.summary_text
        assert result.start_interaction == 1
        # Only exchanges OUT of the 12-exchange window are summarized
        assert result.end_interaction == 28

        mock_client.chat_json.assert_called_once()
        call_kwargs = mock_client.chat_json.call_args.kwargs
        assert call_kwargs.get("temperature") == 0.3 or mock_client.chat_json.call_args[0][0] == "qwen3.5:9b"

    def test_summarize_excludes_window_exchanges_from_prompt(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        """The LLM prompt must not contain the exchanges still rendered
        in the sliding window (audit low: layer 2/3 duplication)."""
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 40)

        mock_client = _make_mock_client("Summary.")
        summarizer = Summarizer(db_session, mock_client)
        summarizer.summarize(sample_campaign.id)

        user_message = mock_client.chat_json.call_args[0][1][1]["content"]
        assert "number 28" in user_message
        assert "number 29" not in user_message
        assert "number 40" not in user_message

    def test_summarize_purges_summarized_exchanges(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        """After a successful summary, the summarized exchanges are
        deleted — the exchanges table stays bounded (audit low)."""
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 40)

        summarizer = Summarizer(db_session, _make_mock_client("Summary."))
        result = summarizer.summarize(sample_campaign.id)
        db_session.commit()

        assert result is not None
        remaining = ExchangeRepository(db_session).get_recent(
            sample_campaign.id, limit=100,
        )
        assert len(remaining) == 12
        assert remaining[0].interaction_number == 29

    def test_summarize_failure_does_not_purge(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 40)

        mock_client = MagicMock()
        mock_client.chat_json.side_effect = ConnectionError("Ollama down")
        summarizer = Summarizer(db_session, mock_client)
        result = summarizer.summarize(sample_campaign.id)

        assert result is None
        remaining = ExchangeRepository(db_session).get_recent(
            sample_campaign.id, limit=100,
        )
        assert len(remaining) == 40

    def test_summarize_returns_none_when_not_enough(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 5)
        summarizer = Summarizer(db_session, _make_mock_client())
        result = summarizer.summarize(sample_campaign.id)
        assert result is None

    def test_summarize_graceful_on_invalid_json(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 40)

        mock_client = MagicMock()
        mock_client.chat_json.return_value = {"wrong_key": "no summary field"}

        summarizer = Summarizer(db_session, mock_client)
        result = summarizer.summarize(sample_campaign.id)
        assert result is None

    def test_summarize_graceful_on_connection_error(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 40)

        mock_client = MagicMock()
        mock_client.chat_json.side_effect = ConnectionError("Ollama down")

        summarizer = Summarizer(db_session, mock_client)
        result = summarizer.summarize(sample_campaign.id)
        assert result is None

    def test_get_recent_summaries(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        summary_repo = SummaryRepository(db_session)
        for i in range(5):
            summary_repo.save(CompressedSummary(
                campaign_id=sample_campaign.id, summary_text=f"Summary {i + 1}",
                start_interaction=i * 20 + 1, end_interaction=(i + 1) * 20,
            ))
        db_session.commit()
        summarizer = Summarizer(db_session, _make_mock_client())
        results = summarizer.get_recent_summaries(sample_campaign.id, limit=3)
        assert len(results) == 3
        assert results[0].start_interaction == 41

    def test_render(self, db_session: Session) -> None:
        summaries = [
            CompressedSummary(
                campaign_id="c1", summary_text="The party arrived at Neverwinter.",
                start_interaction=1, end_interaction=20,
            ),
            CompressedSummary(
                campaign_id="c1", summary_text="They defeated the goblins.",
                start_interaction=21, end_interaction=40,
            ),
        ]
        summarizer = Summarizer(db_session, _make_mock_client())
        text = summarizer.render(summaries)
        assert "[SESSION HISTORY]" in text
        assert "[Interactions 1-20]" in text
        assert "arrived at Neverwinter" in text
        assert "[Interactions 21-40]" in text

    def test_render_within_budget(self, db_session: Session) -> None:
        summaries = [
            CompressedSummary(
                campaign_id="c1",
                summary_text="A very long summary text that goes on " * 20,
                start_interaction=1, end_interaction=20,
            ),
        ]
        summarizer = Summarizer(db_session, _make_mock_client())
        text = summarizer.render(summaries, max_tokens=30)
        assert estimate_tokens(text) <= 30

    def test_render_over_budget_keeps_most_recent(self, db_session: Session) -> None:
        """Over budget, the OLDEST summaries are dropped, not the newest."""
        summaries = [
            CompressedSummary(
                campaign_id="c1",
                summary_text=f"Era {i}: " + "many things happened during this period " * 5,
                start_interaction=i * 20 + 1, end_interaction=(i + 1) * 20,
            )
            for i in range(4)
        ]
        summarizer = Summarizer(db_session, _make_mock_client())
        text = summarizer.render(summaries, max_tokens=80)
        assert estimate_tokens(text) <= 80
        assert "[SESSION HISTORY]" in text
        assert "Era 3:" in text
        assert "Era 0:" not in text

    def test_render_empty(self, db_session: Session) -> None:
        summarizer = Summarizer(db_session, _make_mock_client())
        assert summarizer.render([]) == ""
