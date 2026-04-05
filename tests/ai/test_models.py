"""Tests for AI Pydantic models."""

import pytest
from pydantic import ValidationError

from ai.models import (
    DirectorNote,
    InterpretedAction,
    NPCResponse,
    NarrativeResult,
)
from engine.validators import ActionType


def test_interpreted_action_valid() -> None:
    action = InterpretedAction(
        action_type=ActionType.ATTACK,
        actor_name="Thorin",
        target_name="Goblin",
        raw_input="I attack the goblin",
    )
    assert action.confidence == 1.0
    assert action.weapon_name is None


def test_interpreted_action_low_confidence() -> None:
    action = InterpretedAction(
        action_type=ActionType.DEFEND,
        actor_name="Thorin",
        raw_input="uh idk",
        confidence=0.0,
    )
    assert action.confidence == 0.0


def test_narrative_result_valid() -> None:
    result = NarrativeResult(narrative="The sword strikes true.", tone="dramatic")
    assert result.narrative
    assert result.tone == "dramatic"


def test_director_note_valid() -> None:
    note = DirectorNote(
        coherence_issues=["Quest abandoned"],
        suggested_hooks=["Bring back the merchant NPC"],
        priority="high",
    )
    assert note.priority == "high"


def test_director_note_invalid_priority() -> None:
    with pytest.raises(ValidationError):
        DirectorNote(coherence_issues=[], suggested_hooks=[], priority="extreme")


def test_npc_response_default_values() -> None:
    resp = NPCResponse(dialogue="Hello, traveler.")
    assert resp.disposition_change == 0
    assert resp.revealed_info == []


def test_npc_response_valid_disposition_change() -> None:
    resp = NPCResponse(dialogue="I hate you!", disposition_change=-2)
    assert resp.disposition_change == -2
