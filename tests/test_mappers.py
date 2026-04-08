"""Tests for db/mappers.py — domain ↔ DB round-trip fidelity."""

from sqlalchemy.orm import Session

from engine.character import AbilityScores, CharacterClass, Race, create_character
from engine.inventory import create_inventory
from engine.spells import create_spellcaster_state
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC, DialogueExchange, NPCDisposition
from world.quest import Quest, QuestStatus

from db.mappers import (
    campaign_channel_from_db,
    campaign_channel_to_db,
    campaign_from_db,
    campaign_to_db,
    location_from_db,
    location_to_db,
    npc_from_db,
    npc_to_db,
    player_character_from_db,
    player_character_to_db,
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

    def test_roundtrip_with_combat_state(self) -> None:
        campaign = Campaign(
            id="c-combat",
            name="Battle Test",
            combat_state_json='{"combatants":[],"round_number":3,"current_turn_index":0,"is_active":true}',
        )
        row = campaign_to_db(campaign)
        assert row.combat_state_json == campaign.combat_state_json
        restored = campaign_from_db(row)
        assert restored.combat_state_json == campaign.combat_state_json

    def test_roundtrip_without_combat_state(self) -> None:
        campaign = Campaign(id="c-no-combat", name="Peaceful")
        row = campaign_to_db(campaign)
        assert row.combat_state_json is None
        restored = campaign_from_db(row)
        assert restored.combat_state_json is None

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

    def test_roundtrip_dialogue_fields(
        self, sample_ability_scores: AbilityScores
    ) -> None:
        """secrets, knowledge, and dialogue_history round-trip via mappers."""
        npc = NPC(
            name="Elyra",
            race=Race.ELF,
            ability_scores=sample_ability_scores,
            hp=10,
            max_hp=10,
            ac=12,
            secrets=["knows the king's bastard", "smuggles artifacts"],
            knowledge=["ancient elven runes", "forest paths"],
            dialogue_history=[
                DialogueExchange(
                    player_said="Tell me about the ruins.",
                    npc_said="They predate the Sundering.",
                    revealed=["ruins age"],
                ),
                DialogueExchange(
                    player_said="Anything else?",
                    npc_said="Beware the wraiths.",
                ),
            ],
        )
        row = npc_to_db(npc, "camp-1")
        assert row.secrets == ["knows the king's bastard", "smuggles artifacts"]
        assert row.knowledge == ["ancient elven runes", "forest paths"]
        assert isinstance(row.dialogue_history, list)
        assert row.dialogue_history[0]["player_said"] == "Tell me about the ruins."
        restored = npc_from_db(row)
        assert restored == npc
        assert len(restored.dialogue_history) == 2
        assert isinstance(restored.dialogue_history[0], DialogueExchange)
        assert restored.dialogue_history[0].revealed == ["ruins age"]

    def test_sqlite_roundtrip_dialogue_fields(
        self, db_session: Session, sample_ability_scores: AbilityScores,
        sample_campaign: Campaign,
    ) -> None:
        """Full SQLite persist + reload preserves new NPC fields."""
        from db.mappers import campaign_to_db

        db_session.add(campaign_to_db(sample_campaign))
        db_session.flush()
        npc = NPC(
            name="Borin",
            race=Race.DWARF,
            ability_scores=sample_ability_scores,
            hp=8,
            max_hp=8,
            ac=15,
            secrets=["hides forge-key"],
            knowledge=["runesmithing"],
            dialogue_history=[
                DialogueExchange(
                    player_said="Hello",
                    npc_said="Well met.",
                    revealed=["greeted"],
                )
            ],
        )
        row = npc_to_db(npc, sample_campaign.id)
        db_session.add(row)
        db_session.commit()
        db_session.expire_all()

        from db.models import NPCRow

        loaded = db_session.query(NPCRow).filter_by(name="Borin").one()
        restored = npc_from_db(loaded)
        assert restored.secrets == ["hides forge-key"]
        assert restored.knowledge == ["runesmithing"]
        assert len(restored.dialogue_history) == 1
        assert restored.dialogue_history[0].npc_said == "Well met."
        assert restored.dialogue_history[0].revealed == ["greeted"]

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


class TestPlayerCharacterMapper:
    """PlayerCharacter mapper round-trip tests."""

    def test_roundtrip_no_spellcaster(self) -> None:
        scores = AbilityScores(STR=16, DEX=14, CON=13, INT=10, WIS=12, CHA=8)
        char = create_character("Thorin", Race.DWARF, CharacterClass.FIGHTER, scores)
        inv = create_inventory()
        row = player_character_to_db(123, "camp-1", char, inv, None)
        assert row.discord_user_id == 123
        assert row.campaign_id == "camp-1"
        assert row.spellcaster_json is None

        uid, restored_char, restored_inv, restored_spell = player_character_from_db(row)
        assert uid == 123
        assert restored_char.name == "Thorin"
        assert restored_char.race == Race.DWARF
        assert restored_inv.gold == 0
        assert restored_spell is None

    def test_roundtrip_with_spellcaster(self) -> None:
        scores = AbilityScores(STR=8, DEX=14, CON=12, INT=16, WIS=13, CHA=10)
        char = create_character("Elara", Race.ELF, CharacterClass.WIZARD, scores)
        inv = create_inventory()
        spell = create_spellcaster_state(CharacterClass.WIZARD, 1)
        assert spell is not None

        row = player_character_to_db(456, "camp-2", char, inv, spell)
        assert row.spellcaster_json is not None

        uid, restored_char, restored_inv, restored_spell = player_character_from_db(row)
        assert uid == 456
        assert restored_char.name == "Elara"
        assert restored_spell is not None
        assert restored_spell.spellcasting_ability == spell.spellcasting_ability

    def test_character_json_contains_data(self) -> None:
        scores = AbilityScores(STR=16, DEX=14, CON=13, INT=10, WIS=12, CHA=8)
        char = create_character("Test", Race.HUMAN, CharacterClass.FIGHTER, scores)
        inv = create_inventory()
        row = player_character_to_db(1, "c", char, inv, None)
        assert "Test" in row.character_json
        assert "Human" in row.character_json


class TestCampaignChannelMapper:
    """CampaignChannel mapper round-trip tests."""

    def test_roundtrip(self) -> None:
        row = campaign_channel_to_db(111, "camp-1", 222)
        assert row.channel_id == 111
        assert row.campaign_id == "camp-1"
        assert row.guild_id == 222

        channel_id, campaign_id, guild_id = campaign_channel_from_db(row)
        assert channel_id == 111
        assert campaign_id == "camp-1"
        assert guild_id == 222
