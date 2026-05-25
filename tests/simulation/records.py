"""Pydantic models exchanged between simulator components."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AgentIntent(BaseModel):
    """A single action chosen by the AutonomousAgent for a turn."""

    reasoning: str = Field(..., max_length=200)
    action: Literal[
        "attack",
        "cast_spell",
        "defend",
        "flee",
        "move",
        "look",
        "talk",
        "search",
        "equip",
        "unequip",
        "use_item",
        "free_form",
        "wait",
    ]
    args: dict[str, str] = Field(default_factory=dict)
    raw_text: str | None = None

    @model_validator(mode="after")
    def _free_form_requires_raw_text(self) -> "AgentIntent":
        if self.action == "free_form" and not self.raw_text:
            raise ValueError("raw_text is required when action == 'free_form'")
        return self


class LLMTimings(BaseModel):
    """Per-phase latency in milliseconds for a single turn."""

    agent: int
    interpreter: int
    engine: int
    narrator: int


class TurnOutcome(BaseModel):
    """What happened when the AgentIntent was executed."""

    narration: str
    action_resolved: dict[str, Any]
    error: str | None
    timing_ms: LLMTimings


class IncoherenceAlert(BaseModel):
    """An incoherence detected by the IncoherenceChecker."""

    severity: Literal["hard", "soft", "drift"]
    category: str
    turn: int
    rule: str
    narration_snippet: str = Field(..., max_length=200)
    expected: str
    source: Literal["heuristic", "story_director"] = "heuristic"


class TurnRecord(BaseModel):
    """The full record persisted to transcript.jsonl per turn."""

    turn: int
    ts: str
    observation: str
    intent: AgentIntent
    outcome: TurnOutcome
    diff: dict[str, list[Any]]  # {path: [old, new]}
    alerts: list[IncoherenceAlert]
    agent_retries: int = 0
