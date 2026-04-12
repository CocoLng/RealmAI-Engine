"""Tests for the Interpreter module."""

import json

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient
from ai.interpreter import Interpreter
from ai.models import InterpretedAction
from ai.scene_context import SceneContext
from engine.validators import ActionType
from tests.ai.conftest import CHAT_URL, make_ollama_response


@pytest.fixture
def interpreter(ollama_client: OllamaClient) -> Interpreter:
    return Interpreter(ollama_client)


@pytest.fixture
def combat_scene() -> SceneContext:
    return SceneContext(
        location_name="Crypte",
        location_description="Une crypte sombre.",
        in_combat=True,
        combat_summary="Round 3, current turn: Thorin",
        enemies_visible=["Goblin", "Skeleton"],
    )


@pytest.fixture
def cathedral_scene() -> SceneContext:
    return SceneContext(
        location_name="Place de la Cathédrale",
        location_description="Une vaste place pavée devant la cathédrale.",
        visible_npcs=["Père Aldric", "Frère Corin"],
        visible_exits=["Intérieur de la cathédrale", "Ruelle nord"],
        visible_objects=["Autel de pierre", "Statue de saint"],
    )


# ---------------------------------------------------------------------------
# Combat actions
# ---------------------------------------------------------------------------


def test_interpret_attack_action(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    combat_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Attack",
        "actor_name": "Thorin",
        "target_name": "Goblin",
        "weapon_name": "axe",
        "spell_name": None,
        "item_name": None,
        "confidence": 0.95,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="I attack the goblin with my axe",
        actor_name="Thorin",
        scene_context=combat_scene,
    )

    assert isinstance(result, InterpretedAction)
    assert result.action_type == ActionType.ATTACK
    assert result.actor_name == "Thorin"
    assert result.target_name == "Goblin"
    assert result.weapon_name == "axe"
    assert result.confidence == 0.95
    assert result.raw_input == "I attack the goblin with my axe"


def test_interpret_cast_spell(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    combat_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Cast Spell",
        "actor_name": "Merlin",
        "target_name": "Skeleton",
        "weapon_name": None,
        "spell_name": "Fireball",
        "item_name": None,
        "confidence": 0.9,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="cast fireball on the skeleton",
        actor_name="Merlin",
        scene_context=combat_scene,
    )

    assert result.action_type == ActionType.CAST_SPELL
    assert result.spell_name == "Fireball"
    assert result.target_name == "Skeleton"


def test_interpret_use_item(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    combat_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Use Item",
        "actor_name": "Aria",
        "target_name": None,
        "weapon_name": None,
        "spell_name": None,
        "item_name": "Healing Potion",
        "confidence": 0.99,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="use my healing potion",
        actor_name="Aria",
        scene_context=combat_scene,
    )

    assert result.action_type == ActionType.USE_ITEM
    assert result.item_name == "Healing Potion"


def test_interpret_flee(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    combat_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Flee",
        "actor_name": "Thorin",
        "target_name": None,
        "weapon_name": None,
        "spell_name": None,
        "item_name": None,
        "confidence": 0.8,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="I run away",
        actor_name="Thorin",
        scene_context=combat_scene,
    )
    assert result.action_type == ActionType.FLEE


# ---------------------------------------------------------------------------
# Exploration actions
# ---------------------------------------------------------------------------


def test_interpret_look_action(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Look",
        "actor_name": "Aldric",
        "target_name": None,
        "confidence": 0.95,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="je regarde autour de moi",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )
    assert result.action_type == ActionType.LOOK


def test_interpret_talk_action(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Talk",
        "actor_name": "Aldric",
        "target_name": "Père Aldric",
        "talk_topic": "le rituel nocturne",
        "confidence": 0.9,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="je demande au prêtre Aldric ce qu'il sait sur le rituel nocturne",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )
    assert result.action_type == ActionType.TALK
    assert result.target_name == "Père Aldric"
    assert result.talk_topic == "le rituel nocturne"


def test_interpret_move_action(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Move",
        "actor_name": "Aldric",
        "target_name": "Intérieur de la cathédrale",
        "confidence": 0.95,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="j'entre dans la cathédrale",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )
    assert result.action_type == ActionType.MOVE
    assert result.target_name == "Intérieur de la cathédrale"


