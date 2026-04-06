"""Story arc repository — CRUD for campaign story arcs."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.mappers import story_arc_from_db, story_arc_to_db
from db.models import StoryArcRow
from world.story_arc import StoryArc


class StoryArcRepository:
    """Persistence operations for StoryArc entities."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, arc: StoryArc) -> None:
        """Insert a new story arc."""
        row = story_arc_to_db(arc)
        self._session.add(row)

    def get_by_campaign(self, campaign_id: str) -> StoryArc | None:
        """Fetch the story arc for a campaign, or None if not found."""
        stmt = select(StoryArcRow).where(
            StoryArcRow.campaign_id == campaign_id,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return story_arc_from_db(row)

    def update(self, arc: StoryArc) -> None:
        """Update an existing story arc (looked up by campaign_id)."""
        stmt = select(StoryArcRow).where(
            StoryArcRow.campaign_id == arc.campaign_id,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            msg = f"StoryArc not found for campaign '{arc.campaign_id}'"
            raise ValueError(msg)
        row.arc_json = arc.model_dump_json()
