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
        "description": "…",
        "connections": [],
        "npcs_present": [],
        "items_available": ["Croix de fer"],
        "item_descriptions": {
            "Croix de fer": "Vieille croix de forge.",
            "Épée d'or": "Une lame légendaire qui n'existe pas dans la scène.",
        },
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(campaign_context="…", location_type="crypt")

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

    result = generator.generate(campaign_context="…", location_type="bridge")

    assert result.item_descriptions == {}
