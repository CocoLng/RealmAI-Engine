"""Tests for db/mappers.py — domain ↔ DB round-trip fidelity."""

from engine.character import AbilityScores, Race
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC, NPCDisposition
from world.quest import Quest, QuestStatus

from db.mappers import (
    campaign_from_db,
    campaign_to_db,
    location_from_db,
    location_to_db,
    npc_from_db,
    npc_to_db,
    quest_from_db,
    quest_to_db,
)


class TestCampaignMapper:
    """Campaign mapper round-trip tests."""

    def test_roundtrip(self, sample_campaign: Campaign) -> None:
        row = campaign_to_db(sample_campaign)
        restored = campaign_from_db(row)
        assert restored.id == sample_campaign.id
        assert restored.name == sample_campaign.name
        assert restored.player_names == sample_campaign.player_names
        assert restored.current_location == sample_campaign.current_location
        assert restored.interaction_count == sample_campaign.interaction_count

    def test_with_players(self) -> None:
        campaign = Campaign(
            id="c1",
            name="Test",
            player_names=["Alice", "Bob"],
            current_location="Tavern",
            interaction_count=42,
        )
        row = campaign_to_db(campaign)
        restored = campaign_from_db(row)
        assert restored.player_names == ["Alice", "Bob"]
        assert restored.current_location == "Tavern"
        assert restored.interaction_count == 42


class TestNPCMapper:
    """NPC mapper round-trip tests."""

    def test_roundtrip_full(self, sample_npc: NPC) -> None:
        row = npc_to_db(sample_npc, "camp-1")
        assert row.campaign_id == "camp-1"
        restored = npc_from_db(row)
        assert restored == sample_npc

    def test_roundtrip_no_class(self, sample_ability_scores: AbilityScores) -> None:
        npc = NPC(
            name="Commoner",
            race=Race.HUMAN,
            ability_scores=sample_ability_scores,
            hp=4,
            max_hp=4,
            ac=10,
        )
        row = npc_to_db(npc, "camp-1")
        assert row.char_class is None
        restored = npc_from_db(row)
        assert restored.char_class is None
        assert restored == npc

    def test_ability_scores_serialization(self, sample_npc: NPC) -> None:
        row = npc_to_db(sample_npc, "camp-1")
        assert isinstance(row.ability_scores, dict)
        assert row.ability_scores["STR"] == 15
        assert row.ability_scores["CHA"] == 8

    def test_enum_values_stored_as_strings(self, sample_npc: NPC) -> None:
        row = npc_to_db(sample_npc, "camp-1")
        assert row.race == "Dwarf"
        assert row.char_class == "Fighter"
        assert row.disposition == "friendly"

    def test_all_dispositions(self, sample_ability_scores: AbilityScores) -> None:
        for disp in NPCDisposition:
            npc = NPC(
                name="Test",
                race=Race.HUMAN,
                ability_scores=sample_ability_scores,
                hp=1,
                max_hp=1,
                ac=10,
                disposition=disp,
            )
            row = npc_to_db(npc, "c")
            restored = npc_from_db(row)
            assert restored.disposition == disp


class TestLocationMapper:
    """Location mapper round-trip tests."""

    def test_roundtrip(self, sample_location: Location) -> None:
        row = location_to_db(sample_location, "camp-1")
        assert row.campaign_id == "camp-1"
        restored = location_from_db(row)
        assert restored == sample_location

    def test_empty_lists(self) -> None:
        loc = Location(name="Void")
        row = location_to_db(loc, "c")
        restored = location_from_db(row)
        assert restored.connections == []
        assert restored.npcs_present == []
        assert restored.items_available == []


class TestQuestMapper:
    """Quest mapper round-trip tests."""

    def test_roundtrip(self, sample_quest: Quest) -> None:
        row = quest_to_db(sample_quest, "camp-1")
        assert row.campaign_id == "camp-1"
        restored = quest_from_db(row)
        assert restored == sample_quest

    def test_objectives_serialization(self, sample_quest: Quest) -> None:
        row = quest_to_db(sample_quest, "c")
        assert isinstance(row.objectives, list)
        assert len(row.objectives) == 2
        assert row.objectives[0]["description"] == "Talk to Gundren"
        assert row.objectives[1]["is_complete"] is True

    def test_empty_objectives(self) -> None:
        quest = Quest(title="Empty")
        row = quest_to_db(quest, "c")
        restored = quest_from_db(row)
        assert restored.objectives == []

    def test_all_statuses(self) -> None:
        for status in QuestStatus:
            quest = Quest(title="Test", status=status)
            row = quest_to_db(quest, "c")
            restored = quest_from_db(row)
            assert restored.status == status

    def test_status_stored_as_string(self, sample_quest: Quest) -> None:
        row = quest_to_db(sample_quest, "c")
        assert row.status == "active"
