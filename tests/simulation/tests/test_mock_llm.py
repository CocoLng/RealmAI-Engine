"""Tests for tests/simulation/mock_llm.py — MockOllamaClient."""

from __future__ import annotations

from tests.simulation.mock_llm import MockOllamaClient


def test_mock_returns_agent_intent_json() -> None:
    """When called like an agent (no model-specific hint), returns a valid AgentIntent JSON."""
    client = MockOllamaClient()
    result = client.chat_json(
        model="qwen3.5:4b",
        messages=[
            {"role": "system", "content": "You are an autonomous player..."},
            {"role": "user", "content": "TURN 1\nYou play: Aria"},
        ],
    )
    # result should be a dict that can be model_validated to AgentIntent
    assert isinstance(result, dict)
    assert "action" in result
    # Must be a legal action
    assert result["action"] in {
        "attack", "cast_spell", "defend", "flee", "move", "look", "talk",
        "search", "equip", "unequip", "use_item", "free_form", "wait",
    }


def test_mock_returns_narrator_response_for_narrator_call() -> None:
    """When called with a narrator-style prompt, returns a narration string."""
    client = MockOllamaClient()
    result = client.chat_json(
        model="qwen3.5:9b",
        messages=[
            {"role": "system", "content": "You are the Narrator. Describe the scene..."},
            {"role": "user", "content": "Action: look at the cave"},
        ],
    )
    assert isinstance(result, dict)
    # narrator responses include some text under a "narration" or "text" key, or a json with narrative content
    assert any(k in result for k in ("narration", "text", "content"))


def test_mock_returns_interpreter_response_for_interpreter_call() -> None:
    """When called with an interpreter-style prompt, returns a structured action JSON."""
    client = MockOllamaClient()
    result = client.chat_json(
        model="qwen3.5:4b",
        messages=[
            {"role": "system", "content": "You are the Interpreter. Parse the player text..."},
            {"role": "user", "content": "I attack the goblin"},
        ],
    )
    assert isinstance(result, dict)


def test_mock_temperature_argument_accepted() -> None:
    """Mock accepts temperature kwarg without raising."""
    client = MockOllamaClient()
    result = client.chat_json(
        model="qwen3.5:4b",
        messages=[{"role": "user", "content": "x"}],
        temperature=0.0,
    )
    assert result is not None


def test_mock_signature_compatible_with_simulation_mode() -> None:
    """Mock has a simulation_mode constructor like the real client (no-op)."""
    client = MockOllamaClient(simulation_mode=True)
    assert client is not None
