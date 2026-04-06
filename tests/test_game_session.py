"""Tests for bot/game_session.py — GameSession state management."""

from unittest.mock import patch

from bot.game_session import GameSession, create_ai_services
from engine.character import AbilityScores, CharacterClass, Race, create_character
from engine.inventory import create_inventory
from engine.spells import create_spellcaster_state
from world.campaign import Campaign


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
