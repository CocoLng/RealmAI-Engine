"""Integration-style tests for lethal intent detection (Task 40).

Each test feeds a canonical phrase through the Interpreter with a mocked
Ollama response that mirrors the kind of output the prompt is expected to
produce. These tests guard the *parsing contract* between the prompt and
the downstream pipeline, not the raw LLM intelligence.
"""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.interpreter import Interpreter
from ai.scene_context import SceneContext
from engine.validators import ActionType
from tests.ai.conftest import CHAT_URL, make_ollama_response


@pytest.fixture
def interpreter(ollama_client: OllamaClient) -> Interpreter:
    return Interpreter(ollama_client)


@pytest.fixture
def market_scene() -> SceneContext:
    return SceneContext(
        location_name="Marché de Mageta",
        location_description="Une place commerçante animée.",
        visible_npcs=["Vellus", "Marchand Korr", "Garde Thal"],
        visible_exits=["Ruelle est"],
        visible_objects=["Étal de poissons", "Porte de bois"],
    )


@pytest.fixture
def ambush_scene() -> SceneContext:
    return SceneContext(
        location_name="Ravin aride",
        location_description="Un passage rocheux sec.",
        enemies_visible=["bandits"],
    )


# ---------------------------------------------------------------------------
# Positive cases — lethal intent MUST be flagged
# ---------------------------------------------------------------------------


def test_interpreter_detects_lethal_sword_charge(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    market_scene: SceneContext,
) -> None:
    """'Je sors mon épée et je charge Vellus' → lethal intent True."""
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "action_type": "Improvise",
                "actor_name": "Aldric",
                "target_name": "Vellus",
                "improvise_description": "charge le marchand épée au clair",
                "is_lethal_intent": True,
                "confidence": 0.9,
            }
        ),
    )

    result = interpreter.interpret(
        player_text="je sors mon épée et je charge Vellus",
        actor_name="Aldric",
        scene_context=market_scene,
    )

    assert result.is_lethal_intent is True
    assert result.target_name == "Vellus"


def test_interpreter_detects_lethal_stab(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    market_scene: SceneContext,
) -> None:
    """'Je poignarde Vellus dans le dos' → lethal intent True."""
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "action_type": "Attack",
                "actor_name": "Aldric",
                "target_name": "Vellus",
                "weapon_name": "dague",
                "is_lethal_intent": True,
                "confidence": 0.95,
            }
        ),
    )

    result = interpreter.interpret(
        player_text="je poignarde Vellus dans le dos",
        actor_name="Aldric",
        scene_context=market_scene,
    )

    assert result.is_lethal_intent is True
    assert result.action_type == ActionType.ATTACK
    assert result.target_name == "Vellus"


def test_interpreter_detects_lethal_spell(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    ambush_scene: SceneContext,
) -> None:
    """'Je lance une boule de feu sur les bandits' → lethal intent True."""
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "action_type": "Cast Spell",
                "actor_name": "Merlin",
                "target_name": "bandits",
                "spell_name": "Boule de feu",
                "is_lethal_intent": True,
                "confidence": 0.9,
            }
        ),
    )

    result = interpreter.interpret(
        player_text="je lance une boule de feu sur les bandits",
        actor_name="Merlin",
        scene_context=ambush_scene,
    )

    assert result.is_lethal_intent is True
    assert result.spell_name == "Boule de feu"
    assert result.target_name == "bandits"


# ---------------------------------------------------------------------------
# Negative cases — lethal intent MUST NOT be flagged
# ---------------------------------------------------------------------------


def test_interpreter_rejects_threat_as_talk(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    market_scene: SceneContext,
) -> None:
    """'Je menace le garde avec mon arme' → Talk, not lethal."""
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "action_type": "Talk",
                "actor_name": "Aldric",
                "target_name": "Garde Thal",
                "talk_topic": "intimidation",
                "is_lethal_intent": False,
                "confidence": 0.8,
            }
        ),
    )

    result = interpreter.interpret(
        player_text="je menace le garde avec mon arme",
        actor_name="Aldric",
        scene_context=market_scene,
    )

    assert result.is_lethal_intent is False
    assert result.action_type == ActionType.TALK


def test_interpreter_rejects_object_target(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    market_scene: SceneContext,
) -> None:
    """'J'attaque la porte' → object, not a creature, lethal = False."""
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "action_type": "Interact",
                "actor_name": "Aldric",
                "target_name": "Porte de bois",
                "is_lethal_intent": False,
                "confidence": 0.7,
            }
        ),
    )

    result = interpreter.interpret(
        player_text="j'attaque la porte",
        actor_name="Aldric",
        scene_context=market_scene,
    )

    assert result.is_lethal_intent is False


def test_interpreter_rejects_future_intent(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    market_scene: SceneContext,
) -> None:
    """'Je vais chercher le dragon pour le combattre' → future, lethal = False."""
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {
                "action_type": "Improvise",
                "actor_name": "Aldric",
                "improvise_description": "part à la recherche du dragon",
                "is_lethal_intent": False,
                "confidence": 0.6,
            }
        ),
    )

    result = interpreter.interpret(
        player_text="je vais chercher le dragon pour le combattre",
        actor_name="Aldric",
        scene_context=market_scene,
    )

    assert result.is_lethal_intent is False
