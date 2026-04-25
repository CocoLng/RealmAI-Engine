"""Tests for the BeatProgressionEngine — pure deterministic logic."""

from engine.beat_progression import (
    BeatHistory,
    BeatProgress,
    BeatProgressionResult,
    JudgeRequest,
    ObjectivePartialMatch,
    ObjectiveState,
)
from world.story_arc import StoryBeat


def test_objective_state_defaults():
    state = ObjectiveState(status="pending")
    assert state.status == "pending"
    assert state.last_attempt_action_id is None
    assert state.last_attempt_score == 0.0
    assert state.completed_at_turn is None


def test_beat_history_construction():
    h = BeatHistory(recent_decisions=["STAY", "STAY", "ADVANCE"], current_beat_turns=3)
    assert len(h.recent_decisions) == 3
    assert h.current_beat_turns == 3


def test_beat_progression_result_shape():
    beat = StoryBeat(
        beat_number=1,
        title="X",
        description="A placeholder beat for shape testing.",
        location_hint="Somewhere",
        encounter_type="exploration",
    )
    r = BeatProgressionResult(
        decision="STAY",
        progress=BeatProgress(
            beat=beat,
            objective_states={},
            progress_score=0,
            last_action_advanced=False,
        ),
        reasons=["no objective matched"],
    )
    assert r.decision == "STAY"
    assert r.new_beat is None
    assert r.judge_request is None


def test_judge_request_includes_partial_objectives():
    pm = ObjectivePartialMatch(
        id="talk_kaelen",
        kind="talk",  # type: ignore[arg-type]  # str-coerced from ObjectiveKind enum
        target="Kaelen",
        description="...",
        match_score=0.55,
        gate_failed=False,
        gate_kind=None,
    )
    req = JudgeRequest(
        beat_title="X",
        beat_description="Y",
        beat_judge_rubric=None,
        objectives=[pm],
        player_action_text="I wave at Kaelen",
        interpreted_action={},
        outcome_summary="...",
        location_name="Forge",
        npcs_present=["Kaelen"],
    )
    assert len(req.objectives) == 1
