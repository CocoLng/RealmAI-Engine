"""Tests for the BeatProgressionEngine — pure deterministic logic."""

from ai.models import InterpretedAction, MechanicsOutcome
from engine.beat_progression import (
    BeatHistory,
    BeatProgress,
    BeatProgressionResult,
    JudgeRequest,
    ObjectivePartialMatch,
    ObjectiveState,
)
from engine.validators import ActionType
from world.story_arc import (
    BeatObjective,
    ObjectiveKind,
    StoryArc,
    StoryBeat,
)


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


# ---------------------------------------------------------------------------
# B4 helpers
# ---------------------------------------------------------------------------

def _make_arc(beats: list[StoryBeat], current_index: int = 0) -> StoryArc:
    while len(beats) < 8:  # arc requires min 8 beats
        beats.append(StoryBeat(
            beat_number=len(beats) + 1,
            title=f"Filler {len(beats) + 1}",
            description="...",
            location_hint="...",
            encounter_type="exploration",
        ))
    arc = StoryArc(
        campaign_id="test",
        theme="test",
        premise="A long enough premise here.",
        beats=beats,
        villain_name="X",
        villain_motivation="Y",
        current_beat_index=current_index,
    )
    return arc


def _interp(action_type: ActionType, target: str | None = None, raw: str = "") -> InterpretedAction:
    return InterpretedAction(
        action_type=action_type,
        actor_name="hero",
        target_name=target,
        raw_input=raw or f"{action_type.value} {target or ''}",
    )


def _outcome(**kwargs) -> MechanicsOutcome:
    return MechanicsOutcome(summary=kwargs.pop("summary", "ok"), **kwargs)


def _history() -> BeatHistory:
    return BeatHistory(recent_decisions=[], current_beat_turns=0)


# ---------------------------------------------------------------------------
# B4 tests
# ---------------------------------------------------------------------------

def test_stay_when_no_match():
    obj = BeatObjective(
        id="talk_npc", kind=ObjectiveKind.TALK, target="Bob", description="...",
    )
    beat = StoryBeat(
        beat_number=1, title="X", description="...", location_hint="...",
        encounter_type="social", objectives=[obj],
    )
    arc = _make_arc([beat])
    from engine.beat_progression import BeatProgressionEngine
    engine = BeatProgressionEngine()
    result = engine.evaluate(
        arc=arc,
        interpreted=_interp(ActionType.MOVE, target="north"),
        outcome=_outcome(),
        location=None,
        history=_history(),
        world_flags={},
        inventory=set(),
    )
    assert result.decision == "STAY"
    assert result.new_beat is None
    assert result.judge_request is None
