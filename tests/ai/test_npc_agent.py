"""Tests for the NPC Agent module."""

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.models import NPCResponse
from ai.npc_agent import NPCAgent
from tests.ai.conftest import make_ollama_response
from world.npc import NPC, NPCDisposition
from engine.character import AbilityScores, Race

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"


@pytest.fixture
def client() -> OllamaClient:
    return OllamaClient()


@pytest.fixture
def npc_agent(client: OllamaClient) -> NPCAgent:
    return NPCAgent(client)


@pytest.fixture
def sample_npc() -> NPC:
    return NPC(
        name="Gareth the Innkeeper",
        race=Race.HUMAN,
        ability_scores=AbilityScores(
            STR=10, DEX=10, CON=10,
            INT=12, WIS=14, CHA=16,
        ),
        hp=12,
        max_hp=12,
        ac=10,
        disposition=NPCDisposition.FRIENDLY,
        description="A jovial innkeeper with a red beard.",
        personality="Friendly and talkative. Loves gossip. Dislikes violence.",
        location_name="The Rusty Flagon Inn",
    )


def test_respond_returns_npc_response(
    httpx_mock: HTTPXMock, npc_agent: NPCAgent, sample_npc: NPC
) -> None:
    """NPCAgent.respond() returns a valid NPCResponse."""
    response_data = {
        "dialogue": "Ah, welcome traveler! What brings you to my inn?",
        "disposition_change": 1,
        "revealed_info": ["The merchant left yesterday"],
    }
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    result = npc_agent.respond(
        npc=sample_npc,
        player_input="Hello, innkeeper!",
        context_prompt="## Game State\nLocation: The Rusty Flagon Inn",
    )

    assert isinstance(result, NPCResponse)
    assert result.dialogue == "Ah, welcome traveler! What brings you to my inn?"
    assert result.disposition_change == 1
    assert "The merchant left yesterday" in result.revealed_info


def test_respond_does_not_mutate_npc(
    httpx_mock: HTTPXMock, npc_agent: NPCAgent, sample_npc: NPC
) -> None:
    """NPCAgent.respond() never mutates the NPC object."""
    original_disposition = sample_npc.disposition
    response_data = {
        "dialogue": "Get out of my sight!",
        "disposition_change": -2,
        "revealed_info": [],
    }
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    result = npc_agent.respond(
        npc=sample_npc,
        player_input="I insult the innkeeper",
        context_prompt="Context.",
    )

    # NPC object must NOT be mutated — disposition_change is only a signal
    assert sample_npc.disposition == original_disposition
    assert result.disposition_change == -2


def test_respond_with_empty_revealed_info(
    httpx_mock: HTTPXMock, npc_agent: NPCAgent, sample_npc: NPC
) -> None:
    """NPCAgent handles empty revealed_info correctly."""
    response_data = {
        "dialogue": "I have nothing to say.",
        "disposition_change": 0,
        "revealed_info": [],
    }
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(response_data))

    result = npc_agent.respond(
        npc=sample_npc,
        player_input="Any news?",
        context_prompt="Context.",
    )

    assert result.revealed_info == []
    assert result.disposition_change == 0
