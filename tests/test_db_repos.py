"""Tests for db/repositories/ — CRUD operations with in-memory SQLite."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.quest_repo import QuestRepository
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC, DialogueExchange, NPCDisposition
from world.quest import Quest, QuestStatus


# ---------------------------------------------------------------------------
# CampaignRepository
# ---------------------------------------------------------------------------


class TestCampaignRepository:
    """Campaign CRUD tests."""

    def test_save_and_get(self, db_session: Session, sample_campaign: Campaign) -> None:
        repo = CampaignRepository(db_session)
        repo.save(sample_campaign)
        db_session.commit()

        result = repo.get_by_id(sample_campaign.id)
        assert result is not None
        assert result.name == sample_campaign.name

    def test_get_missing_returns_none(self, db_session: Session) -> None:
        repo = CampaignRepository(db_session)
        assert repo.get_by_id("nonexistent") is None

    def test_list_all(self, db_session: Session) -> None:
        repo = CampaignRepository(db_session)
        repo.save(Campaign(id="c1", name="First"))
        repo.save(Campaign(id="c2", name="Second"))
        db_session.commit()

        results = repo.list_all()
        assert len(results) == 2
        names = {c.name for c in results}
        assert names == {"First", "Second"}

    def test_update(self, db_session: Session, sample_campaign: Campaign) -> None:
        repo = CampaignRepository(db_session)
        repo.save(sample_campaign)
        db_session.commit()

        updated = sample_campaign.model_copy(
            update={"name": "Updated Name", "interaction_count": 10}
        )
        repo.update(updated)
        db_session.commit()

        result = repo.get_by_id(sample_campaign.id)
        assert result is not None
        assert result.name == "Updated Name"
        assert result.interaction_count == 10

    def test_update_missing_raises(self, db_session: Session) -> None:
        repo = CampaignRepository(db_session)
        with pytest.raises(ValueError, match="not found"):
            repo.update(Campaign(id="nonexistent", name="X"))

    def test_delete(self, db_session: Session, sample_campaign: Campaign) -> None:
        repo = CampaignRepository(db_session)
        repo.save(sample_campaign)
        db_session.commit()

        repo.delete(sample_campaign.id)
        db_session.commit()

        assert repo.get_by_id(sample_campaign.id) is None

    def test_delete_missing_is_noop(self, db_session: Session) -> None:
        repo = CampaignRepository(db_session)
        repo.delete("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# NPCRepository
# ---------------------------------------------------------------------------


class TestNPCRepository:
    """NPC CRUD tests."""

    def test_save_and_get(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name("Gundren Rockseeker", sample_campaign.id)
        assert result is not None
        assert result.name == "Gundren Rockseeker"
        assert result.race == sample_npc.race
        assert result.ability_scores == sample_npc.ability_scores

    def test_get_missing_returns_none(self, db_session: Session) -> None:
        repo = NPCRepository(db_session)
        assert repo.get_by_name("Nobody", "no-campaign") is None

    def test_list_by_campaign(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)
        npc2 = sample_npc.model_copy(update={"name": "Sildar Hallwinter", "location_name": "Phandalin"})
        repo.save(npc2, sample_campaign.id)
        db_session.commit()

        results = repo.list_by_campaign(sample_campaign.id)
        assert len(results) == 2

    def test_list_by_location(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)  # location: Neverwinter
        npc2 = sample_npc.model_copy(update={"name": "Sildar", "location_name": "Phandalin"})
        repo.save(npc2, sample_campaign.id)
        db_session.commit()

        results = repo.list_by_location("Neverwinter", sample_campaign.id)
        assert len(results) == 1
        assert results[0].name == "Gundren Rockseeker"

    def test_update(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)
        db_session.commit()

        updated = sample_npc.model_copy(update={"hp": 10, "disposition": NPCDisposition.HOSTILE})
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_npc.name, sample_campaign.id)
        assert result is not None
        assert result.hp == 10
        assert result.disposition == NPCDisposition.HOSTILE

    def test_update_preserves_all_fields(
        self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC,
    ) -> None:
        """Regression: update() must persist aliases, secrets, knowledge, dialogue_history."""
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)

        # Save NPC with rich data in the four previously-lost fields
        npc_with_data = sample_npc.model_copy(update={
            "aliases": ["Gundren", "The Rockseeker"],
            "secrets": ["Knows location of Wave Echo Cave"],
            "knowledge": ["Phandalin history", "Mining lore"],
            "dialogue_history": [
                DialogueExchange(
                    player_said="Where is the cave?",
                    npc_said="I cannot tell you yet.",
                    revealed=["cave_exists"],
                ),
            ],
        })
        repo.save(npc_with_data, sample_campaign.id)
        db_session.commit()

        # Update: change disposition AND add more dialogue
        updated = npc_with_data.model_copy(update={
            "disposition": NPCDisposition.HOSTILE,
            "dialogue_history": npc_with_data.dialogue_history + [
                DialogueExchange(
                    player_said="Tell me now!",
                    npc_said="Fine, it is to the east.",
                    revealed=["cave_location"],
                ),
            ],
            "secrets": ["Knows location of Wave Echo Cave", "Has a map"],
        })
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_npc.name, sample_campaign.id)
        assert result is not None
        assert result.disposition == NPCDisposition.HOSTILE
        assert result.aliases == ["Gundren", "The Rockseeker"]
        assert result.secrets == ["Knows location of Wave Echo Cave", "Has a map"]
        assert result.knowledge == ["Phandalin history", "Mining lore"]
        assert len(result.dialogue_history) == 2
        assert result.dialogue_history[0].player_said == "Where is the cave?"
        assert result.dialogue_history[1].npc_said == "Fine, it is to the east."
        assert result.dialogue_history[1].revealed == ["cave_location"]

    def test_update_missing_raises(self, db_session: Session, sample_npc: NPC) -> None:
        repo = NPCRepository(db_session)
        with pytest.raises(ValueError, match="not found"):
            repo.update(sample_npc, "no-campaign")

    def test_delete(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)
        db_session.commit()

        repo.delete(sample_npc.name, sample_campaign.id)
        db_session.commit()

        assert repo.get_by_name(sample_npc.name, sample_campaign.id) is None

    def test_unique_constraint(self, db_session: Session, sample_campaign: Campaign, sample_npc: NPC) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = NPCRepository(db_session)
        repo.save(sample_npc, sample_campaign.id)
        db_session.commit()

        repo.save(sample_npc, sample_campaign.id)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


# ---------------------------------------------------------------------------
# LocationRepository
# ---------------------------------------------------------------------------


class TestLocationRepository:
    """Location CRUD tests."""

    def test_save_and_get(self, db_session: Session, sample_campaign: Campaign, sample_location: Location) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name("Neverwinter", sample_campaign.id)
        assert result is not None
        assert result.description == sample_location.description
        assert result.connections == ["Phandalin", "Triboar"]

    def test_get_missing_returns_none(self, db_session: Session) -> None:
        repo = LocationRepository(db_session)
        assert repo.get_by_name("Nowhere", "no-campaign") is None

    def test_list_by_campaign(self, db_session: Session, sample_campaign: Campaign, sample_location: Location) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        repo.save(Location(name="Phandalin"), sample_campaign.id)
        db_session.commit()

        results = repo.list_by_campaign(sample_campaign.id)
        assert len(results) == 2

    def test_update(self, db_session: Session, sample_campaign: Campaign, sample_location: Location) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        updated = sample_location.model_copy(update={"description": "A ruined city"})
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_name(sample_location.name, sample_campaign.id)
        assert result is not None
        assert result.description == "A ruined city"

    def test_update_missing_raises(self, db_session: Session, sample_location: Location) -> None:
        repo = LocationRepository(db_session)
        with pytest.raises(ValueError, match="not found"):
            repo.update(sample_location, "no-campaign")

    def test_delete(self, db_session: Session, sample_campaign: Campaign, sample_location: Location) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        repo.delete(sample_location.name, sample_campaign.id)
        db_session.commit()

        assert repo.get_by_name(sample_location.name, sample_campaign.id) is None

    def test_unique_constraint(self, db_session: Session, sample_campaign: Campaign, sample_location: Location) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = LocationRepository(db_session)
        repo.save(sample_location, sample_campaign.id)
        db_session.commit()

        repo.save(sample_location, sample_campaign.id)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


# ---------------------------------------------------------------------------
# QuestRepository
# ---------------------------------------------------------------------------


class TestQuestRepository:
    """Quest CRUD tests."""

    def test_save_and_get(self, db_session: Session, sample_campaign: Campaign, sample_quest: Quest) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = QuestRepository(db_session)
        repo.save(sample_quest, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_title("Find the Lost Mine", sample_campaign.id)
        assert result is not None
        assert result.status == QuestStatus.ACTIVE
        assert len(result.objectives) == 2
        assert result.objectives[1].is_complete is True

    def test_get_missing_returns_none(self, db_session: Session) -> None:
        repo = QuestRepository(db_session)
        assert repo.get_by_title("Nothing", "no-campaign") is None

    def test_list_by_campaign(self, db_session: Session, sample_campaign: Campaign, sample_quest: Quest) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = QuestRepository(db_session)
        repo.save(sample_quest, sample_campaign.id)
        repo.save(Quest(title="Side Quest"), sample_campaign.id)
        db_session.commit()

        results = repo.list_by_campaign(sample_campaign.id)
        assert len(results) == 2

    def test_update_status(self, db_session: Session, sample_campaign: Campaign, sample_quest: Quest) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = QuestRepository(db_session)
        repo.save(sample_quest, sample_campaign.id)
        db_session.commit()

        updated = sample_quest.model_copy(update={"status": QuestStatus.COMPLETED})
        repo.update(updated, sample_campaign.id)
        db_session.commit()

        result = repo.get_by_title(sample_quest.title, sample_campaign.id)
        assert result is not None
        assert result.status == QuestStatus.COMPLETED

    def test_update_missing_raises(self, db_session: Session, sample_quest: Quest) -> None:
        repo = QuestRepository(db_session)
        with pytest.raises(ValueError, match="not found"):
            repo.update(sample_quest, "no-campaign")

    def test_delete(self, db_session: Session, sample_campaign: Campaign, sample_quest: Quest) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = QuestRepository(db_session)
        repo.save(sample_quest, sample_campaign.id)
        db_session.commit()

        repo.delete(sample_quest.title, sample_campaign.id)
        db_session.commit()

        assert repo.get_by_title(sample_quest.title, sample_campaign.id) is None

    def test_unique_constraint(self, db_session: Session, sample_campaign: Campaign, sample_quest: Quest) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = QuestRepository(db_session)
        repo.save(sample_quest, sample_campaign.id)
        db_session.commit()

        repo.save(sample_quest, sample_campaign.id)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


# ---------------------------------------------------------------------------
# Cascade deletes
# ---------------------------------------------------------------------------


class TestCascadeDeletes:
    """Verify that deleting a campaign cascades to all children."""

    def test_delete_campaign_cascades(
        self,
        db_session: Session,
        sample_campaign: Campaign,
        sample_npc: NPC,
        sample_location: Location,
        sample_quest: Quest,
    ) -> None:
        camp_repo = CampaignRepository(db_session)
        camp_repo.save(sample_campaign)

        NPCRepository(db_session).save(sample_npc, sample_campaign.id)
        LocationRepository(db_session).save(sample_location, sample_campaign.id)
        QuestRepository(db_session).save(sample_quest, sample_campaign.id)
        db_session.commit()

        camp_repo.delete(sample_campaign.id)
        db_session.commit()

        assert NPCRepository(db_session).list_by_campaign(sample_campaign.id) == []
        assert LocationRepository(db_session).list_by_campaign(sample_campaign.id) == []
        assert QuestRepository(db_session).list_by_campaign(sample_campaign.id) == []
