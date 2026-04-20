"""Tests for the Story Director module."""

import pytest
from pytest_httpx import HTTPXMock
from unittest.mock import MagicMock

from ai.client import OllamaClient
from ai.models import DirectorNote
from ai.story_director import StoryDirector
from memory.semantic import SemanticMemory
from tests.ai.conftest import CHAT_URL, make_ollama_response


def _make_brainstorm_response() -> dict:
    """Build a valid brainstorm response."""
    return {
        "options": [
            {
                "concept": "Quest giver NPC inconsistency",
                "key_elements": [
                    "NPC mentioned as dead but still appears",
                    "Quest log references outdated location",
                ],
                "risk": "Players will notice the contradiction",
                "selected": True,
            },
            {
                "concept": "Stale side quest",
                "key_elements": ["Old fetch quest never resolved", "NPC waiting forever"],
                "risk": "Immersion break",
                "selected": False,
            },
            {
                "concept": "Faction tension opportunity",
                "key_elements": ["Two factions both helped", "Conflict brewing"],
                "risk": "Could derail main plot",
                "selected": False,
            },
        ]
    }


@pytest.fixture
def mock_semantic() -> MagicMock:
    return MagicMock(spec=SemanticMemory)


@pytest.fixture
def director(ollama_client: OllamaClient, mock_semantic: MagicMock) -> StoryDirector:
    return StoryDirector(ollama_client, mock_semantic)


def test_check_coherence_returns_director_note(
    httpx_mock: HTTPXMock, director: StoryDirector
) -> None:
    """StoryDirector.check_coherence() returns a valid DirectorNote."""
    response_data = {
        "coherence_issues": ["Quest giver NPC was mentioned as dead but still appears"],
        "suggested_hooks": ["The merchant's revenge plot"],
        "priority": "high",
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(_make_brainstorm_response()))
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

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
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(_make_brainstorm_response()))
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    director.check_coherence(
        campaign_id="camp-456",
        context_prompt="Everything is fine.",
    )

    mock_semantic.add_documents.assert_called_once()
    call_args = mock_semantic.add_documents.call_args
    documents = call_args[0][0]  # first positional argument
    assert len(documents) == 1
    assert documents[0].campaign_id == "camp-456"
    from memory.models import SemanticDocumentType
    assert documents[0].doc_type == SemanticDocumentType.PAST_EVENT


def test_check_coherence_low_priority(
    httpx_mock: HTTPXMock, director: StoryDirector
) -> None:
    """Director returns low priority when story is coherent."""
    response_data = {
        "coherence_issues": [],
        "suggested_hooks": ["An old legend about the forest"],
        "priority": "low",
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(_make_brainstorm_response()))
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = director.check_coherence(
        campaign_id="camp-789",
        context_prompt="Story is coherent.",
    )

    assert result.priority == "low"
    assert result.coherence_issues == []


def test_check_coherence_falls_back_on_brainstorm_failure(
    httpx_mock: HTTPXMock, director: StoryDirector
) -> None:
    """If brainstorm fails, check_coherence() still works with a single call."""
    response_data = {
        "coherence_issues": [],
        "suggested_hooks": ["A fallback hook"],
        "priority": "low",
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response("not json"))
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = director.check_coherence(
        campaign_id="camp-fallback",
        context_prompt="Test context.",
    )

    assert isinstance(result, DirectorNote)
    assert result.priority == "low"


class TestStoryDirectorDirection:
    def test_check_coherence_parses_direction_fields(
        self,
        httpx_mock: HTTPXMock,
        director: StoryDirector,
    ) -> None:
        """check_coherence() parses and returns all direction fields from the LLM."""
        brainstorm_response = {"options": []}
        generate_response = {
            "coherence_issues": [],
            "suggested_hooks": ["Bring back Elena."],
            "priority": "medium",
            "current_objective": "Retrieve the dungeon map.",
            "next_beat_hint": "Encounter the spy at the well.",
            "forbidden_topics": ["map_in_cellar"],
            "required_mentions": ["Elena"],
            "stale_quest_ids": [],
        }
        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(brainstorm_response))
        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(generate_response))

        note = director.check_coherence(
            campaign_id="cmp_1",
            context_prompt="## Game State\nLast scene: the players reached the tavern.",
        )

        assert isinstance(note, DirectorNote)
        assert note.current_objective == "Retrieve the dungeon map."
        assert note.next_beat_hint == "Encounter the spy at the well."
        assert note.forbidden_topics == ["map_in_cellar"]
        assert note.required_mentions == ["Elena"]
        assert note.stale_quest_ids == []

    def test_check_coherence_direction_fields_default_when_absent(
        self,
        httpx_mock: HTTPXMock,
        director: StoryDirector,
    ) -> None:
        """check_coherence() defaults direction fields when LLM omits them."""
        brainstorm_response = {"options": []}
        generate_response = {
            "coherence_issues": [],
            "suggested_hooks": ["Old legend about the forest"],
            "priority": "low",
        }
        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(brainstorm_response))
        httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(generate_response))

        note = director.check_coherence(
            campaign_id="cmp_2",
            context_prompt="Everything is fine.",
        )

        assert note.current_objective == ""
        assert note.next_beat_hint == ""
        assert note.forbidden_topics == []
        assert note.required_mentions == []
        assert note.stale_quest_ids == []
