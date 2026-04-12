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


# ---------------------------------------------------------------------------
# Exit aliases (new in the exploration/movement fix)
# ---------------------------------------------------------------------------


def test_generate_populates_exit_aliases(
    httpx_mock: HTTPXMock, generator: WorldGenerator,
) -> None:
    """LLM-provided exit_aliases are forwarded to the Location."""
    response_data = {
        "name": "Salle des échos",
        "description": "Une salle circulaire.",
        "connections": ["Couloir du nord", "Sortie sud"],
        "exit_aliases": {
            "Couloir du nord": ["nord", "couloir", "corridor"],
            "Sortie sud": ["sud", "sortir", "dehors"],
        },
        "npcs_present": [],
        "items_available": [],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="A haunted ruin.",
        location_type="hall",
    )

    assert result.exit_aliases == {
        "Couloir du nord": ["nord", "couloir", "corridor"],
        "Sortie sud": ["sud", "sortir", "dehors"],
    }


def test_generate_drops_exit_aliases_for_unknown_connections(
    httpx_mock: HTTPXMock, generator: WorldGenerator,
) -> None:
    """Aliases whose key is NOT in connections are dropped (anti-leak)."""
    response_data = {
        "name": "La Crypte",
        "description": "...",
        "connections": ["Entrée"],
        "exit_aliases": {
            "Entrée": ["porte", "entrée"],
            "Passage secret": ["secret", "caché"],  # not in connections
        },
        "npcs_present": [],
        "items_available": [],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(campaign_context="...", location_type="crypt")

    assert "Entrée" in result.exit_aliases
    assert "Passage secret" not in result.exit_aliases


def test_generate_missing_exit_aliases_defaults_empty(
    httpx_mock: HTTPXMock, generator: WorldGenerator,
) -> None:
    """Backward compat: LLM responses without exit_aliases still work."""
    response_data = {
        "name": "Le Vieux Pont",
        "description": "Un pont de pierre.",
        "connections": ["Village"],
        "npcs_present": [],
        "items_available": [],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(campaign_context="...", location_type="bridge")

    assert result.exit_aliases == {}


def test_generate_required_connections_force_injected_if_missing(
    httpx_mock: HTTPXMock, generator: WorldGenerator,
) -> None:
    """Safety net: if the LLM forgets a required connection, we force-inject it."""
    response_data = {
        "name": "Nouveau lieu",
        "description": "Une place.",
        "connections": ["Autre endroit"],  # LLM forgot the required back-link
        "exit_aliases": {},
        "npcs_present": [],
        "items_available": [],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="...",
        location_type="connected_area",
        required_connections=["Parent Village"],
    )

    assert "Parent Village" in result.connections
    assert "Autre endroit" in result.connections


def test_build_user_message_includes_required_connections(
    generator: WorldGenerator,
) -> None:
    """_build_user_message surfaces required_connections to the LLM."""
    msg = generator._build_user_message(
        campaign_context="Context.",
        location_type="area",
        location_name=None,
        required_connections=["Parent Village", "Old Well"],
    )

    assert "Required connections to preserve" in msg
    assert "Parent Village" in msg
    assert "Old Well" in msg


# ---------------------------------------------------------------------------
# Combat zones & triggers
# ---------------------------------------------------------------------------


def test_parses_combat_zones_from_json(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """combat_zones in the LLM output are parsed onto the Location."""
    response_data = {
        "name": "Temple oublié",
        "description": "Un sanctuaire abandonné.",
        "connections": [],
        "npcs_present": [],
        "items_available": [],
        "combat_zones": [
            {
                "name": "Autel central",
                "description": "Une estrade de pierre.",
                "adjacent_zone_names": ["Alcôve sud"],
                "tags": ["elevated"],
            },
            {
                "name": "Alcôve sud",
                "description": "Un renfoncement poussiéreux.",
                "adjacent_zone_names": ["Autel central"],
                "tags": ["obscured"],
            },
        ],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="Ancien ordre disparu.",
        location_type="temple",
    )

    assert len(result.combat_zones) == 2
    assert result.has_combat_zones()
    assert result.get_zone("Autel central") is not None
    assert result.are_adjacent("Autel central", "Alcôve sud")


def test_drops_invalid_zones_with_asymmetric_adjacency(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """Asymmetric zone graphs trigger a fallback to empty combat_zones."""
    response_data = {
        "name": "Grotte brisée",
        "description": "Une grotte humide.",
        "connections": [],
        "npcs_present": [],
        "items_available": [],
        "combat_zones": [
            {
                "name": "Entrée",
                "adjacent_zone_names": ["Fond"],
                "tags": [],
            },
            {
                # Asymmetric: does NOT list "Entrée" back
                "name": "Fond",
                "adjacent_zone_names": [],
                "tags": [],
            },
        ],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="Donjon.",
        location_type="cave",
    )

    # Parser catches the Location ValidationError and drops combat_zones.
    assert result.combat_zones == []
    assert not result.has_combat_zones()


def test_parses_combat_triggers_from_json(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """combat_triggers in the LLM output are parsed onto the Location."""
    response_data = {
        "name": "Salle des urnes",
        "description": "Une salle rituelle.",
        "connections": [],
        "npcs_present": [],
        "items_available": ["Urne scellée"],
        "combat_triggers": {
            "Urne scellée": {
                "spawn_npcs": ["Spectre affamé"],
                "reveal_narration": "L'urne se brise et un spectre en jaillit.",
            }
        },
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="Crypte maudite.",
        location_type="crypt",
    )

    assert "Urne scellée" in result.combat_triggers
    trigger = result.combat_triggers["Urne scellée"]
    assert trigger.item_name == "Urne scellée"
    assert "Spectre affamé" in trigger.spawn_npcs
    assert "spectre" in trigger.reveal_narration.lower()
    assert trigger.consumed is False


def test_empty_zones_and_triggers_accepted(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """A peaceful tavern can emit combat_zones=[] and combat_triggers={}."""
    response_data = {
        "name": "Taverne paisible",
        "description": "Un feu crépite dans l'âtre.",
        "connections": ["Rue principale"],
        "npcs_present": ["Aubergiste"],
        "items_available": [],
        "combat_zones": [],
        "combat_triggers": {},
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="Village tranquille.",
        location_type="tavern",
    )

    assert result.combat_zones == []
    assert result.combat_triggers == {}
    assert not result.has_combat_zones()


def test_zone_validation_error_does_not_crash_generation(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """A zone with an unknown adjacent neighbor is dropped silently."""
    response_data = {
        "name": "Chambre",
        "description": "Une chambre.",
        "connections": [],
        "npcs_present": [],
        "items_available": [],
        "combat_zones": [
            {
                "name": "Centre",
                # References a zone that does not exist → Location validator
                # will reject the whole graph; parser falls back to empty.
                "adjacent_zone_names": ["Inexistante"],
                "tags": [],
            },
        ],
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    # Should not raise; instead drops combat_zones and returns a usable
    # Location with the rest of the fields.
    result = generator.generate(
        campaign_context="Manoir.",
        location_type="room",
    )

    assert isinstance(result, Location)
    assert result.name == "Chambre"
    assert result.combat_zones == []


def test_invalid_trigger_entry_dropped_silently(
    httpx_mock: HTTPXMock, generator: WorldGenerator
) -> None:
    """A trigger whose payload is not a dict is dropped with a warning."""
    response_data = {
        "name": "Place",
        "description": "Une place.",
        "connections": [],
        "npcs_present": [],
        "items_available": [],
        "combat_triggers": {
            "Urne": "not a dict",
            "Sceau": {
                "spawn_npcs": ["Ombre"],
                "reveal_narration": "Le sceau se brise.",
            },
        },
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = generator.generate(
        campaign_context="Donjon.",
        location_type="room",
    )

    assert "Urne" not in result.combat_triggers
    assert "Sceau" in result.combat_triggers