def test_interpret_move_recovers_missing_target_via_visible_exit(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    """Regression — observed 2026-04-11 in logs: the 4B model returned
    action=Move with target_name=None. The Python safety net must recover
    the destination by matching the raw input against visible_exits."""
    response_data = {
        "action_type": "Move",
        "actor_name": "Aldric",
        "target_name": None,  # <-- 4B model dropped it
        "confidence": 0.95,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="je vais dans l'intérieur de la cathédrale",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )
    assert result.action_type == ActionType.MOVE
    # The helper normalised the raw input and picked the canonical exit.
    assert result.target_name == "Intérieur de la cathédrale"


def test_interpret_move_recovery_returns_raw_text_when_no_match(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    """When the recovery helper cannot uniquely match any exit, it
    returns the raw player text so the downstream entity resolver can
    try again with the full alias set."""
    response_data = {
        "action_type": "Move",
        "actor_name": "Aldric",
        "target_name": None,
        "confidence": 0.8,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="je vais sur la lune",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )
    assert result.action_type == ActionType.MOVE
    # Target_name should not remain None — it must at least carry the raw
    # text so the entity resolver produces a proper UnknownEntityResult
    # instead of a generic "missing target_name" failure.
    assert result.target_name == "je vais sur la lune"


def test_interpret_search_action(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Search",
        "actor_name": "Aldric",
        "target_name": "Autel de pierre",
        "search_detail": "à la recherche d'une trappe secrète",
        "confidence": 0.85,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="je fouille l'autel à la recherche d'une trappe secrète",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )
    assert result.action_type == ActionType.SEARCH
    assert result.target_name == "Autel de pierre"
    assert result.search_detail == "à la recherche d'une trappe secrète"


def test_interpret_improvise_creative_action(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Improvise",
        "actor_name": "Aldric",
        "target_name": None,
        "improvise_description": "saute sur la statue et pose un drap dessus",
        "confidence": 0.7,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="je saute sur la statue et je pose un drap dessus",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )
    assert result.action_type == ActionType.IMPROVISE
    assert result.improvise_description is not None


# ---------------------------------------------------------------------------
# Fallbacks
# ---------------------------------------------------------------------------


def test_invalid_json_falls_back_to_improvise_in_exploration(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response("not JSON"))

    result = interpreter.interpret(
        player_text="uhhhh idk",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )
    assert result.action_type == ActionType.IMPROVISE
    assert result.confidence == 0.0
    assert result.raw_input == "uhhhh idk"
    assert result.improvise_description == "uhhhh idk"


def test_invalid_json_falls_back_to_defend_in_combat(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    combat_scene: SceneContext,
) -> None:
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response("not JSON"))

    result = interpreter.interpret(
        player_text="uhhhh",
        actor_name="Thorin",
        scene_context=combat_scene,
    )
    assert result.action_type == ActionType.DEFEND
    assert result.confidence == 0.0


def test_invalid_action_type_falls_back(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    httpx_mock.add_response(
        url=CHAT_URL,
        json=make_ollama_response(
            {"action_type": "INVALID_ACTION", "actor_name": "Aldric", "confidence": 0.5},
        ),
    )

    result = interpreter.interpret(
        player_text="do something",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )
    assert result.action_type == ActionType.IMPROVISE
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------


def test_scene_context_serialized_in_prompt(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    """The interpreter sends the scene's NPCs and exits to the LLM."""
    response_data = {
        "action_type": "Look",
        "actor_name": "Aldric",
        "confidence": 0.95,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    interpreter.interpret(
        player_text="je regarde",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )

    request = httpx_mock.get_requests()[-1]
    body = json.loads(request.content)
    user_message = body["messages"][-1]["content"]
    assert "Place de la Cathédrale" in user_message
    assert "Père Aldric" in user_message
    assert "Intérieur de la cathédrale" in user_message
    assert "Autel de pierre" in user_message


def test_combat_scene_summary_serialized(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    combat_scene: SceneContext,
) -> None:
    response_data = {
        "action_type": "Attack",
        "actor_name": "Thorin",
        "target_name": "Goblin",
        "weapon_name": "axe",
        "confidence": 0.9,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    interpreter.interpret(
        player_text="hit the goblin",
        actor_name="Thorin",
        scene_context=combat_scene,
    )

    request = httpx_mock.get_requests()[-1]
    body = json.loads(request.content)
    user_message = body["messages"][-1]["content"]
    assert "Round 3" in user_message
    assert "Goblin" in user_message


# ---------------------------------------------------------------------------
# Lethal intent detection
# ---------------------------------------------------------------------------


def test_parse_json_with_lethal_intent_flag(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    """An LLM response that includes is_lethal_intent=True propagates."""
    response_data = {
        "action_type": "Improvise",
        "actor_name": "Aldric",
        "target_name": "Père Aldric",
        "improvise_description": "charge the priest with sword",
        "is_lethal_intent": True,
        "confidence": 0.9,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="je sors mon épée et je charge Père Aldric",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )

    assert result.is_lethal_intent is True
    assert result.target_name == "Père Aldric"


def test_parse_legacy_json_defaults_lethal_intent_false(
    httpx_mock: HTTPXMock,
    interpreter: Interpreter,
    cathedral_scene: SceneContext,
) -> None:
    """An LLM response without the is_lethal_intent field defaults to False."""
    response_data = {
        "action_type": "Look",
        "actor_name": "Aldric",
        "confidence": 0.95,
    }
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(response_data))

    result = interpreter.interpret(
        player_text="je regarde",
        actor_name="Aldric",
        scene_context=cathedral_scene,
    )

    assert result.is_lethal_intent is False


def test_lethal_intent_section_present_in_prompt() -> None:
    """The interpreter prompt contains the lethal intent detection section."""
    from ai.interpreter import _SYSTEM_PROMPT

    assert "Détection d'intention létale" in _SYSTEM_PROMPT
    assert "is_lethal_intent" in _SYSTEM_PROMPT
    # Positive example
    assert "poignarde" in _SYSTEM_PROMPT
    # Negative example / rule
    assert "menace" in _SYSTEM_PROMPT
