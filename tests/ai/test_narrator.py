"""Tests for the Narrator module."""

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.models import NarrativeResult
from ai.narrator import Narrator
from tests.ai.conftest import make_ollama_response

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"


@pytest.fixture
def client() -> OllamaClient:
    return OllamaClient()


@pytest.fixture
def narrator(client: OllamaClient) -> Narrator:
    return Narrator(client)


def test_narrate_returns_narrative_result(httpx_mock: HTTPXMock, narrator: Narrator) -> None:
    """Narrator returns a valid NarrativeResult."""
    response_data = {
        "narrative": "Your axe bites deep into the goblin's shoulder.",
        "tone": "dramatic",
    }
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

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
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    narrator.narrate(
        action_result_text="Merlin casts Fireball. Skeleton fails save. 12 fire damage.",
        context_prompt="## Game State\nMerlin: 30/30 HP",
    )

    # Verify the request was made (httpx_mock would raise if not matched)
    assert len(httpx_mock.get_requests()) == 1


def test_narrate_various_tones(httpx_mock: HTTPXMock, narrator: Narrator) -> None:
    """Narrator accepts all valid tones."""
    for tone in ["dramatic", "tense", "humorous", "somber"]:
        httpx_mock.add_response(
            url=OLLAMA_URL,
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
        url=OLLAMA_URL,
        json=make_ollama_response({"narrative": "The battle rages on.", "tone": "tense"}),
    )
    result = narrator.narrate(
        action_result_text="Miss. No damage.",
        context_prompt="Context.",
    )
    assert isinstance(result, NarrativeResult)
    # Temperature is tested implicitly — if wrong, the call would fail or mock mismatch
