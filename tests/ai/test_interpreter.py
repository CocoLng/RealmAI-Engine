"""Tests for the Interpreter module."""

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.interpreter import Interpreter
from ai.models import InterpretedAction
from engine.validators import ActionType
from tests.ai.conftest import make_ollama_response

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"


@pytest.fixture
def client() -> OllamaClient:
    return OllamaClient()


@pytest.fixture
def interpreter(client: OllamaClient) -> Interpreter:
    return Interpreter(client)


def test_interpret_attack_action(httpx_mock: HTTPXMock, interpreter: Interpreter) -> None:
    """Interpreter correctly parses an attack action."""
    response_data = {
        "action_type": "Attack",
        "actor_name": "Thorin",
        "target_name": "Goblin",
        "weapon_name": "axe",
        "spell_name": None,
        "item_name": None,
        "confidence": 0.95,
    }
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="I attack the goblin with my axe",
        actor_name="Thorin",
        available_actions=["Attack", "Cast Spell", "Defend", "Flee", "Use Item"],
    )

    assert isinstance(result, InterpretedAction)
    assert result.action_type == ActionType.ATTACK
    assert result.actor_name == "Thorin"
    assert result.target_name == "Goblin"
    assert result.weapon_name == "axe"
    assert result.confidence == 0.95
    assert result.raw_input == "I attack the goblin with my axe"


def test_interpret_cast_spell(httpx_mock: HTTPXMock, interpreter: Interpreter) -> None:
    """Interpreter correctly parses a spell cast."""
    response_data = {
        "action_type": "Cast Spell",
        "actor_name": "Merlin",
        "target_name": "Skeleton",
        "weapon_name": None,
        "spell_name": "Fireball",
        "item_name": None,
        "confidence": 0.9,
    }
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="cast fireball on the skeleton",
        actor_name="Merlin",
        available_actions=["Attack", "Cast Spell", "Defend", "Flee"],
    )

    assert result.action_type == ActionType.CAST_SPELL
    assert result.spell_name == "Fireball"
    assert result.target_name == "Skeleton"


def test_interpret_invalid_json_returns_low_confidence(
    httpx_mock: HTTPXMock, interpreter: Interpreter
) -> None:
    """If LLM returns invalid JSON, interpreter returns low-confidence Defend."""
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response("This is not JSON"))

    result = interpreter.interpret(
        player_text="uhhhh idk",
        actor_name="Thorin",
        available_actions=["Attack", "Defend"],
    )

    assert result.action_type == ActionType.DEFEND
    assert result.confidence == 0.0
    assert result.raw_input == "uhhhh idk"


def test_interpret_missing_action_type_falls_back(
    httpx_mock: HTTPXMock, interpreter: Interpreter
) -> None:
    """If LLM returns JSON but action_type is invalid, interpreter falls back."""
    httpx_mock.add_response(
        url=OLLAMA_URL,
        json=make_ollama_response({"action_type": "INVALID_ACTION", "actor_name": "Thorin", "confidence": 0.5}),
    )

    result = interpreter.interpret(
        player_text="do something",
        actor_name="Thorin",
        available_actions=["Attack", "Defend"],
    )

    assert result.action_type == ActionType.DEFEND
    assert result.confidence == 0.0
    assert result.raw_input == "do something"


def test_interpret_combat_context_included(
    httpx_mock: HTTPXMock, interpreter: Interpreter
) -> None:
    """Combat context is included in the request to the LLM."""
    response_data = {
        "action_type": "Flee",
        "actor_name": "Thorin",
        "target_name": None,
        "weapon_name": None,
        "spell_name": None,
        "item_name": None,
        "confidence": 0.8,
    }
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="I run away",
        actor_name="Thorin",
        available_actions=["Attack", "Flee"],
        combat_context="Round 3. Thorin has 5 HP.",
    )

    assert result.action_type == ActionType.FLEE


def test_interpret_use_item(httpx_mock: HTTPXMock, interpreter: Interpreter) -> None:
    """Interpreter correctly parses a use item action."""
    response_data = {
        "action_type": "Use Item",
        "actor_name": "Aria",
        "target_name": None,
        "weapon_name": None,
        "spell_name": None,
        "item_name": "Healing Potion",
        "confidence": 0.99,
    }
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="use my healing potion",
        actor_name="Aria",
        available_actions=["Attack", "Use Item", "Defend"],
    )

    assert result.action_type == ActionType.USE_ITEM
    assert result.item_name == "Healing Potion"
