"""Tests for the Arc Generator module."""

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from ai.arc_generator import ArcGenerator
from ai.client import OllamaClient
from engine.arc_recipes import ArcRecipe, Archetype, BeatType, Tone, VillainArchetype
from tests.ai.conftest import CHAT_URL, make_ollama_response
from world.story_arc import StoryArc


def _make_arc_data(beat_count: int = 10) -> dict:
    """Build a valid arc JSON dict with the given number of beats."""
    encounter_types = ["social", "exploration", "combat", "puzzle"]
    beats = []
    for i in range(1, beat_count + 1):
        is_last = i == beat_count
        beats.append({
            "beat_number": i,
            "title": f"Acte {i}",
            "description": f"Description du beat {i}. Une aventure se dessine. Les heros avancent.",
            "location_hint": f"Lieu {i}",
            "npc_names": [f"PNJ_{i}"] if i % 2 == 0 else [],
            "encounter_type": "boss" if is_last else encounter_types[i % len(encounter_types)],
            "is_twist": i == 7,
        })
    return {
        "campaign_id": "",
        "theme": "dark fantasy",
        "premise": "Un ancien mal se reveille dans les profondeurs de la terre, menacant le royaume.",
        "beats": beats,
        "villain_name": "Seigneur Malachar",
        "villain_motivation": "Dominer le monde en liberant une armee de morts-vivants.",
    }


def _make_recipe() -> ArcRecipe:
    """Build a valid ArcRecipe for testing."""
    return ArcRecipe(
        archetype=Archetype.mystery,
        beat_sequence=[
            BeatType.exploration, BeatType.social, BeatType.puzzle,
            BeatType.social, BeatType.exploration, BeatType.puzzle,
            BeatType.combat, BeatType.exploration, BeatType.social,
            BeatType.boss,
        ],
        beat_subtypes=[
            "tracking", "negotiation", "riddle",
            "interrogation", "infiltration", "investigation",
            "ambush", "discovery", "ceremony",
            "boss",
        ],
        complications=["Trahison d'un allié", "Course contre la montre"],
        tone=Tone.mysterieux,
        twist_position=5,
        num_beats=10,
        villain_archetype=VillainArchetype.manipulateur,
    )


@pytest.fixture
def generator(ollama_client: OllamaClient) -> ArcGenerator:
    return ArcGenerator(ollama_client)


def test_system_prompt_file_exists() -> None:
    """The system prompt file for the arc generator must exist."""
    prompt_path = Path(__file__).parent.parent.parent / "ai" / "prompts" / "system_arc_generator.txt"
    assert prompt_path.exists(), f"Missing system prompt: {prompt_path}"
    content = prompt_path.read_text()
    assert len(content) > 100, "System prompt seems too short"


def test_generate_returns_valid_story_arc(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """ArcGenerator.generate() returns a valid StoryArc with correct theme."""
    arc_data = _make_arc_data(10)
    # Single call (no recipe)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="dark fantasy", player_count=4)

    assert isinstance(result, StoryArc)
    assert result.theme == "dark fantasy"
    assert result.villain_name == "Seigneur Malachar"
    assert len(result.beats) == 10


def test_generate_with_recipe(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """ArcGenerator.generate() with a recipe uses single-call generation."""
    arc_data = _make_arc_data(10)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    recipe = _make_recipe()
    result = generator.generate(theme="dark fantasy", player_count=4, recipe=recipe)

    assert isinstance(result, StoryArc)
    assert result.theme == "dark fantasy"
    assert len(result.beats) == 10


def test_generate_beats_have_correct_structure(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """Each beat in the generated arc has the required fields."""
    arc_data = _make_arc_data(10)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="dark fantasy", player_count=3)

    for beat in result.beats:
        assert beat.beat_number >= 1
        assert len(beat.title) > 0
        assert len(beat.description) > 0
        assert beat.encounter_type in ("social", "combat", "exploration", "puzzle", "boss")


def test_generate_last_beat_is_boss(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """The final beat of the generated arc must be a boss encounter."""
    arc_data = _make_arc_data(12)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="pirate adventure", player_count=5)

    assert result.beats[-1].encounter_type == "boss"


def test_generate_contains_twist(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """The generated arc contains at least one twist beat."""
    arc_data = _make_arc_data(10)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="dark fantasy", player_count=4)

    twist_beats = [b for b in result.beats if b.is_twist]
    assert len(twist_beats) >= 1


def test_generate_current_beat_index_starts_at_zero(
    httpx_mock: HTTPXMock, generator: ArcGenerator
) -> None:
    """A freshly generated arc starts at beat index 0."""
    arc_data = _make_arc_data(10)
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(arc_data))

    result = generator.generate(theme="mystery", player_count=2)

    assert result.current_beat_index == 0


def test_build_user_message_with_recipe_contains_recipe_fields(
    generator: ArcGenerator,
) -> None:
    """The recipe-based user message includes archetype, tone, beats, etc."""
    recipe = _make_recipe()
    msg = generator._build_user_message_with_recipe("dark fantasy", 4, recipe)

    assert "dark fantasy" in msg
    assert "4" in msg
    assert "mystery" in msg  # archetype
    assert "mystérieux" in msg  # tone
    assert "manipulateur" in msg  # villain archetype
    assert "Trahison d'un allié" in msg  # complication
    assert "[TWIST]" in msg
    assert "Beat 1:" in msg
    assert f"Beat {recipe.num_beats}:" in msg


def test_build_user_message_with_recipe_no_villain(
    generator: ArcGenerator,
) -> None:
    """When villain_archetype is None, the message shows 'au choix'."""
    recipe = _make_recipe().model_copy(update={"villain_archetype": None})
    msg = generator._build_user_message_with_recipe("dark fantasy", 4, recipe)

    assert "au choix" in msg
