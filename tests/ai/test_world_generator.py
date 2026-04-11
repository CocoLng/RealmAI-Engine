"""Tests for the World Generator module."""

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.world_generator import WorldGenerator
from tests.ai.conftest import CHAT_URL, make_ollama_response
from world.location import Location


@pytest.fixture
def generator(ollama_client: OllamaClient) -> WorldGenerator:
    return WorldGenerator(ollama_client)


def test_generate_returns_location_with_name_and_description(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """WorldGenerator.generate() returns a valid Location with name and description."""
    response_data = {
        "name": "The Broken Crown Tavern",
        "description": (
            "A ramshackle inn perched at the edge of the village square. "
            "Smoke-stained rafters hang low above tables carved with the names of long-dead adventurers. "
            "The smell of stale ale and roasting meat fills the air."
        ),
        "connections": ["Market Square", "North Road", "Stables"],
        "npcs_present": ["Marta the Innkeeper", "Old Gruff"],
        "items_available": ["Healing Potion", "Traveler's Rations"],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="A quiet border village on the edge of the Ashwood.",
        location_type="tavern",
    )

    assert isinstance(result, Location)
    assert result.name == "The Broken Crown Tavern"
    assert "ramshackle inn" in result.description


def test_generate_connections_populated(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """WorldGenerator.generate() returns a Location with populated connections list."""
    response_data = {
        "name": "The Ashwood Gate",
        "description": "A crumbling stone arch marks the entrance to the dark Ashwood forest.",
        "connections": ["Village Square", "Forest Path", "Watchtower"],
        "npcs_present": ["Guard Sergeant Bram"],
        "items_available": [],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="A frontier settlement bordering a haunted forest.",
        location_type="gate",
    )

    assert isinstance(result, Location)
    assert len(result.connections) == 3
    assert "Village Square" in result.connections
    assert "Forest Path" in result.connections
    assert "Watchtower" in result.connections


def test_generate_empty_npcs_and_items(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """WorldGenerator.generate() works when npcs_present and items_available are empty."""
    response_data = {
        "name": "The Forgotten Tomb",
        "description": (
            "A moss-covered sarcophagus dominates the center of this ancient burial chamber. "
            "Cobwebs drape every corner and the air tastes of centuries of dust."
        ),
        "connections": ["Dungeon Entrance"],
        "npcs_present": [],
        "items_available": [],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="An ancient dungeon beneath the ruins of a fallen kingdom.",
        location_type="tomb",
    )

    assert isinstance(result, Location)
    assert result.name == "The Forgotten Tomb"
    assert result.npcs_present == []
    assert result.items_available == []
    assert result.connections == ["Dungeon Entrance"]


def test_generate_with_optional_location_name(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """WorldGenerator.generate() accepts an optional location_name hint."""
    response_data = {
        "name": "The Silver Serpent Inn",
        "description": "A prosperous inn with a silver serpent carved above the door.",
        "connections": ["Harbor District", "Temple Row"],
        "npcs_present": [],
        "items_available": ["Room Key"],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="A bustling port city.",
        location_type="inn",
        location_name="Silver Serpent Inn",
    )

    assert isinstance(result, Location)
    assert result.name == "The Silver Serpent Inn"
    assert "Room Key" in result.items_available


def test_generate_populates_item_descriptions(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """LLM-provided item_descriptions are forwarded to the Location."""
    response_data = {
        "name": "La Paroisse de Saint-Michel",
        "description": "Une vieille église paroissiale.",
        "connections": ["Village"],
        "npcs_present": [],
        "items_available": ["Croix de fer", "Cierge pourri"],
        "item_descriptions": {
            "Croix de fer": "Vieille croix de forge médiévale, noircie par les ans.",
            "Cierge pourri": "Un cierge consumé, la cire jaunie et craquelée.",
        },
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="Un village médiéval menacé par une corruption ancienne.",
        location_type="starting_area",
    )

    assert result.item_descriptions["Croix de fer"].startswith("Vieille croix de forge")
    assert "cire jaunie" in result.item_descriptions["Cierge pourri"]


def test_generate_drops_descriptions_for_unknown_items(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """Descriptions whose key is NOT in items_available are dropped (anti-leak)."""
    response_data = {
        "name": "La Crypte",
        "description": "...",
        "connections": [],
        "npcs_present": [],
        "items_available": ["Croix de fer"],
        "item_descriptions": {
            "Croix de fer": "Vieille croix de forge.",
            "Épée d'or": "Une lame légendaire qui n'existe pas dans la scène.",
        },
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(campaign_context="...", location_type="crypt")

    assert "Croix de fer" in result.item_descriptions
    assert "Épée d'or" not in result.item_descriptions


def test_generate_missing_item_descriptions_defaults_empty(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """Backward compat: LLM responses without item_descriptions still work."""
    response_data = {
        "name": "Le Vieux Pont",
        "description": "Un pont de pierre.",
        "connections": [],
        "npcs_present": [],
        "items_available": ["Lanterne"],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(campaign_context="...", location_type="bridge")

    assert result.item_descriptions == {}


def test_generate_with_atmosphere_hint(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """generate() includes atmosphere hint in the user message."""
    response_data = {
        "name": "The Shadowed Alley",
        "description": "A narrow passage between crumbling buildings.",
        "connections": ["Market Square"],
        "npcs_present": [],
        "items_available": [],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="A decaying city.",
        location_type="alley",
        atmosphere="oppressive, claustrophobic",
    )

    assert isinstance(result, Location)
    assert result.name == "The Shadowed Alley"


def test_generate_with_beat_context(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """generate() includes beat_context in the user message."""
    response_data = {
        "name": "The War Room",
        "description": "A fortified chamber with battle maps.",
        "connections": ["Castle Hallway"],
        "npcs_present": ["General Voss"],
        "items_available": [],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="A kingdom at war.",
        location_type="war_room",
        beat_context="The heroes must convince the general to commit troops.",
    )

    assert isinstance(result, Location)
    assert "General Voss" in result.npcs_present


def test_generate_with_npc_count_hint(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """generate() includes npc_count_hint in the user message."""
    response_data = {
        "name": "The Bustling Bazaar",
        "description": "A colorful open-air market.",
        "connections": ["City Gate"],
        "npcs_present": ["Merchant Kira", "Spy Dalen", "Guard Halm"],
        "items_available": [],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="A desert trading hub.",
        location_type="market",
        npc_count_hint=3,
    )

    assert isinstance(result, Location)
    assert len(result.npcs_present) >= 3


def test_build_user_message_includes_all_hints(
    generator: WorldGenerator,
) -> None:
    """_build_user_message includes all optional hints when provided."""
    msg = generator._build_user_message(
        campaign_context="Context here.",
        location_type="tavern",
        location_name="The Red Lion",
        location_hints=["Market Square", "Docks"],
        atmosphere="cozy but tense",
        beat_context="A secret meeting takes place.",
        npc_count_hint=2,
    )

    assert "Context here." in msg
    assert "Location type: tavern" in msg
    assert "Suggested name: The Red Lion" in msg
    assert "Market Square, Docks" in msg
    assert "Atmosphere suggestion: cozy but tense" in msg
    assert "Story context for this location: A secret meeting takes place." in msg
    assert "at least 2 NPCs" in msg
