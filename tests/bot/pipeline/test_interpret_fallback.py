"""Fallback IMPROVISE quand l'interpreter épuise ses retries (axe robustesse)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ai.client import LLMParseError, OllamaUnavailableError
from ai.scene_context import SceneContext
from bot.pipeline import interpret
from engine.validators import ActionType


def _scene() -> SceneContext:
    return SceneContext(location_name="Crypte", location_description="Sombre.")


def _parse_error() -> LLMParseError:
    return LLMParseError(
        "unknown action_type 'Dance'",
        raw_response="{}",
        model="qwen3.5:4b",
        messages=[],
    )


@pytest.mark.asyncio
async def test_fallback_improvise_on_parse_error_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMParseError après retries → IMPROVISE forgé, confidence 0.3."""

    async def _exhausted(fn: Any, **kwargs: Any) -> Any:
        raise _parse_error()

    monkeypatch.setattr(interpret, "retry_llm_call", _exhausted)

    action = await interpret.call_interpreter(
        interpreter=MagicMock(),  # jamais appelé : retry_llm_call est court-circuité
        player_text="je danse avec le squelette",
        scene=_scene(),
        actor_name="Aldric",
        language="fr",
    )

    assert action.action_type is ActionType.IMPROVISE
    assert action.improvise_description == "je danse avec le squelette"
    assert action.raw_input == "je danse avec le squelette"
    assert action.actor_name == "Aldric"
    assert action.confidence == interpret.FALLBACK_IMPROVISE_CONFIDENCE
    assert action.confidence == 0.3


@pytest.mark.asyncio
async def test_ollama_unavailable_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serveur down = vraie panne : pas de fallback mensonger."""

    async def _down(fn: Any, **kwargs: Any) -> Any:
        raise OllamaUnavailableError("connection refused")

    monkeypatch.setattr(interpret, "retry_llm_call", _down)

    with pytest.raises(OllamaUnavailableError):
        await interpret.call_interpreter(
            interpreter=MagicMock(),
            player_text="je regarde",
            scene=_scene(),
            actor_name="Aldric",
            language="fr",
        )
