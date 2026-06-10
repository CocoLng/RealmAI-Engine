"""Tests for db/mappers.py — domain ↔ DB round-trip fidelity."""

import json

import pytest
from sqlalchemy.orm import Session

from engine.character import AbilityScores, CharacterClass, Race, create_character
from engine.character.classes import CLASS_FEATURES
from engine.character.races import RACIAL_FEATURES
from engine.inventory import create_inventory
from engine.spells import create_spellcaster_state
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC, DialogueExchange, NPCDisposition
from world.quest import Quest, QuestStatus

from db.mappers import (
    backfill_character_features,
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
from db.models import PlayerCharacterRow


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

    def test_arrival_hook_roundtrip(self) -> None:
        """arrival_hook survives to_db → from_db."""
        loc = Location(
            name="Place des Néons",
            arrival_hook="Vous venez de sortir du monorail, trempés et hagards.",
        )
        row = location_to_db(loc, "c")
        restored = location_from_db(row)
        assert restored.arrival_hook == loc.arrival_hook

    def test_arrival_hook_defaults_empty(self) -> None:
        """Legacy rows with NULL arrival_hook restore as empty string."""
        loc = Location(name="Ancien Lieu")
        row = location_to_db(loc, "c")
        # Simulate a legacy row where the column was NULL.
        row.arrival_hook = None  # type: ignore[assignment]
        restored = location_from_db(row)
        assert restored.arrival_hook == ""


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


class TestCorruptedDataResilience:
    """A single corrupted JSON entry must not crash the whole entity load."""

    def test_corrupted_objective_is_skipped(
        self, sample_quest: Quest, caplog,
    ) -> None:
        row = quest_to_db(sample_quest, "c")
        # Inject a malformed objective alongside the valid ones.
        row.objectives = [
            row.objectives[0],
            {"not": "valid"},
            row.objectives[1],
        ]
        restored = quest_from_db(row)
        # Bad entry dropped; valid ones survive.
        assert len(restored.objectives) == 2
        assert restored.objectives[0].description == sample_quest.objectives[0].description

    def test_corrupted_zone_is_skipped(self) -> None:
        from world.combat_zone import Zone
        loc = Location(name="Hall", combat_zones=[Zone(name="Z1")])
        row = location_to_db(loc, "c")
        # Inject a bogus zone dict
        row.combat_zones = [row.combat_zones[0], {"bogus": "fields"}]  # missing name
        restored = location_from_db(row)
        assert len(restored.combat_zones) == 1
        assert restored.combat_zones[0].name == "Z1"

    def test_corrupted_dialogue_entry_is_skipped(self) -> None:
        npc = NPC(
            name="Gunther",
            race=Race.HUMAN,
            ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
            hp=10,
            max_hp=10,
            ac=10,
            disposition=NPCDisposition.NEUTRAL,
            dialogue_history=[
                DialogueExchange(player_said="Hi", npc_said="Hello"),
            ],
        )
        row = npc_to_db(npc, "c")
        row.dialogue_history = [
            row.dialogue_history[0],
            {"bogus": True},
        ]
        restored = npc_from_db(row)
        assert len(restored.dialogue_history) == 1
        assert restored.dialogue_history[0].player_said == "Hi"


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


class TestCorruptSaveErrors:
    """H3 — drifted/corrupt JSON blobs must surface an explicit error
    (player character, story arc) or degrade gracefully (spellcaster),
    never crash campaign load with a raw ValidationError."""

    def _pc_row(self) -> PlayerCharacterRow:
        scores = AbilityScores(STR=16, DEX=14, CON=13, INT=10, WIS=12, CHA=8)
        char = create_character("Thorin", Race.DWARF, CharacterClass.FIGHTER, scores)
        return player_character_to_db(123, "camp-1", char, create_inventory(), None)

    def test_corrupt_character_json_raises_explicit_error(self) -> None:
        from db.mappers import CorruptSaveError

        row = self._pc_row()
        row.character_json = '{"name": "Ghost"}'  # missing race/class/scores
        with pytest.raises(CorruptSaveError) as exc_info:
            player_character_from_db(row)
        err = exc_info.value
        assert err.entity == "Character"
        assert err.field  # names the faulty field
        assert "Character" in str(err)

    def test_unparseable_character_json_raises_explicit_error(self) -> None:
        from db.mappers import CorruptSaveError

        row = self._pc_row()
        row.character_json = "{not even json"
        with pytest.raises(CorruptSaveError) as exc_info:
            player_character_from_db(row)
        assert exc_info.value.entity == "Character"

    def test_corrupt_inventory_json_raises_explicit_error(self) -> None:
        from db.mappers import CorruptSaveError

        row = self._pc_row()
        row.inventory_json = '{"items": 42}'
        with pytest.raises(CorruptSaveError) as exc_info:
            player_character_from_db(row)
        assert exc_info.value.entity == "Inventory"
        assert "items" in exc_info.value.field

    def test_corrupt_spellcaster_json_degrades_to_none(self) -> None:
        row = self._pc_row()
        row.spellcaster_json = '{"bogus": 1}'  # missing spellcasting_ability
        uid, char, inv, spell = player_character_from_db(row)
        assert uid == 123
        assert char.name == "Thorin"
        assert spell is None

    def test_corrupt_story_arc_raises_explicit_error(self) -> None:
        from db.mappers import CorruptSaveError, story_arc_from_db
        from db.models import StoryArcRow

        row = StoryArcRow(
            campaign_id="camp-1",
            arc_json='{"campaign_id": "camp-1"}',  # missing beats/villain_name
            current_beat_index=0,
        )
        with pytest.raises(CorruptSaveError) as exc_info:
            story_arc_from_db(row)
        assert exc_info.value.entity == "StoryArc"


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


# ---------------------------------------------------------------------------
# Legacy JSON deserialization & backfill tests
# ---------------------------------------------------------------------------

# Minimal character_json produced *before* features/skill_proficiencies were added.
_LEGACY_CHARACTER_JSON = json.dumps({
    "name": "Thorin",
    "race": "Dwarf",
    "char_class": "Fighter",
    "level": 1,
    "xp": 0,
    "ability_scores": {"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 13, "CHA": 8},
    "hp": 11,
    "max_hp": 11,
    "ac": 16,
    "speed": 25,
    "proficiency_bonus": 2,
    "saving_throw_proficiencies": ["STR", "CON"],
    "hit_die": "1d10",
    "size": "Medium",
    # NOTE: "features" and "skill_proficiencies" are intentionally absent
})


class TestLegacyCharacterDeserialization:
    """Pydantic v2 fills defaults for missing fields in old JSON blobs."""

    def test_missing_features_defaults_to_empty_list(self) -> None:
        """Old JSON without 'features' key deserializes with features=[]."""
        from engine.character import Character

        char = Character.model_validate_json(_LEGACY_CHARACTER_JSON)
        assert char.features == []

    def test_missing_skill_proficiencies_defaults_to_empty_list(self) -> None:
        """Old JSON without 'skill_proficiencies' key deserializes with skill_proficiencies=[]."""
        from engine.character import Character

        char = Character.model_validate_json(_LEGACY_CHARACTER_JSON)
        assert char.skill_proficiencies == []

    def test_other_fields_preserved(self) -> None:
        """Legacy deserialization preserves all other fields correctly."""
        from engine.character import Character

        char = Character.model_validate_json(_LEGACY_CHARACTER_JSON)
        assert char.name == "Thorin"
        assert char.race == Race.DWARF
        assert char.char_class == CharacterClass.FIGHTER
        assert char.level == 1


class TestBackfillCharacterFeatures:
    """backfill_character_features() populates features for pre-refactor characters."""

    def test_backfill_dwarf_fighter(self) -> None:
        """Dwarf Fighter without features gets racial + class features after backfill."""
        from engine.character import Character

        char = Character.model_validate_json(_LEGACY_CHARACTER_JSON)
        assert char.features == [], "precondition: no features before backfill"

        result = backfill_character_features(char)

        expected_racial = RACIAL_FEATURES[Race.DWARF]
        expected_class = [
            f for f in CLASS_FEATURES[CharacterClass.FIGHTER]
            if f.level_requirement <= 1
        ]
        expected = list(expected_racial) + list(expected_class)
        assert result.features == expected

    def test_backfill_is_noop_when_features_already_present(self) -> None:
        """Characters that already have features are not modified by backfill."""
        scores = AbilityScores(STR=16, DEX=14, CON=13, INT=10, WIS=12, CHA=8)
        char = create_character("Thorin", Race.DWARF, CharacterClass.FIGHTER, scores)
        original_features = list(char.features)
        assert original_features, "precondition: create_character populates features"

        result = backfill_character_features(char)
        assert result.features == original_features

    def test_player_character_from_db_applies_backfill(self) -> None:
        """player_character_from_db() automatically backfills legacy rows."""
        from engine.inventory import create_inventory

        inv = create_inventory()
        row = PlayerCharacterRow(
            discord_user_id=42,
            campaign_id="camp-legacy",
            character_json=_LEGACY_CHARACTER_JSON,
            inventory_json=inv.model_dump_json(),
            spellcaster_json=None,
        )
        uid, char, _inv, _spell = player_character_from_db(row)
        assert uid == 42
        assert len(char.features) > 0, "backfill should have populated features"
        # Verify at least the Darkvision racial feature is present for Dwarf
        feature_names = {f.name for f in char.features}
        assert "Darkvision" in feature_names
        assert "Second Wind" in feature_names


class TestPlayerCharacterRepositoryIsolation:
    """Repository methods always require both user_id AND campaign_id (PK isolation)."""

    def test_save_requires_both_pk_parts(self) -> None:
        """save() signature enforces user_id and campaign_id as separate positional args."""
        import inspect
        from db.repositories.player_character_repo import PlayerCharacterRepository

        sig = inspect.signature(PlayerCharacterRepository.save)
        params = list(sig.parameters.keys())
        assert "user_id" in params
        assert "campaign_id" in params

    def test_get_requires_both_pk_parts(self) -> None:
        """get() signature enforces both PK parts."""
        import inspect
        from db.repositories.player_character_repo import PlayerCharacterRepository

        sig = inspect.signature(PlayerCharacterRepository.get)
        params = list(sig.parameters.keys())
        assert "user_id" in params
        assert "campaign_id" in params

    def test_update_requires_both_pk_parts(self) -> None:
        """update() signature enforces both PK parts."""
        import inspect
        from db.repositories.player_character_repo import PlayerCharacterRepository

        sig = inspect.signature(PlayerCharacterRepository.update)
        params = list(sig.parameters.keys())
        assert "user_id" in params
        assert "campaign_id" in params

    def test_delete_requires_both_pk_parts(self) -> None:
        """delete() signature enforces both PK parts."""
        import inspect
        from db.repositories.player_character_repo import PlayerCharacterRepository

        sig = inspect.signature(PlayerCharacterRepository.delete)
        params = list(sig.parameters.keys())
        assert "user_id" in params
        assert "campaign_id" in params

    def test_cannot_access_another_users_character(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        """A user_id lookup only returns rows matching that specific user_id."""
        from db.repositories.campaign_repo import CampaignRepository
        from db.repositories.player_character_repo import PlayerCharacterRepository

        # Persist campaign for FK integrity
        CampaignRepository(db_session).save(sample_campaign)
        db_session.flush()

        scores = AbilityScores(STR=16, DEX=14, CON=13, INT=10, WIS=12, CHA=8)
        char = create_character("Alice", Race.HUMAN, CharacterClass.FIGHTER, scores)
        inv = create_inventory()

        repo = PlayerCharacterRepository(db_session)
        user_alice = 111111
        user_bob = 222222
        repo.save(user_alice, sample_campaign.id, char, inv, None)
        db_session.flush()

        # Bob cannot see Alice's character
        assert repo.get(user_bob, sample_campaign.id) is None
        # Alice can see her own character
        result = repo.get(user_alice, sample_campaign.id)
        assert result is not None
        assert result[0].name == "Alice"
