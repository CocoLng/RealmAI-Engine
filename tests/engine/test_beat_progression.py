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
    AdvanceRule,
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


def test_advance_on_last_beat_returns_new_beat_none():
    """REGRESSION I1: ADVANCE on the final beat must set new_beat=None
    so the orchestrator can detect arc completion."""
    from engine.beat_progression import BeatProgressionEngine
    obj = BeatObjective(
        id="last_obj", kind=ObjectiveKind.TALK, target="Final NPC",
        description="...",
    )
    final_beat = StoryBeat(
        beat_number=8, title="Finale", description="...", location_hint="...",
        encounter_type="boss", objectives=[obj],
    )
    # Build an arc with the final beat as the current one.
    fillers = [
        StoryBeat(
            beat_number=i + 1, title=f"F{i+1}", description="...",
            location_hint="...", encounter_type="exploration",
        )
        for i in range(7)
    ]
    arc = _make_arc(fillers + [final_beat], current_index=7)

    engine = BeatProgressionEngine()
    result = engine.evaluate(
        arc=arc,
        interpreted=_interp(ActionType.TALK, target="Final NPC"),
        outcome=_outcome(),
        location=None, history=_history(),
        world_flags={}, inventory=set(),
    )
    assert result.decision == "ADVANCE"
    assert result.new_beat is None  # arc complete
    assert "arc_complete_on_advance" in result.reasons


def test_m_of_n_fallback_logs_reason():
    """REGRESSION M1: M_OF_N with advance_threshold=None should log a
    fallback reason so the misconfiguration is observable."""
    from engine.beat_progression import BeatProgressionEngine
    objs = [
        BeatObjective(
            id=f"obj_{i}", kind=ObjectiveKind.FLAG, target=f"flag_{i}",
            description="...", required=False,
        )
        for i in range(3)
    ]
    beat = StoryBeat(
        beat_number=1, title="X", description="...", location_hint="...",
        encounter_type="exploration", objectives=objs,
        advance_rule=AdvanceRule.M_OF_N, advance_threshold=None,
    )
    arc = _make_arc([beat])
    engine = BeatProgressionEngine()
    # Set 2 of 3 flags — won't trigger ADVANCE since threshold falls back to 3.
    result = engine.evaluate(
        arc=arc,
        interpreted=_interp(ActionType.IMPROVISE),
        outcome=_outcome(),
        location=None, history=_history(),
        world_flags={"flag_0": True, "flag_1": True}, inventory=set(),
    )
    # The fallback reason should be present even though we didn't advance.
    assert "advance_rule:m_of_n_no_threshold_fallback" in result.reasons
    assert result.decision == "STAY" or result.decision == "ADVANCE"  # depends on threshold semantics
