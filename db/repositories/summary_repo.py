"""Summary repository — CRUD for compressed summaries."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.mappers import summary_from_db, summary_to_db
from db.models import SummaryRow
from memory.models import CompressedSummary


class SummaryRepository:
    """Persistence operations for CompressedSummary entities."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, summary: CompressedSummary) -> None:
        """Insert a new summary."""
        row = summary_to_db(summary)
        self._session.add(row)

    def get_recent(self, campaign_id: str, limit: int = 4) -> list[CompressedSummary]:
        """Get the last N summaries, returned in ASC order (oldest first)."""
        subq = (
            select(SummaryRow)
            .where(SummaryRow.campaign_id == campaign_id)
            .order_by(SummaryRow.end_interaction.desc())
            .limit(limit)
            .subquery()
        )
        stmt = select(SummaryRow).join(
            subq, SummaryRow.id == subq.c.id
        ).order_by(SummaryRow.end_interaction.asc())
        rows = self._session.execute(stmt).scalars().all()
        return [summary_from_db(r) for r in rows]

    def get_latest(self, campaign_id: str) -> CompressedSummary | None:
        """Get the most recent summary, or None if none exist."""
        stmt = (
            select(SummaryRow)
            .where(SummaryRow.campaign_id == campaign_id)
            .order_by(SummaryRow.end_interaction.desc())
            .limit(1)
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return summary_from_db(row)

    def list_by_campaign(self, campaign_id: str) -> list[CompressedSummary]:
        """List all summaries in a campaign."""
        stmt = (
            select(SummaryRow)
            .where(SummaryRow.campaign_id == campaign_id)
            .order_by(SummaryRow.end_interaction.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [summary_from_db(r) for r in rows]
