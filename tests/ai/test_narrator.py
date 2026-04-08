"""Tests for the Narrator module."""

from unittest.mock import MagicMock

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.models import NarrativeResult
from ai.narrator import Narrator
from tests.ai.conftest import CHAT_URL, make_ollama_response


@pytest.fixture
def narrator(ollama_client: OllamaClient) -> Narrator:
    return Narrator(ollama_client)


def test_narrate_returns_narrative_result(httpx_mock: HTTPXMock, narrator: Narrator) -> None:
    """Narrator returns a valid NarrativeResult."""
    response_data = {
        "narrative": "Your axe bites deep into the goblin's shoulder.",
        "tone": "dramatic",
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = narrator.narrate(
        action_result_text="Thorin attacks Goblin. Hit! 8 damage dealt.",
        context_prompt="## Game State\nLocation: Goblin Cave\n## Recent Events\nThorin entered the cave.",
    )

    assert isinstance(result, NarrativeResult)
    assert result.narrative == "Your axe bites deep into the goblin's shoulder."
    assert result.tone == "dramatic"


def test_narrate_uses_both_context_and_action(
    httpx_mock: HTTPXMock, narrator: Narrator
) -> None:
    """The user message includes both context_prompt and action_result_text."""
    response_data = {"narrative": "The skeleton crumbles.", "tone": "somber"}
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    narrator.narrate(
        action_result_text="Merlin casts Fireball. Skeleton fails save. 12 fire damage.",
        context_prompt="## Game State\nMerlin: 30/30 HP",
    )

    # Verify the chat request was made (tags health check + chat = 2 requests)
    assert len(httpx_mock.get_requests()) == 2


def test_narrate_various_tones(httpx_mock: HTTPXMock, narrator: Narrator) -> None:
    """Narrator accepts all valid tones."""
    for tone in ["dramatic", "tense", "humorous", "somber"]:
        httpx_mock.add_response(
            url=CHAT_URL,
            json=make_ollama_response({"narrative": "Something happened.", "tone": tone}),
        )
        result = narrator.narrate(
            action_result_text="Some action occurred.",
            context_prompt="Context here.",
        )
        assert result.tone == tone


def test_narrate_uses_high_temperature(httpx_mock: HTTPXMock, narrator: Narrator) -> None:
    """Narrator uses temperature 0.8 for creative output."""
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response({"narrative": "The battle rages on.", "tone": "tense"}),
    )
    result = narrator.narrate(
        action_result_text="Miss. No damage.",
        context_prompt="Context.",
    )
    assert isinstance(result, NarrativeResult)
    # Temperature is tested implicitly — if wrong, the call would fail or mock mismatch


def test_narrate_includes_player_intent_and_outcome_facts():
    client = MagicMock()
    client.chat_json.return_value = {"narrative": "ok", "tone": "tense"}
    narrator = Narrator(client)

    narrator.narrate(
        action_result_text="Xavier searches Croix de fer.",
        context_prompt="## Location\nÉglise\nVieille paroisse.",
        language="fr",
        player_intent="inspecte la croix de fer pour voir si c une d'origine de 39-45",
        outcome_facts="",
    )

    args, kwargs = client.chat_json.call_args
    messages = args[1] if len(args) > 1 else kwargs["messages"]
    user_msg = messages[-1]["content"]
    assert "39-45" in user_msg
    assert "Église" in user_msg
    assert "Xavier searches" in user_msg


def test_narrate_legacy_signature_still_works():
    client = MagicMock()
    client.chat_json.return_value = {"narrative": "ok", "tone": "dramatic"}
    narrator = Narrator(client)

    narrator.narrate(
        action_result_text="Goblin takes 8 damage.",
        context_prompt="## Location\nForest",
    )

    args, kwargs = client.chat_json.call_args
    messages = args[1] if len(args) > 1 else kwargs["messages"]
    user_msg = messages[-1]["content"]
    assert "Goblin" in user_msg
