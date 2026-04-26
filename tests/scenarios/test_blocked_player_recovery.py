"""End-to-end scenarios validating that the new system unblocks players."""

from unittest.mock import MagicMock

from ai.models import InterpretedAction, MechanicsOutcome
from engine.beat_progression import (
    BeatHistory,
    BeatProgressionEngine,
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


def _make_arc(beats):
    while len(beats) < 8:
        beats.append(StoryBeat(
            beat_number=len(beats) + 1,
            title=f"Filler {len(beats) + 1}",
            description="...",
            location_hint="...",
            encounter_type="exploration",
        ))
    return StoryArc(
        campaign_id="c", theme="t", premise="A long enough premise.",
        beats=beats, villain_name="X", villain_motivation="Y",
    )


def test_blocked_by_min_reveals_gate_engine_returns_needs_judge():
    """Player talks to NPC but gets no reveals — engine must NOT advance silently
    and must emit NEEDS_JUDGE so /hint or BeatJudge can intervene."""
    obj = BeatObjective(
        id="talk_kaelen", kind=ObjectiveKind.TALK, target="Kaelen",
        description="Get info from Kaelen",
        gate=ObjectiveGate(kind=GateKind.MIN_REVEALS, value=1),
    )
    beat = StoryBeat(
        beat_number=1, title="Interrogate Kaelen", description="...",
        location_hint="Forge", encounter_type="social", objectives=[obj],
    )
    arc = _make_arc([beat])
    engine = BeatProgressionEngine()
    result = engine.evaluate(
        arc=arc,
        interpreted=InterpretedAction(
            action_type=ActionType.TALK, actor_name="hero",
            target_name="Kaelen", raw_input="I greet Kaelen",
        ),
        outcome=MechanicsOutcome(summary="Kaelen nods.", talk_reveals_count=0),
        location=None, history=BeatHistory(),
        world_flags={}, inventory=set(),
    )
    assert result.decision == "NEEDS_JUDGE"
    assert result.judge_request is not None
    assert result.progress.progress_score == 0


def test_engine_no_double_advance_when_two_objectives_match_simultaneously():
    """Two objectives on the same beat both match in one action — beat advances
    EXACTLY ONCE (one advance, not two)."""
    obj_talk = BeatObjective(
        id="talk_npc", kind=ObjectiveKind.TALK, target="Bob", description="...",
    )
    obj_arrive = BeatObjective(
        id="arrive_x", kind=ObjectiveKind.ARRIVE, target="The Inn", description="...",
    )
    beat1 = StoryBeat(
        beat_number=1, title="Beat 1", description="...", location_hint="The Inn",
        encounter_type="social", objectives=[obj_talk, obj_arrive],
        advance_rule=AdvanceRule.ALL_REQUIRED,
    )
    beat2 = StoryBeat(
        beat_number=2, title="Beat 2", description="...", location_hint="...",
        encounter_type="exploration",
        objectives=[BeatObjective(
            id="other", kind=ObjectiveKind.TALK, target="Carol", description="...",
        )],
    )
    arc = _make_arc([beat1, beat2])
    engine = BeatProgressionEngine()
    location = MagicMock()
    location.name = "The Inn"
    result = engine.evaluate(
        arc=arc,
        interpreted=InterpretedAction(
            action_type=ActionType.TALK, actor_name="hero", target_name="Bob",
            raw_input="I talk to Bob",
        ),
        outcome=MechanicsOutcome(summary="..."),
        location=location, history=BeatHistory(),
        world_flags={}, inventory=set(),
    )
    assert result.decision == "ADVANCE"
    assert result.new_beat is not None
    assert result.new_beat.beat_number == 2  # NOT 3
