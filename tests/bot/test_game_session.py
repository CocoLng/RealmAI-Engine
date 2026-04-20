"""Tests for bot/game_session.py — GameSession state management."""

from unittest.mock import patch

from bot.game_session import GameSession, create_ai_services
from engine.character import AbilityScores, CharacterClass, Race, create_character
from engine.inventory import create_inventory
from engine.spells import create_spellcaster_state
from world.campaign import Campaign
from world.npc import NPC, NPCDisposition
from world.quest import Quest, QuestStatus


def _make_campaign() -> Campaign:
    return Campaign(id="camp-1", name="Test", player_names=["Alice"])


class TestGameSession:
    """GameSession creation and field access."""

    def test_create_empty_session(self) -> None:
        session = GameSession(campaign=_make_campaign())
        assert session.campaign.id == "camp-1"
        assert session.characters == {}
        assert session.inventories == {}
        assert session.spellcasters == {}
        assert session.combat_state is None
        assert session.current_location is None
        assert session.narrator is None
        assert session.interpreter is None

    def test_add_character(self) -> None:
        session = GameSession(campaign=_make_campaign())
        scores = AbilityScores(STR=16, DEX=14, CON=13, INT=10, WIS=12, CHA=8)
        char = create_character("Thorin", Race.DWARF, CharacterClass.FIGHTER, scores)
        inv = create_inventory()
        spell = create_spellcaster_state(CharacterClass.FIGHTER, 1)

        user_id = 123
        session.characters[user_id] = char
        session.inventories[user_id] = inv
        session.spellcasters[user_id] = spell

        assert session.characters[user_id].name == "Thorin"
        assert session.inventories[user_id].gold == 0
        assert session.spellcasters[user_id] is None  # Fighters aren't spellcasters


class TestCreateAIServices:
    """AI service initialization with graceful failure."""

    def test_services_set_when_ollama_available(self) -> None:
        session = GameSession(campaign=_make_campaign())
        with patch("bot.game_session.OllamaClient") as mock_client:
            create_ai_services(session)
        assert session.ollama_client is not None
        assert session.narrator is not None
        assert session.interpreter is not None
        assert session.npc_agent is not None
        mock_client.assert_called_once()

    def test_services_none_when_ollama_unavailable(self) -> None:
        session = GameSession(campaign=_make_campaign())
        with patch("bot.game_session.OllamaClient", side_effect=ConnectionError):
            create_ai_services(session)
        assert session.ollama_client is None
        assert session.narrator is None
        assert session.interpreter is None
        assert session.npc_agent is None

    def test_semantic_indexer_created_alongside_semantic_memory(self) -> None:
        """When SemanticMemory initializes, so does SemanticIndexer."""
        from memory.indexer import SemanticIndexer

        session = GameSession(campaign=_make_campaign())
        with patch("bot.game_session.OllamaClient"):
            create_ai_services(session)
        assert session.semantic_memory is not None
        assert isinstance(session.semantic_indexer, SemanticIndexer)

    def test_semantic_indexer_none_when_semantic_memory_fails(self) -> None:
        """When SemanticMemory init raises, indexer is also None."""
        session = GameSession(campaign=_make_campaign())
        with patch("bot.game_session.OllamaClient"), patch(
            "bot.game_session.SemanticMemory", side_effect=RuntimeError("chroma down")
        ):
            create_ai_services(session)
        assert session.semantic_memory is None
        assert session.semantic_indexer is None


class TestGameSessionNpcsQuests:
    """Tests for npcs and quests fields on GameSession."""

    def test_session_has_npcs_field(self) -> None:
        session = GameSession(campaign=Campaign(id="t1", name="test"))
        assert session.npcs == {}

    def test_session_has_quests_field(self) -> None:
        session = GameSession(campaign=Campaign(id="t2", name="test"))
        assert session.quests == []

    def test_session_npcs_can_store_npc(self) -> None:
        session = GameSession(campaign=Campaign(id="t3", name="test"))
        npc = NPC(
            name="Barkeep",
            race=Race.HUMAN,
            level=1,
            ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
            hp=8,
            max_hp=8,
            ac=10,
            disposition=NPCDisposition.FRIENDLY,
        )
        session.npcs["Barkeep"] = npc
        assert session.npcs["Barkeep"].name == "Barkeep"

    def test_session_quests_can_store_quest(self) -> None:
        session = GameSession(campaign=Campaign(id="t4", name="test"))
        quest = Quest(title="Find the key", description="A key is lost", status=QuestStatus.ACTIVE)
        session.quests.append(quest)
        assert len(session.quests) == 1
        assert session.quests[0].title == "Find the key"
