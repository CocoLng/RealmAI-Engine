"""Tests for tests/simulation/records.py — Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.simulation.records import (
    AgentIntent,
    IncoherenceAlert,
    LLMTimings,
    TurnOutcome,
    TurnRecord,
)


class TestAgentIntent:
    def test_attack_with_target(self) -> None:
        intent = AgentIntent(
            reasoning="goblin is bloodied, finishing it",
            action="attack",
            args={"target": "Goblin_2"},
        )
        assert intent.action == "attack"
        assert intent.args["target"] == "Goblin_2"
        assert intent.raw_text is None

    def test_free_form_requires_raw_text(self) -> None:
        with pytest.raises(ValidationError, match="raw_text"):
            AgentIntent(reasoning="x", action="free_form", args={})

    def test_unknown_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentIntent(reasoning="x", action="dance", args={})  # type: ignore[arg-type]

    def test_reasoning_max_length(self) -> None:
        with pytest.raises(ValidationError):
            AgentIntent(reasoning="x" * 201, action="look", args={})


class TestIncoherenceAlert:
    def test_construct(self) -> None:
        alert = IncoherenceAlert(
            severity="hard",
            category="dead_npc_speaks",
            turn=12,
            rule="R1.npc_status",
            narration_snippet="Garm sourit.",
            expected="Garm marked dead at turn 8",
        )
        assert alert.severity == "hard"
        assert alert.turn == 12

    def test_severity_enum(self) -> None:
        with pytest.raises(ValidationError):
            IncoherenceAlert(
                severity="critical",  # type: ignore[arg-type]  # not in enum
                category="x",
                turn=1,
                rule="r",
                narration_snippet="s",
                expected="e",
            )


class TestTurnRecord:
    def test_full_record(self) -> None:
        record = TurnRecord(
            turn=1,
            ts="2026-05-25T16:42:01Z",
            observation="TURN 1\nYou play: Aria",
            intent=AgentIntent(reasoning="look", action="look", args={}),
            outcome=TurnOutcome(
                narration="Vous voyez une grotte.",
                action_resolved={"type": "look"},
                error=None,
                timing_ms=LLMTimings(agent=100, interpreter=200, engine=5, narrator=1500),
            ),
            diff={},
            alerts=[],
            agent_retries=0,
        )
        assert record.turn == 1
        assert record.outcome.error is None

    def test_serializes_to_jsonl_line(self) -> None:
        record = TurnRecord(
            turn=1,
            ts="2026-05-25T16:42:01Z",
            observation="o",
            intent=AgentIntent(reasoning="r", action="look", args={}),
            outcome=TurnOutcome(
                narration="n",
                action_resolved={},
                error=None,
                timing_ms=LLMTimings(agent=1, interpreter=2, engine=3, narrator=4),
            ),
            diff={},
            alerts=[],
            agent_retries=0,
        )
        line = record.model_dump_json()
        assert '"turn":1' in line
        assert "\n" not in line
