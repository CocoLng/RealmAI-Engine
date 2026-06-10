"""Layer 3 — Compressed summaries via Ollama LLM.

Auto-generates summaries every ~20 interactions using
the native Ollama API via OllamaClient (qwen3.5:9b).
"""

import logging

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ai.client import OllamaClient
from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.summary_repo import SummaryRepository
from memory.models import CompressedSummary, NarrativeExchange
from memory.token_utils import truncate_lines_keep_recent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a concise summarizer for a D&D 5e game session. "
    "You will receive a sequence of narrative exchanges between players and a narrator. "
    "Produce a JSON object with a single \"summary\" field containing a 2-4 sentence "
    "summary of the key events, decisions, combat outcomes, and discoveries. "
    "Focus on facts that matter for story continuity. "
    "Do NOT include mechanical details like exact dice rolls.\n\n"
    "Respond ONLY with valid JSON in this format:\n"
    "{\"summary\": \"your summary text here\"}"
)


class _SummaryResponse(BaseModel):
    """Expected JSON structure from the LLM."""

    summary: str


class Summarizer:
    """Generates compressed summaries using Ollama (Layer 3)."""

    SUMMARY_INTERVAL: int = 20
    MODEL: str = "qwen3.5:9b"

    def __init__(
        self,
        session: Session,
        client: OllamaClient | None,
    ) -> None:
        """``client=None`` disables summary GENERATION; reads still work."""
        self._summary_repo = SummaryRepository(session)
        self._exchange_repo = ExchangeRepository(session)
        self._client = client

    def should_summarize(self, campaign_id: str) -> bool:
        """Check if enough unsummarized exchanges have accumulated."""
        latest = self._summary_repo.get_latest(campaign_id)
        last_summarized = latest.end_interaction if latest else 0
        count = self._exchange_repo.count_unsummarized(campaign_id, last_summarized)
        return count >= self.SUMMARY_INTERVAL

    def summarize(self, campaign_id: str) -> CompressedSummary | None:
        """Generate a summary of unsummarized exchanges via Ollama.

        Returns None if not enough exchanges or if LLM call fails.
        """
        if self._client is None:
            return None
        latest = self._summary_repo.get_latest(campaign_id)
        last_summarized = latest.end_interaction if latest else 0

        exchanges = self._exchange_repo.get_unsummarized(campaign_id, last_summarized)
        if len(exchanges) < self.SUMMARY_INTERVAL:
            return None

        exchanges_text = self._format_exchanges(exchanges)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Summarize these exchanges:\n\n{exchanges_text}"},
        ]

        try:
            data = self._client.chat_json(self.MODEL, messages, temperature=0.3)
        except Exception:
            logger.warning("Ollama call failed for summarization", exc_info=True)
            return None

        try:
            summary_response = _SummaryResponse.model_validate(data)
        except Exception:
            logger.warning("Unexpected summarizer response shape: %s", data)
            return None

        summary = CompressedSummary(
            campaign_id=campaign_id,
            summary_text=summary_response.summary,
            start_interaction=exchanges[0].interaction_number,
            end_interaction=exchanges[-1].interaction_number,
        )
        self._summary_repo.save(summary)
        logger.info(
            "SUMMARY campaign=%s interactions=%d-%d",
            campaign_id, summary.start_interaction, summary.end_interaction,
        )
        return summary

    def get_recent_summaries(
        self, campaign_id: str, limit: int = 4
    ) -> list[CompressedSummary]:
        """Get the N most recent summaries for context injection."""
        return self._summary_repo.get_recent(campaign_id, limit=limit)

    def render(
        self, summaries: list[CompressedSummary], max_tokens: int = 400
    ) -> str:
        """Render summaries into a text block for the prompt."""
        if not summaries:
            return ""
        lines = ["[SESSION HISTORY]"]
        for s in summaries:
            lines.append(
                f"[Interactions {s.start_interaction}-{s.end_interaction}] "
                f"{s.summary_text}"
            )
        text = "\n".join(lines)
        # Oldest summaries are dropped first when over budget.
        return truncate_lines_keep_recent(text, max_tokens)

    def _format_exchanges(self, exchanges: list[NarrativeExchange]) -> str:
        """Format exchanges for the LLM prompt."""
        lines: list[str] = []
        for ex in exchanges:
            role = ex.role.value.capitalize()
            lines.append(f"{role}: {ex.content}")
        return "\n".join(lines)
