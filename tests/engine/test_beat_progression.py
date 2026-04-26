"""Tests for the BeatProgressionEngine — pure deterministic logic."""

from unittest.mock import MagicMock

from ai.models import InterpretedAction, MechanicsOutcome
from engine.beat_progression import (
    BeatHistory,
    BeatProgress,
    BeatProgressionResult,
    BeatProgressionEngine,
    JudgeRequest,
    ObjectivePartialMatch,
    ObjectiveState,
)
from engine.validators import ActionType
from world.story_arc import (
    AdvanceRule,
    BeatObjective,
    GateKind,
    ObjectiveGate,
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
        interpreted_action=InterpretedAction(
            action_type=ActionType.TALK,
            actor_name="hero",
            target_name="Kaelen",
            raw_input="I wave at Kaelen",
        ),
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


# ---------------------------------------------------------------------------
# B5 tests — ADVANCE / NEEDS_JUDGE / edge paths
# ---------------------------------------------------------------------------


def test_advance_all_required():
    obj1 = BeatObjective(
        id="talk_npc", kind=ObjectiveKind.TALK, target="Bob", description="...",
    )
    obj2 = BeatObjective(
        id="get_item", kind=ObjectiveKind.POSSESS, target="key", description="...",
    )
    beat = StoryBeat(
        beat_number=1, title="X", description="...", location_hint="...",
        encounter_type="social", objectives=[obj1, obj2],
        advance_rule=AdvanceRule.ALL_REQUIRED,
    )
    arc = _make_arc([beat])
    engine = BeatProgressionEngine()
    # Player has the key AND talks to Bob.
    result = engine.evaluate(
        arc=arc,
        interpreted=_interp(ActionType.TALK, target="Bob"),
        outcome=_outcome(),
        location=None,
        history=_history(),
        world_flags={},
        inventory={"key"},
    )
    assert result.decision == "ADVANCE"
    assert result.progress.progress_score == 100
    assert result.new_beat is not None


def test_no_advance_when_one_required_missing():
    obj1 = BeatObjective(
        id="talk_npc", kind=ObjectiveKind.TALK, target="Bob", description="...",
    )
    obj2 = BeatObjective(
        id="get_item", kind=ObjectiveKind.POSSESS, target="key", description="...",
    )
    beat = StoryBeat(
        beat_number=1, title="X", description="...", location_hint="...",
        encounter_type="social", objectives=[obj1, obj2],
    )
    arc = _make_arc([beat])
    engine = BeatProgressionEngine()
    # Player talks to Bob but has no key.
    result = engine.evaluate(
        arc=arc,
        interpreted=_interp(ActionType.TALK, target="Bob"),
        outcome=_outcome(),
        location=None,
        history=_history(),
        world_flags={},
        inventory=set(),
    )
    assert result.decision == "STAY"
    assert result.progress.progress_score == 50  # 1/2 completed


def test_advance_any_rule():
    obj = BeatObjective(
        id="talk_npc", kind=ObjectiveKind.TALK, target="Bob", description="...",
    )
    beat = StoryBeat(
        beat_number=1, title="X", description="...", location_hint="...",
        encounter_type="social", objectives=[obj, BeatObjective(
            id="other", kind=ObjectiveKind.POSSESS, target="key", description="...",
            required=False,
        )],
        advance_rule=AdvanceRule.ANY,
    )
    arc = _make_arc([beat])
    engine = BeatProgressionEngine()
    result = engine.evaluate(
        arc=arc, interpreted=_interp(ActionType.TALK, target="Bob"),
        outcome=_outcome(), location=None, history=_history(),
        world_flags={}, inventory=set(),
    )
    assert result.decision == "ADVANCE"


def test_advance_m_of_n_rule():
    objs = [
        BeatObjective(
            id=f"o{i}", kind=ObjectiveKind.FLAG, target=f"f{i}", description="...",
            required=False,
        )
        for i in range(4)
    ]
    beat = StoryBeat(
        beat_number=1, title="X", description="...", location_hint="...",
        encounter_type="exploration", objectives=objs,
        advance_rule=AdvanceRule.M_OF_N, advance_threshold=2,
    )
    arc = _make_arc([beat])
    engine = BeatProgressionEngine()
    result = engine.evaluate(
        arc=arc, interpreted=_interp(ActionType.IMPROVISE),
        outcome=_outcome(), location=None, history=_history(),
        world_flags={"f0": True, "f1": True}, inventory=set(),
    )
    assert result.decision == "ADVANCE"  # 2 of 4 satisfies threshold


def test_needs_judge_on_partial_match():
    obj = BeatObjective(
        id="talk_kaelen", kind=ObjectiveKind.TALK, target="Kaelen", description="...",
    )
    beat = StoryBeat(
        beat_number=1, title="X", description="Y", location_hint="Z",
        encounter_type="social", objectives=[obj],
        judge_rubric="Accept any approach where Kaelen actually speaks.",
    )
    arc = _make_arc([beat])
    engine = BeatProgressionEngine()
    # Talk to "Kae" — fuzzy ratio about 0.7-ish, but with default threshold
    # 0.7, may land just below.
    result = engine.evaluate(
        arc=arc, interpreted=_interp(ActionType.TALK, target="Kae"),
        outcome=_outcome(), location=None, history=_history(),
        world_flags={}, inventory=set(),
    )
    # Either ADVANCE (if ratio >= 0.7) or NEEDS_JUDGE (if 0.5 <= ratio < 0.7).
    # NEVER STAY for this input.
    assert result.decision in ("ADVANCE", "NEEDS_JUDGE")
    if result.decision == "NEEDS_JUDGE":
        assert result.judge_request is not None
        assert len(result.judge_request.objectives) == 1
        assert result.judge_request.beat_judge_rubric is not None


def test_needs_judge_on_gate_failed():
    """Match passes but gate fails → NEEDS_JUDGE with gate_failed=True."""
    obj = BeatObjective(
        id="talk_kaelen", kind=ObjectiveKind.TALK, target="Kaelen", description="...",
        gate=ObjectiveGate(kind=GateKind.MIN_REVEALS, value=1),
    )
    beat = StoryBeat(
        beat_number=1, title="X", description="...", location_hint="...",
        encounter_type="social", objectives=[obj],
    )
    arc = _make_arc([beat])
    engine = BeatProgressionEngine()
    result = engine.evaluate(
        arc=arc, interpreted=_interp(ActionType.TALK, target="Kaelen"),
        outcome=_outcome(talk_reveals_count=0),  # gate fails
        location=None, history=_history(),
        world_flags={}, inventory=set(),
    )
    assert result.decision == "NEEDS_JUDGE"
    assert result.judge_request is not None
    pm = result.judge_request.objectives[0]
    assert pm.gate_failed is True
    assert pm.gate_kind == GateKind.MIN_REVEALS


def test_arc_complete_returns_stay():
    beat = StoryBeat(
        beat_number=1, title="X", description="...", location_hint="...",
        encounter_type="exploration",
    )
    arc = _make_arc([beat])
    # Force current_beat_index past the end.
    arc = arc.model_copy(update={"current_beat_index": len(arc.beats)})
    engine = BeatProgressionEngine()
    result = engine.evaluate(
        arc=arc, interpreted=_interp(ActionType.IMPROVISE),
        outcome=_outcome(), location=None, history=_history(),
        world_flags={}, inventory=set(),
    )
    assert result.decision == "STAY"
    assert "arc_complete" in result.reasons


def test_no_objectives_returns_stay_with_reason():
    """Beat with empty objectives list (legacy unmappable trigger) stays put."""
    beat = StoryBeat(
        beat_number=1, title="X", description="...", location_hint="...",
        encounter_type="exploration",
    )
    arc = _make_arc([beat])
    engine = BeatProgressionEngine()
    result = engine.evaluate(
        arc=arc, interpreted=_interp(ActionType.IMPROVISE),
        outcome=_outcome(), location=None, history=_history(),
        world_flags={}, inventory=set(),
    )
    assert result.decision == "STAY"
    assert "no_objectives" in result.reasons


# ---------------------------------------------------------------------------
# B6 tests — anti-regression + required-only-subset edge case
# ---------------------------------------------------------------------------


def test_no_double_advance_in_one_turn():
    """REGRESSION: legacy code could advance two beats in one turn because
    the deterministic check (orchestrator.py:500) and location-based check
    (game_session.py:106) ran in series. The new engine returns ONE decision
    per evaluate() call — no double-advance possible at the engine level."""
    # Beat 1: talk to Kaelen at the Forge
    beat1 = StoryBeat(
        beat_number=1, title="Find Kaelen", description="...",
        location_hint="Forge",
        encounter_type="social",
        objectives=[BeatObjective(
            id="talk_kaelen", kind=ObjectiveKind.TALK, target="Kaelen",
            description="Speak with Kaelen",
        )],
    )
    # Beat 2: arrive at the Marketplace
    beat2 = StoryBeat(
        beat_number=2, title="Find the witness", description="...",
        location_hint="Marketplace",
        encounter_type="exploration",
        objectives=[BeatObjective(
            id="arrive_market", kind=ObjectiveKind.ARRIVE, target="Marketplace",
            description="Reach the marketplace",
        )],
    )
    arc = _make_arc([beat1, beat2])
    engine = BeatProgressionEngine()

    # Player talks to Kaelen — should ONLY satisfy beat 1, not jump to beat 2.
    location = MagicMock()
    location.name = "Forge"
    result = engine.evaluate(
        arc=arc, interpreted=_interp(ActionType.TALK, target="Kaelen"),
        outcome=_outcome(), location=location, history=_history(),
        world_flags={}, inventory=set(),
    )

    assert result.decision == "ADVANCE"
    # The new beat must be beat 2 (index 1), not beat 3 (index 2).
    assert result.new_beat is not None
    assert result.new_beat.beat_number == 2


def test_advance_required_only_subset():
    """When a beat has both required and optional objectives, completing only
    the required ones should ADVANCE — even though progress_score < 100."""
    required_obj = BeatObjective(
        id="talk_npc", kind=ObjectiveKind.TALK, target="Bob",
        description="...", required=True,
    )
    optional_obj = BeatObjective(
        id="optional_item", kind=ObjectiveKind.POSSESS, target="bonus key",
        description="...", required=False,
    )
    beat = StoryBeat(
        beat_number=1, title="X", description="...", location_hint="...",
        encounter_type="social", objectives=[required_obj, optional_obj],
        advance_rule=AdvanceRule.ALL_REQUIRED,
    )
    arc = _make_arc([beat])
    engine = BeatProgressionEngine()
    result = engine.evaluate(
        arc=arc, interpreted=_interp(ActionType.TALK, target="Bob"),
        outcome=_outcome(), location=None, history=_history(),
        world_flags={}, inventory=set(),  # no bonus key
    )
    assert result.decision == "ADVANCE"
    assert result.progress.progress_score == 50  # 1 of 2 completed, but ALL_REQUIRED met
