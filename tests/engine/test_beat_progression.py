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
    assert state.last_attempt_score == 0.0
    assert state.completed_at_turn is None


def test_objective_state_ignores_legacy_action_id_key():
    """Arcs persisted before 2026-07-19 may carry the removed
    ``last_attempt_action_id`` key — loading them must not raise."""
    state = ObjectiveState.model_validate(
        {"status": "pending", "last_attempt_action_id": "act-42"},
    )
    assert state.status == "pending"
    assert not hasattr(state, "last_attempt_action_id")


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
    # H16 contract change: a full match while the beat stays blocked is no
    # longer a silent STAY — the judge gets a chance to unlock the beat.
    # The beat itself must still NOT advance.
    assert result.decision == "NEEDS_JUDGE"
    assert result.new_beat is None
    assert result.progress.last_action_advanced is False
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


# ---------------------------------------------------------------------------
# H16 — persistent objective state across turns
# ---------------------------------------------------------------------------


def _search_examine_beat() -> StoryBeat:
    """M_OF_N(2) beat needing SEARCH + EXAMINE — one action can never match
    both matchers, so without persisted completions it is mechanically
    insatisfiable (the audit's soft-lock)."""
    return StoryBeat(
        beat_number=1, title="Le Rituel", description="...", location_hint="...",
        encounter_type="puzzle",
        objectives=[
            BeatObjective(
                id="search_altar", kind=ObjectiveKind.SEARCH,
                target="autel", description="Fouiller l'autel",
            ),
            BeatObjective(
                id="examine_runes", kind=ObjectiveKind.EXAMINE,
                target="gravures", description="Examiner les gravures",
            ),
        ],
        advance_rule=AdvanceRule.M_OF_N, advance_threshold=2,
    )


class TestPersistentObjectiveStateH16:
    def test_prior_completions_merge_from_beat(self):
        """Objectives recorded in beat.objectives_completed stay completed
        on later turns, whatever the current action is."""
        beat = _search_examine_beat()
        beat.objectives_completed = {"search_altar": 3}
        arc = _make_arc([beat])
        engine = BeatProgressionEngine()
        result = engine.evaluate(
            arc=arc, interpreted=_interp(ActionType.TALK, target="Bob"),
            outcome=_outcome(), location=None, history=_history(),
            world_flags={}, inventory=set(),
        )
        st = result.progress.objective_states["search_altar"]
        assert st.status == "completed"
        assert st.completed_at_turn == 3
        assert "search_altar:already_completed" in result.reasons

    def test_completed_at_turn_stamped_on_new_completion(self):
        beat = _search_examine_beat()
        arc = _make_arc([beat])
        engine = BeatProgressionEngine()
        result = engine.evaluate(
            arc=arc, interpreted=_interp(ActionType.SEARCH, target="autel"),
            outcome=_outcome(), location=None, history=_history(),
            world_flags={}, inventory=set(), turn_number=7,
        )
        st = result.progress.objective_states["search_altar"]
        assert st.status == "completed"
        assert st.completed_at_turn == 7

    def test_m_of_n_accumulates_across_turns(self):
        """SEARCH on turn 1, EXAMINE on turn 2 → threshold 2 reached.
        The write-back between turns mirrors what the orchestrator does."""
        beat = _search_examine_beat()
        arc = _make_arc([beat])
        engine = BeatProgressionEngine()

        r1 = engine.evaluate(
            arc=arc, interpreted=_interp(ActionType.SEARCH, target="autel"),
            outcome=_outcome(), location=None, history=_history(),
            world_flags={}, inventory=set(), turn_number=1,
        )
        assert r1.decision != "ADVANCE"
        assert r1.progress.objective_states["search_altar"].status == "completed"

        # Orchestrator-style write-back of new completions.
        beat.objectives_completed.update({
            oid: st.completed_at_turn
            for oid, st in r1.progress.objective_states.items()
            if st.status == "completed"
        })

        r2 = engine.evaluate(
            arc=arc, interpreted=_interp(ActionType.LOOK, target="gravures"),
            outcome=_outcome(), location=None, history=_history(),
            world_flags={}, inventory=set(), turn_number=2,
        )
        assert r2.decision == "ADVANCE"

    def test_full_match_blocked_goes_to_judge(self):
        """An objective fully completed this turn while the beat stays
        blocked must produce NEEDS_JUDGE (escape hatch), with the remaining
        objectives as candidates."""
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
        result = engine.evaluate(
            arc=arc, interpreted=_interp(ActionType.TALK, target="Bob"),
            outcome=_outcome(), location=None, history=_history(),
            world_flags={}, inventory=set(), turn_number=1,
        )
        assert result.decision == "NEEDS_JUDGE"
        assert "full_match_blocked_judge" in result.reasons
        assert result.judge_request is not None
        assert {pm.id for pm in result.judge_request.objectives} == {"get_item"}

    def test_flag_without_writer_autocompletes(self):
        """A FLAG objective whose flag is set by no beat effect and absent
        from world state is mechanically unsatisfiable → auto-completed."""
        beat = StoryBeat(
            beat_number=1, title="X", description="...", location_hint="...",
            encounter_type="puzzle",
            objectives=[BeatObjective(
                id="ritual_flag", kind=ObjectiveKind.FLAG,
                target="ritual_complete", description="...",
            )],
        )
        arc = _make_arc([beat])
        engine = BeatProgressionEngine()
        result = engine.evaluate(
            arc=arc, interpreted=_interp(ActionType.LOOK, target="autel"),
            outcome=_outcome(), location=None, history=_history(),
            world_flags={}, inventory=set(),
        )
        assert result.decision == "ADVANCE"
        assert "ritual_flag:flag_no_writer_auto" in result.reasons

    def test_flag_with_writer_stays_pending(self):
        """A FLAG objective IS satisfiable when some beat effect sets the
        flag — no auto-completion."""
        from world.story_arc import BeatEffects

        beat1 = StoryBeat(
            beat_number=1, title="X", description="...", location_hint="...",
            encounter_type="puzzle",
            objectives=[BeatObjective(
                id="door_flag", kind=ObjectiveKind.FLAG,
                target="door_open", description="...",
            )],
        )
        beat2 = StoryBeat(
            beat_number=2, title="Y", description="...", location_hint="...",
            encounter_type="exploration",
            on_complete=BeatEffects(state_flags={"door_open": True}),
        )
        arc = _make_arc([beat1, beat2])
        engine = BeatProgressionEngine()
        result = engine.evaluate(
            arc=arc, interpreted=_interp(ActionType.LOOK, target="autel"),
            outcome=_outcome(), location=None, history=_history(),
            world_flags={}, inventory=set(),
        )
        assert result.decision == "STAY"
        assert result.progress.objective_states["door_flag"].status == "pending"

    def test_flag_present_but_falsy_in_world_stays_pending(self):
        """A flag initialized (falsy) in world state has a live mechanism —
        no auto-completion either."""
        beat = StoryBeat(
            beat_number=1, title="X", description="...", location_hint="...",
            encounter_type="puzzle",
            objectives=[BeatObjective(
                id="lever_flag", kind=ObjectiveKind.FLAG,
                target="lever_pulled", description="...",
            )],
        )
        arc = _make_arc([beat])
        engine = BeatProgressionEngine()
        result = engine.evaluate(
            arc=arc, interpreted=_interp(ActionType.LOOK, target="autel"),
            outcome=_outcome(), location=None, history=_history(),
            world_flags={"lever_pulled": False}, inventory=set(),
        )
        assert result.decision == "STAY"
        assert result.progress.objective_states["lever_flag"].status == "pending"
