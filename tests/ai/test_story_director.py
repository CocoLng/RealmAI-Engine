"""Tests for the Story Director module."""

import pytest
from pytest_httpx import HTTPXMock
from unittest.mock import MagicMock

from ai.client import OllamaClient
from ai.models import DirectorNote
from ai.story_director import StoryDirector
from memory.semantic import SemanticMemory
from tests.ai.conftest import make_ollama_response

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"


@pytest.fixture
def client() -> OllamaClient:
    return OllamaClient()


@pytest.fixture
def mock_semantic() -> MagicMock:
    return MagicMock(spec=SemanticMemory)


@pytest.fixture
def director(client: OllamaClient, mock_semantic: MagicMock) -> StoryDirector:
    return StoryDirector(client, mock_semantic)


def test_check_coherence_returns_director_note(
    httpx_mock: HTTPXMock, director: StoryDirector
) -> None:
    """StoryDirector.check_coherence() returns a valid DirectorNote."""
    response_data = {
        "coherence_issues": ["Quest giver NPC was mentioned as dead but still appears"],
        "suggested_hooks": ["The merchant's revenge plot"],
        "priority": "high",
    }
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    result = director.check_coherence(
        campaign_id="camp-123",
        context_prompt="## Game State\nLocation: Tavern\n## Quests\nFind the merchant",
    )

    assert isinstance(result, DirectorNote)
    assert result.priority == "high"
    assert len(result.coherence_issues) == 1
    assert len(result.suggested_hooks) == 1


def test_check_coherence_stores_semantic_document(
    httpx_mock: HTTPXMock,
    director: StoryDirector,
    mock_semantic: MagicMock,
) -> None:
    """check_coherence() stores the DirectorNote as a SemanticDocument."""
    response_data = {
        "coherence_issues": [],
        "suggested_hooks": ["Explore the old mine"],
        "priority": "low",
    }
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    director.check_coherence(
        campaign_id="camp-456",
        context_prompt="Everything is fine.",
    )

    mock_semantic.add_documents.assert_called_once()
    call_args = mock_semantic.add_documents.call_args
    documents = call_args[0][0]  # first positional argument
    assert len(documents) == 1
    assert documents[0].campaign_id == "camp-456"


def test_check_coherence_low_priority(
    httpx_mock: HTTPXMock, director: StoryDirector
) -> None:
    """Director returns low priority when story is coherent."""
    response_data = {
        "coherence_issues": [],
        "suggested_hooks": ["An old legend about the forest"],
        "priority": "low",
    }
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    result = director.check_coherence(
        campaign_id="camp-789",
        context_prompt="Story is coherent.",
    )

    assert result.priority == "low"
    assert result.coherence_issues == []
