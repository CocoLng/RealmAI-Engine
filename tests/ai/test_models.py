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
        DirectorNote(coherence_issues=[], suggested_hooks=[], priority="extreme")  # type: ignore[arg-type]


def test_npc_response_default_values() -> None:
    resp = NPCResponse(dialogue="Hello, traveler.")
    assert resp.disposition_change == 0
    assert resp.revealed_info == []


def test_npc_response_valid_disposition_change() -> None:
    resp = NPCResponse(dialogue="I hate you!", disposition_change=-2)
    assert resp.disposition_change == -2


def test_mechanics_outcome_minimal():
    from ai.models import MechanicsOutcome

    out = MechanicsOutcome(summary="Xavier searches Croix de fer.")
    assert out.summary == "Xavier searches Croix de fer."
    assert out.player_intent == ""
    assert out.outcome_facts == ""


def test_mechanics_outcome_full():
    from ai.models import MechanicsOutcome

    out = MechanicsOutcome(
        summary="Xavier picks up the Croix de fer.",
        player_intent="inspecte la croix de fer pour voir si c une d'origine de 39-45",
        outcome_facts="Item 'Croix de fer' moved from scene to Xavier's inventory.",
    )
    assert "39-45" in out.player_intent
    assert "inventory" in out.outcome_facts


def test_npc_sheet_minimal():
    from ai.models import NPCSheet
    sheet = NPCSheet(
        personality="Vieil ermite méfiant.",
        description="Un homme voûté en robe de bure.",
        secrets=["Il cache un talisman."],
        knowledge=["Connaît les herbes locales."],
    )
    assert sheet.personality.startswith("Vieil")
    assert len(sheet.secrets) == 1
    assert len(sheet.knowledge) == 1


def test_npc_sheet_rejects_empty_personality():
    from pydantic import ValidationError
    from ai.models import NPCSheet
    with pytest.raises(ValidationError):
        NPCSheet(
            personality="",
            description="Un homme voûté.",
            secrets=["Un secret."],
            knowledge=["Un savoir."],
        )


def test_npc_sheet_rejects_empty_secrets():
    from pydantic import ValidationError
    from ai.models import NPCSheet
    with pytest.raises(ValidationError):
        NPCSheet(
            personality="Méfiant.",
            description="Un homme voûté.",
            secrets=[],
            knowledge=["Un savoir."],
        )


def test_npc_sheet_full():
    from ai.models import NPCSheet
    sheet = NPCSheet(
        personality="Méfiant mais loyal envers les justes.",
        description="Un ermite voûté, robe de bure tachée de cendre.",
        secrets=["Sait que Dom André est corrompu."],
        knowledge=["Connaît l'entrée de la crypte sous l'autel."],
    )
    assert "corrompu" in sheet.secrets[0]
    assert len(sheet.knowledge) == 1
