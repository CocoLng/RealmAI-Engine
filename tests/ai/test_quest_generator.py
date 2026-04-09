"""Tests for the Quest Generator module."""

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.quest_generator import QuestGenerator
from tests.ai.conftest import CHAT_URL, make_ollama_response
from world.quest import Quest, QuestStatus


def _make_brainstorm_response() -> dict:
    """Build a valid brainstorm response."""
    return {
        "options": [
            {
                "concept": "A missing merchant needs to be found",
                "key_elements": ["investigation", "Captain Aldric is concerned", "bandits"],
                "risk": "Might be too linear",
                "selected": True,
            },
            {
                "concept": "A monster threatens the trade route",
                "key_elements": ["combat focus", "escorting caravans", "ogre lair"],
                "risk": "Too combat-heavy for early game",
                "selected": False,
            },
            {
                "concept": "A mysterious artifact is discovered",
                "key_elements": ["dungeon crawl", "ancient temple", "cursed item"],
                "risk": "Disconnected from NPCs",
                "selected": False,
            },
        ]
    }


@pytest.fixture
def generator(ollama_client: OllamaClient) -> QuestGenerator:
    return QuestGenerator(ollama_client)


def test_generate_returns_quest(httpx_mock: HTTPXMock, generator: QuestGenerator) -> None:
    """QuestGenerator.generate() returns a valid Quest."""
    response_data = {
        "title": "The Missing Merchant",
        "description": "A local merchant has gone missing. Find him.",
        "objectives": [
            {"description": "Investigate the merchant's last known location", "is_completed": False},
            {"description": "Find the merchant or evidence of his fate", "is_completed": False},
        ],
        "reward_xp": 300,
        "reward_gold": 50,
        "giver_npc": "Captain Aldric",
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(_make_brainstorm_response()))
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="A small trading town. Caravans often pass through.",
        location_name="Millhaven",
        available_npcs=["Captain Aldric", "Gareth the Innkeeper"],
    )

    assert isinstance(result, Quest)
    assert result.title == "The Missing Merchant"
    assert result.status == QuestStatus.AVAILABLE
    assert len(result.objectives) == 2
    assert result.reward_xp == 300
    assert result.reward_gold == 50
    assert result.giver_npc == "Captain Aldric"
    assert all(not obj.is_complete for obj in result.objectives)


def test_generate_status_always_available(
    httpx_mock: HTTPXMock, generator: QuestGenerator
) -> None:
    """Generated quests always have AVAILABLE status."""
    response_data = {
        "title": "Slay the Dragon",
        "description": "There is a dragon.",
        "objectives": [{"description": "Kill the dragon", "is_completed": False}],
        "reward_xp": 2000,
        "reward_gold": 500,
        "giver_npc": None,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(_make_brainstorm_response()))
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="Epic campaign.",
        location_name="Dragon Mountain",
        available_npcs=[],
    )

    assert result.status == QuestStatus.AVAILABLE
    assert result.giver_npc is None


def test_generate_with_no_npcs(httpx_mock: HTTPXMock, generator: QuestGenerator) -> None:
    """QuestGenerator works with an empty NPC list."""
    response_data = {
        "title": "Explore the Dungeon",
        "description": "Darkness calls.",
        "objectives": [{"description": "Enter the dungeon", "is_completed": False}],
        "reward_xp": 100,
        "reward_gold": 0,
        "giver_npc": None,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(_make_brainstorm_response()))
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="Wilderness.",
        location_name="Ancient Ruins",
        available_npcs=[],
    )

    assert result.title == "Explore the Dungeon"
    assert result.reward_gold == 0


def test_generate_falls_back_on_brainstorm_failure(
    httpx_mock: HTTPXMock, generator: QuestGenerator
) -> None:
    """If brainstorm fails, generate() still works with a single call."""
    response_data = {
        "title": "Fallback Quest",
        "description": "A quest that works.",
        "objectives": [{"description": "Do the thing", "is_completed": False}],
        "reward_xp": 100,
        "reward_gold": 10,
        "giver_npc": None,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response("not json"))
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="...",
        location_name="Anywhere",
        available_npcs=[],
    )

    assert isinstance(result, Quest)
    assert result.title == "Fallback Quest"
