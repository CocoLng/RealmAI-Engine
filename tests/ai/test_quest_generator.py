"""Tests for the Quest Generator module."""

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.quest_generator import QuestGenerator
from tests.ai.conftest import CHAT_URL, make_ollama_response
from world.quest import Quest, QuestStatus


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
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="Wilderness.",
        location_name="Ancient Ruins",
        available_npcs=[],
    )

    assert result.title == "Explore the Dungeon"
    assert result.reward_gold == 0


def test_generate_with_quest_hint(httpx_mock: HTTPXMock, generator: QuestGenerator) -> None:
    """When quest_hint is provided, it is included in the prompt."""
    response_data = {
        "title": "The Haunted Mine",
        "description": "Strange noises echo from the old mine.",
        "objectives": [{"description": "Investigate the mine", "is_completed": False}],
        "reward_xp": 200,
        "reward_gold": 30,
        "giver_npc": "Old Miner",
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="A mining village with rumors of ghosts.",
        location_name="Ironvale",
        available_npcs=["Old Miner"],
        quest_hint="The mine collapse was no accident — sabotage by rival faction.",
    )

    assert isinstance(result, Quest)
    assert result.title == "The Haunted Mine"

    # Verify hint was included in the prompt
    request = httpx_mock.get_request(url=CHAT_URL)
    assert request is not None
    import json
    body = json.loads(request.content)
    user_msg = body["messages"][-1]["content"]
    assert "Quest context hint:" in user_msg
    assert "sabotage by rival faction" in user_msg
