"""Tests for the arc-archetype persistence used by cross-campaign variety.

``engine.arc_recipes.generate_recipe`` can exclude the archetype used by the
previous campaign of the same Discord guild — which requires the archetype to
survive in the database. These tests cover the storage column, the mapper
round-trip, and the guild-scoped lookup.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from db.repositories.campaign_channel_repo import CampaignChannelRepository
from db.repositories.campaign_repo import CampaignRepository
from db.repositories.story_arc_repo import StoryArcRepository
from world.campaign import Campaign
from world.story_arc import StoryArc, StoryBeat

_TYPES = ["social", "combat", "exploration", "puzzle", "boss"]


def _make_arc(campaign_id: str, archetype: str | None = None) -> StoryArc:
    beats = [
        StoryBeat(
            beat_number=i + 1,
            title=f"Beat {i + 1}",
            description="A meaningful beat description.",
            location_hint=f"Place {i + 1}",
            encounter_type=_TYPES[i % len(_TYPES)],
        )
        for i in range(8)
    ]
    return StoryArc(
        campaign_id=campaign_id,
        theme="mystery",
        premise="A long-buried evil has stirred and the heroes must investigate.",
        beats=beats,
        villain_name="The Hollow King",
        villain_motivation="Restore his sundered realm",
        archetype=archetype,
    )


def _seed(
    db_session: Session,
    *,
    campaign_id: str,
    guild_id: int,
    archetype: str | None,
    created_at: datetime,
    channel_id: int,
) -> None:
    CampaignRepository(db_session).save(
        Campaign(id=campaign_id, name=campaign_id, created_at=created_at),
    )
    db_session.flush()
    CampaignChannelRepository(db_session).save(channel_id, campaign_id, guild_id)
    StoryArcRepository(db_session).upsert(_make_arc(campaign_id, archetype))
    db_session.commit()


class TestArchetypeRoundTrip:
    """The archetype must survive a save → load cycle."""

    def test_archetype_round_trips(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = StoryArcRepository(db_session)
        repo.upsert(_make_arc(sample_campaign.id, "heist"))
        db_session.commit()

        loaded = repo.get_by_campaign(sample_campaign.id)
        assert loaded is not None
        assert loaded.archetype == "heist"

    def test_archetype_defaults_to_none(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        """Legacy arcs carry no archetype — that must stay valid."""
        CampaignRepository(db_session).save(sample_campaign)
        repo = StoryArcRepository(db_session)
        repo.upsert(_make_arc(sample_campaign.id))
        db_session.commit()

        loaded = repo.get_by_campaign(sample_campaign.id)
        assert loaded is not None
        assert loaded.archetype is None

    def test_update_refreshes_the_archetype_column(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = StoryArcRepository(db_session)
        repo.save(_make_arc(sample_campaign.id, "siege"))
        db_session.commit()

        repo.update(_make_arc(sample_campaign.id, "mystery"))
        db_session.commit()

        assert repo.get_latest_archetype_for_guild(1) is None  # no channel mapping
        loaded = repo.get_by_campaign(sample_campaign.id)
        assert loaded is not None
        assert loaded.archetype == "mystery"


class TestLatestArchetypeForGuild:
    """Guild-scoped lookup feeding ``previous_archetype``."""

    def test_returns_none_when_guild_has_no_history(
        self, db_session: Session,
    ) -> None:
        repo = StoryArcRepository(db_session)
        assert repo.get_latest_archetype_for_guild(42) is None

    def test_returns_most_recent_campaign_archetype(
        self, db_session: Session,
    ) -> None:
        now = datetime.now(timezone.utc)
        _seed(
            db_session, campaign_id="old", guild_id=42, archetype="siege",
            created_at=now - timedelta(days=2), channel_id=1,
        )
        _seed(
            db_session, campaign_id="recent", guild_id=42, archetype="heist",
            created_at=now - timedelta(hours=1), channel_id=2,
        )

        repo = StoryArcRepository(db_session)
        assert repo.get_latest_archetype_for_guild(42) == "heist"

    def test_ignores_other_guilds(self, db_session: Session) -> None:
        now = datetime.now(timezone.utc)
        _seed(
            db_session, campaign_id="mine", guild_id=42, archetype="siege",
            created_at=now - timedelta(days=2), channel_id=1,
        )
        _seed(
            db_session, campaign_id="theirs", guild_id=99, archetype="heist",
            created_at=now, channel_id=2,
        )

        repo = StoryArcRepository(db_session)
        assert repo.get_latest_archetype_for_guild(42) == "siege"

    def test_skips_arcs_without_archetype(self, db_session: Session) -> None:
        """A legacy arc must not mask the last known archetype."""
        now = datetime.now(timezone.utc)
        _seed(
            db_session, campaign_id="legacy", guild_id=42, archetype=None,
            created_at=now, channel_id=1,
        )
        _seed(
            db_session, campaign_id="known", guild_id=42, archetype="revenge",
            created_at=now - timedelta(days=1), channel_id=2,
        )

        repo = StoryArcRepository(db_session)
        assert repo.get_latest_archetype_for_guild(42) == "revenge"

    def test_excludes_the_requesting_campaign(self, db_session: Session) -> None:
        now = datetime.now(timezone.utc)
        _seed(
            db_session, campaign_id="current", guild_id=42, archetype="escape",
            created_at=now, channel_id=1,
        )
        _seed(
            db_session, campaign_id="previous", guild_id=42, archetype="betrayal",
            created_at=now - timedelta(days=1), channel_id=2,
        )

        repo = StoryArcRepository(db_session)
        assert repo.get_latest_archetype_for_guild(
            42, exclude_campaign_id="current",
        ) == "betrayal"
