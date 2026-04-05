"""Tests for the Quest Generator module."""

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.quest_generator import QuestGenerator
from tests.ai.conftest import make_ollama_response
from world.quest import Quest, QuestStatus

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"


@pytest.fixture
def client() -> OllamaClient:
    return OllamaClient()


@pytest.fixture
def generator(client: OllamaClient) -> QuestGenerator:
    return QuestGenerator(client)


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
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

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
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

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
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="Wilderness.",
        location_name="Ancient Ruins",
        available_npcs=[],
    )

    assert result.title == "Explore the Dungeon"
    assert result.reward_gold == 0
