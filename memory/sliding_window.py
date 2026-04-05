"""Layer 2 -- Sliding window of recent narrative exchanges.

Maintains the last N exchanges for short-term narrative continuity.
Persists exchanges to SQLite via ExchangeRepository.
"""

from sqlalchemy.orm import Session

from db.repositories.exchange_repo import ExchangeRepository
from memory.models import ExchangeRole, NarrativeExchange
from memory.token_utils import truncate_to_tokens

_ROLE_DISPLAY = {
    ExchangeRole.PLAYER: "Player",
    ExchangeRole.NARRATOR: "Narrator",
    ExchangeRole.SYSTEM: "System",
}


class SlidingWindow:
    """Manages the last N narrative exchanges (Layer 2)."""

    def __init__(self, session: Session, window_size: int = 12) -> None:
        self._repo = ExchangeRepository(session)
        self._window_size = window_size

    def add_exchange(
        self,
        campaign_id: str,
        role: ExchangeRole,
        content: str,
        interaction_number: int,
    ) -> NarrativeExchange:
        """Record a new exchange. Returns the created exchange."""
        exchange = NarrativeExchange(
            campaign_id=campaign_id,
            role=role,
            content=content,
            interaction_number=interaction_number,
        )
        self._repo.save(exchange)
        return exchange

    def get_window(self, campaign_id: str) -> list[NarrativeExchange]:
        """Get the current sliding window (last N exchanges in ASC order)."""
        return self._repo.get_recent(campaign_id, limit=self._window_size)

    def render(
        self, exchanges: list[NarrativeExchange], max_tokens: int = 700
    ) -> str:
        """Render exchanges into a text block for the prompt."""
        if not exchanges:
            return ""
        lines = ["[RECENT NARRATIVE]"]
        for ex in exchanges:
            role_name = _ROLE_DISPLAY.get(ex.role, ex.role.value.capitalize())
            lines.append(f"{role_name}: {ex.content}")
        text = "\n".join(lines)
        return truncate_to_tokens(text, max_tokens)
