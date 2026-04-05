"""Exchange repository — CRUD for narrative exchanges."""

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from db.mappers import exchange_from_db, exchange_to_db
from db.models import ExchangeRow
from memory.models import NarrativeExchange


class ExchangeRepository:
    """Persistence operations for NarrativeExchange entities."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, exchange: NarrativeExchange) -> None:
        """Insert a new exchange."""
        row = exchange_to_db(exchange)
        self._session.add(row)

    def get_recent(self, campaign_id: str, limit: int = 12) -> list[NarrativeExchange]:
        """Get the last N exchanges, returned in ASC order (oldest first)."""
        subq = (
            select(ExchangeRow)
            .where(ExchangeRow.campaign_id == campaign_id)
            .order_by(ExchangeRow.interaction_number.desc())
            .limit(limit)
            .subquery()
        )
        stmt = select(ExchangeRow).join(
            subq, ExchangeRow.id == subq.c.id
        ).order_by(ExchangeRow.interaction_number.asc())
        rows = self._session.execute(stmt).scalars().all()
        return [exchange_from_db(r) for r in rows]

    def get_range(self, campaign_id: str, start: int, end: int) -> list[NarrativeExchange]:
        """Get exchanges with interaction_number between start and end inclusive."""
        stmt = (
            select(ExchangeRow)
            .where(
                ExchangeRow.campaign_id == campaign_id,
                ExchangeRow.interaction_number >= start,
                ExchangeRow.interaction_number <= end,
            )
            .order_by(ExchangeRow.interaction_number.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [exchange_from_db(r) for r in rows]

    def get_unsummarized(self, campaign_id: str, last_summarized: int) -> list[NarrativeExchange]:
        """Get exchanges after the last summarized interaction, in ASC order."""
        stmt = (
            select(ExchangeRow)
            .where(
                ExchangeRow.campaign_id == campaign_id,
                ExchangeRow.interaction_number > last_summarized,
            )
            .order_by(ExchangeRow.interaction_number.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [exchange_from_db(r) for r in rows]

    def count_unsummarized(self, campaign_id: str, last_summarized: int) -> int:
        """Count exchanges after the last summarized interaction."""
        stmt = (
            select(func.count())
            .select_from(ExchangeRow)
            .where(
                ExchangeRow.campaign_id == campaign_id,
                ExchangeRow.interaction_number > last_summarized,
            )
        )
        result = self._session.execute(stmt).scalar()
        return result or 0

    def delete_before(self, campaign_id: str, interaction_number: int) -> None:
        """Delete exchanges older than the given interaction_number."""
        stmt = delete(ExchangeRow).where(
            ExchangeRow.campaign_id == campaign_id,
            ExchangeRow.interaction_number < interaction_number,
        )
        self._session.execute(stmt)
