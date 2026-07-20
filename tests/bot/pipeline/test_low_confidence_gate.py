"""Gate de confiance basse — process() pause avant résolution d'entités."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ai.models import InterpretedAction
from bot.pipeline import interpret, orchestrator
from bot.pipeline.orchestrator import (
    CONFIDENCE_CLARIFY_THRESHOLD,
    LowConfidenceResult,
    PipelineRunner,
)
from engine.validators import ActionType

_CONTINUED = object()
"""Sentinelle : _continue_from_resolution a été atteint (pas de gate)."""


def _action(action_type: ActionType, confidence: float) -> InterpretedAction:
    return InterpretedAction(
        action_type=action_type,
        actor_name="Aldric",
        raw_input="peu importe",
        confidence=confidence,
    )


def _runner(monkeypatch: pytest.MonkeyPatch, action: InterpretedAction) -> PipelineRunner:
    async def _fake_interpret(**kwargs: Any) -> InterpretedAction:
        return action

    async def _fake_continue(self: Any, interpreted: Any, progress_callback: Any) -> Any:
        return _CONTINUED

    monkeypatch.setattr(interpret, "call_interpreter", _fake_interpret)
    monkeypatch.setattr(
        PipelineRunner, "_continue_from_resolution", _fake_continue,
    )
    return PipelineRunner(
        interpreter=MagicMock(),
        narrator=MagicMock(),
        location=None,
        npcs={},
        actor_name="Aldric",
    )


@pytest.mark.asyncio
async def test_below_threshold_returns_low_confidence_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = _action(ActionType.IMPROVISE, 0.59)
    result = await _runner(monkeypatch, action).process("je tente un truc")

    assert isinstance(result, LowConfidenceResult)
    assert result.interpreted_action is action


@pytest.mark.asyncio
async def test_at_threshold_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.6 exactement passe : le gate est strict (< 0.6)."""
    action = _action(ActionType.ATTACK, CONFIDENCE_CLARIFY_THRESHOLD)
    result = await _runner(monkeypatch, action).process("j'attaque")

    assert result is _CONTINUED


@pytest.mark.asyncio
async def test_question_never_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une question est gratuite et sans effet d'état — jamais de friction."""
    action = _action(ActionType.QUESTION, 0.2)
    result = await _runner(monkeypatch, action).process("que vois-je ?")

    assert result is _CONTINUED


@pytest.mark.asyncio
async def test_process_interpreted_action_never_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La voie de reprise (après Oui) ne repasse jamais par le gate."""
    action = _action(ActionType.IMPROVISE, 0.1)
    runner = _runner(monkeypatch, action)
    result = await runner.process_interpreted_action(action)

    assert result is _CONTINUED


def test_facade_reexports_low_confidence_result() -> None:
    from bot.action_pipeline import LowConfidenceResult as FacadeLCR

    assert FacadeLCR is orchestrator.LowConfidenceResult
