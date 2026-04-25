"""Beat progression engine — single decision point for beat advancement.

Pure deterministic Python. The engine evaluates every player action against
the current beat's objectives and emits one of three decisions:

- ``ADVANCE``: objectives satisfy the beat's ``advance_rule``; move to next beat.
- ``STAY``: action does not affect this beat; do nothing.
- ``NEEDS_JUDGE``: action partially matches; defer to ``ai.beat_judge.BeatJudge``.

NO LLM CALLS in this module. The engine is testable without Ollama.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from world.story_arc import (
    GateKind,
    ObjectiveKind,
    StoryBeat,
)

__all__ = [
    "ObjectiveState",
    "BeatProgress",
    "BeatHistory",
    "ObjectivePartialMatch",
    "JudgeRequest",
    "BeatProgressionResult",
]


class ObjectiveState(BaseModel):
    """Runtime state of one BeatObjective for the current beat."""

    status: Literal["pending", "partial", "completed"]
    last_attempt_action_id: str | None = None
    last_attempt_score: float = 0.0
    completed_at_turn: int | None = None


class BeatProgress(BaseModel):
    """Snapshot of progress on the currently active beat."""

    beat: StoryBeat
    objective_states: dict[str, ObjectiveState]
    progress_score: int = Field(ge=0, le=100)
    last_action_advanced: bool


class BeatHistory(BaseModel):
    """Sliding window of recent engine decisions for stagnation detection."""

    recent_decisions: list[Literal["ADVANCE", "STAY", "NEEDS_JUDGE"]] = Field(
        default_factory=list, max_length=5,
    )
    current_beat_turns: int = 0


class ObjectivePartialMatch(BaseModel):
    """An objective that partially matched this turn — passed to BeatJudge."""

    id: str
    kind: ObjectiveKind
    target: str
    description: str
    match_score: float = Field(ge=0.0, le=1.0)
    gate_failed: bool
    gate_kind: GateKind | None


class JudgeRequest(BaseModel):
    """Input contract for ai.beat_judge.BeatJudge.evaluate()."""

    beat_title: str
    beat_description: str
    beat_judge_rubric: str | None
    objectives: list[ObjectivePartialMatch]
    player_action_text: str
    interpreted_action: dict  # type: ignore[type-arg]
    outcome_summary: str
    location_name: str | None
    npcs_present: list[str]


class BeatProgressionResult(BaseModel):
    """Output of BeatProgressionEngine.evaluate()."""

    decision: Literal["ADVANCE", "STAY", "NEEDS_JUDGE"]
    progress: BeatProgress
    new_beat: StoryBeat | None = None
    judge_request: JudgeRequest | None = None
    reasons: list[str] = Field(default_factory=list)
