"""Quest repository — CRUD for quests."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.mappers import quest_from_db, quest_to_db
from db.models import QuestRow
from world.quest import Quest


class QuestRepository:
    """Persistence operations for Quest entities."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, quest: Quest, campaign_id: str) -> None:
        """Insert a new quest."""
        row = quest_to_db(quest, campaign_id)
        self._session.add(row)

    def get_by_title(self, title: str, campaign_id: str) -> Quest | None:
        """Fetch a quest by title within a campaign, or None if not found."""
        stmt = select(QuestRow).where(
            QuestRow.campaign_id == campaign_id,
            QuestRow.title == title,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return quest_from_db(row)

    def list_by_campaign(self, campaign_id: str) -> list[Quest]:
        """List all quests in a campaign."""
        stmt = select(QuestRow).where(QuestRow.campaign_id == campaign_id)
        rows = self._session.execute(stmt).scalars().all()
        return [quest_from_db(r) for r in rows]

    def update(self, quest: Quest, campaign_id: str) -> None:
        """Update an existing quest (looked up by campaign_id + title)."""
        stmt = select(QuestRow).where(
            QuestRow.campaign_id == campaign_id,
            QuestRow.title == quest.title,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            msg = f"Quest '{quest.title}' not found in campaign '{campaign_id}'"
            raise ValueError(msg)
        row.description = quest.description
        row.status = quest.status.value
        row.objectives = [obj.model_dump() for obj in quest.objectives]  # type: ignore[assignment]
        row.reward_xp = quest.reward_xp
        row.reward_gold = quest.reward_gold
        row.giver_npc = quest.giver_npc

    def delete(self, title: str, campaign_id: str) -> None:
        """Delete a quest by title within a campaign."""
        stmt = select(QuestRow).where(
            QuestRow.campaign_id == campaign_id,
            QuestRow.title == title,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is not None:
            self._session.delete(row)
