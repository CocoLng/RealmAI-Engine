"""Story arc repository — CRUD for campaign story arcs."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.mappers import story_arc_from_db, story_arc_to_db
from db.models import CampaignChannelRow, CampaignRow, StoryArcRow
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
        row.current_beat_index = arc.current_beat_index
        row.archetype = arc.archetype

    def upsert(self, arc: StoryArc) -> None:
        """Insert or update a story arc, keyed by campaign_id."""
        stmt = select(StoryArcRow).where(
            StoryArcRow.campaign_id == arc.campaign_id,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            self._session.add(story_arc_to_db(arc))
            return
        row.arc_json = arc.model_dump_json()
        row.current_beat_index = arc.current_beat_index
        row.archetype = arc.archetype

    def get_latest_archetype_for_guild(
        self,
        guild_id: int,
        exclude_campaign_id: str | None = None,
    ) -> str | None:
        """Return the arc archetype of the guild's most recent campaign.

        Feeds ``engine.arc_recipes.generate_recipe(previous_archetype=...)``
        so two consecutive campaigns on the same Discord server don't reuse
        the same narrative shape. Campaigns are ordered by creation date;
        arcs with no recorded archetype (legacy) are skipped rather than
        treated as "no history".

        Args:
            guild_id: Discord guild whose campaign history to inspect.
            exclude_campaign_id: Campaign to leave out — typically the one
                being launched right now.

        Returns:
            The archetype value (e.g. ``"heist"``), or ``None`` when the
            guild has no campaign carrying one.
        """
        stmt = (
            select(StoryArcRow.archetype)
            .join(CampaignRow, CampaignRow.id == StoryArcRow.campaign_id)
            .join(
                CampaignChannelRow,
                CampaignChannelRow.campaign_id == StoryArcRow.campaign_id,
            )
            .where(
                CampaignChannelRow.guild_id == guild_id,
                StoryArcRow.archetype.is_not(None),
            )
            .order_by(CampaignRow.created_at.desc())
            .limit(1)
        )
        if exclude_campaign_id is not None:
            stmt = stmt.where(StoryArcRow.campaign_id != exclude_campaign_id)
        return self._session.execute(stmt).scalars().first()

    def update_beat_index(self, campaign_id: str, index: int) -> None:
        """Update only the current_beat_index column (efficient partial update)."""
        stmt = select(StoryArcRow).where(
            StoryArcRow.campaign_id == campaign_id,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            msg = f"StoryArc not found for campaign '{campaign_id}'"
            raise ValueError(msg)
        row.current_beat_index = index
