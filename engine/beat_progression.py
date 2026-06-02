"""Beat progression engine — single decision point for beat advancement.

Pure deterministic Python. The engine evaluates every player action against
the current beat's objectives and emits one of three decisions:

- ``ADVANCE``: objectives satisfy the beat's ``advance_rule``; move to next beat.
- ``STAY``: action does not affect this beat; do nothing.
- ``NEEDS_JUDGE``: action partially matches; defer to ``ai.beat_judge.BeatJudge``.

NO LLM CALLS in this module. The engine is testable without Ollama.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from engine.contracts import InterpretedAction
from world.story_arc import (
    GateKind,
    ObjectiveKind,
    StoryBeat,
)

if TYPE_CHECKING:
    from engine.contracts import MechanicsOutcome
    from world.location import Location
    from world.story_arc import StoryArc

__all__ = [
    "ObjectiveState",
    "BeatProgress",
    "BeatHistory",
    "ObjectivePartialMatch",
    "JudgeRequest",
    "BeatProgressionResult",
    "BeatProgressionEngine",
    "log_decision",
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
    interpreted_action: InterpretedAction
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


class BeatProgressionEngine:
    """Single-decision-point engine for beat advancement.

    Pure deterministic. NO LLM CALLS. The LLM judge fires from outside this
    class (in the orchestrator), only when ``evaluate()`` returns NEEDS_JUDGE.
    """

    def evaluate(
        self,
        arc: "StoryArc",
        interpreted: "InterpretedAction",
        outcome: "MechanicsOutcome",
        location: "Location | None",
        history: BeatHistory,
        world_flags: dict[str, Any],
        inventory: set[str],
    ) -> BeatProgressionResult:
        """Evaluate the current action against the active beat's objectives.

        Returns a BeatProgressionResult with decision, progress, and optional
        ``new_beat`` (on ADVANCE) or ``judge_request`` (on NEEDS_JUDGE).

        Args:
            arc:         The current story arc (provides beats + current index).
            interpreted: The player's parsed action for this turn.
            outcome:     The mechanical result produced by the engine.
            location:    The player's current location after the action, or None.
            history:     Sliding window of recent decisions (for context only).
            world_flags: Mutable world-state flags (flag_name → truthy value).
            inventory:   The player's current inventory as a set of item names.

        Returns:
            BeatProgressionResult with decision, progress snapshot, and optional
            new_beat (ADVANCE) or judge_request (NEEDS_JUDGE).
        """
        from engine.objective_matchers import compute_match_score, evaluate_gate
        from world.story_arc import AdvanceRule, advance_beat

        reasons: list[str] = []

        # 1. Bounds check — arc complete?
        if arc.current_beat_index >= len(arc.beats):
            empty_progress = BeatProgress(
                beat=arc.beats[-1],
                objective_states={},
                progress_score=100,
                last_action_advanced=False,
            )
            return BeatProgressionResult(
                decision="STAY",
                progress=empty_progress,
                reasons=["arc_complete"],
            )

        current_beat = arc.beats[arc.current_beat_index]

        # 2. Empty objectives → no progression possible (legacy beats with
        # un-mappable triggers, or generator hadn't filled them).
        if not current_beat.objectives:
            return BeatProgressionResult(
                decision="STAY",
                progress=BeatProgress(
                    beat=current_beat,
                    objective_states={},
                    progress_score=0,
                    last_action_advanced=False,
                ),
                reasons=["no_objectives"],
            )

        # 3. Score every objective.
        states: dict[str, ObjectiveState] = {}
        partial_matches: list[ObjectivePartialMatch] = []

        for obj in current_beat.objectives:
            score = compute_match_score(
                obj, interpreted, outcome, location, world_flags, inventory,
            )

            if score >= obj.fuzzy_threshold:
                # Match passed. Now check the gate.
                if obj.gate is None or evaluate_gate(
                    obj.gate, outcome, world_flags, inventory,
                ):
                    states[obj.id] = ObjectiveState(
                        status="completed",
                        last_attempt_score=score,
                    )
                    reasons.append(f"{obj.id}:match_full")
                else:
                    states[obj.id] = ObjectiveState(
                        status="partial",
                        last_attempt_score=score,
                    )
                    reasons.append(f"{obj.id}:gate_failed:{obj.gate.kind.value}")
                    partial_matches.append(ObjectivePartialMatch(
                        id=obj.id, kind=obj.kind, target=obj.target,
                        description=obj.description,
                        match_score=score, gate_failed=True,
                        gate_kind=obj.gate.kind,
                    ))
            elif score >= 0.5:
                states[obj.id] = ObjectiveState(
                    status="partial",
                    last_attempt_score=score,
                )
                reasons.append(f"{obj.id}:match_below_threshold")
                partial_matches.append(ObjectivePartialMatch(
                    id=obj.id, kind=obj.kind, target=obj.target,
                    description=obj.description,
                    match_score=score, gate_failed=False, gate_kind=None,
                ))
            else:
                states[obj.id] = ObjectiveState(
                    status="pending",
                    last_attempt_score=score,
                )

        # 4. Compute progress score.
        completed_count = sum(1 for s in states.values() if s.status == "completed")
        total_count = len(states)
        progress_score = int((completed_count / total_count) * 100) if total_count else 0

        # 5. Evaluate advance_rule.
        required_objectives = [o for o in current_beat.objectives if o.required]
        required_completed = sum(
            1 for o in required_objectives if states[o.id].status == "completed"
        )

        will_advance = False
        if current_beat.advance_rule == AdvanceRule.ALL_REQUIRED:
            will_advance = (
                len(required_objectives) > 0
                and required_completed == len(required_objectives)
            )
        elif current_beat.advance_rule == AdvanceRule.ANY:
            will_advance = completed_count >= 1
        elif current_beat.advance_rule == AdvanceRule.M_OF_N:
            if current_beat.advance_threshold is None:
                threshold = len(current_beat.objectives)
                reasons.append("advance_rule:m_of_n_no_threshold_fallback")
            else:
                threshold = current_beat.advance_threshold
            will_advance = completed_count >= threshold

        progress = BeatProgress(
            beat=current_beat,
            objective_states=states,
            progress_score=progress_score,
            last_action_advanced=will_advance,
        )

        if will_advance:
            new_arc = advance_beat(arc)
            # advance_beat is idempotent at the last beat — if it returned the
            # same object, the arc is complete (no further beat exists).
            if new_arc is arc:
                new_beat = None
                reasons.append("arc_complete_on_advance")
            else:
                new_beat = new_arc.beats[new_arc.current_beat_index]
            reasons.append(f"advance_rule:{current_beat.advance_rule.value}")
            return BeatProgressionResult(
                decision="ADVANCE",
                progress=progress,
                new_beat=new_beat,
                reasons=reasons,
            )

        # 6. Partial match this turn → defer to judge.
        if partial_matches:
            return BeatProgressionResult(
                decision="NEEDS_JUDGE",
                progress=progress,
                judge_request=JudgeRequest(
                    beat_title=current_beat.title,
                    beat_description=current_beat.description,
                    beat_judge_rubric=current_beat.judge_rubric,
                    objectives=partial_matches,
                    player_action_text=interpreted.raw_input,
                    interpreted_action=interpreted,
                    outcome_summary=outcome.summary,
                    location_name=location.name if location else None,
                    npcs_present=[],  # caller fills in if needed
                ),
                reasons=reasons,
            )

        return BeatProgressionResult(
            decision="STAY",
            progress=progress,
            reasons=reasons or ["no_match"],
        )


_logger = logging.getLogger(__name__)


_PROD_LOG_PATH = Path("logs/beat_progression.jsonl")


def log_decision(
    *,
    campaign_id: str,
    beat_number: int,
    result: BeatProgressionResult,
    judge_passed: bool | None = None,
    judge_confidence: float | None = None,
    latency_ms: int | None = None,
) -> None:
    """Append one JSON line to the production engine log.

    Failures are swallowed.
    """
    try:
        _PROD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "campaign_id": campaign_id,
            "beat_number": beat_number,
            "decision": result.decision,
            "progress_score": result.progress.progress_score,
            "judge_passed": judge_passed,
            "judge_confidence": judge_confidence,
            "objectives_updated": [
                oid for oid, st in result.progress.objective_states.items()
                if st.status == "completed"
            ],
            "reasons": result.reasons,
            "latency_ms": latency_ms,
        }
        with _PROD_LOG_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        _logger.exception("prod log failed for campaign=%s", campaign_id)
