"""Tests for new objective primitives and legacy migration."""

import pytest

from world.story_arc import (
    AdvanceRule,
    BeatObjective,
    CompletionTrigger,
    GateKind,
    ObjectiveGate,
    ObjectiveKind,
    StoryArc,
    StoryBeat,
)


def test_objective_kind_enum_values():
    assert ObjectiveKind.TALK.value == "talk"
    assert ObjectiveKind.DEFEAT.value == "defeat"
    assert ObjectiveKind.ARRIVE.value == "arrive"
    assert ObjectiveKind.EXAMINE.value == "examine"
    assert ObjectiveKind.POSSESS.value == "possess"
    assert ObjectiveKind.FLAG.value == "flag"


def test_gate_kind_enum_values():
    assert GateKind.MIN_REVEALS.value == "min_reveals"
    assert GateKind.MIN_DISPOSITION.value == "min_disposition"
    assert GateKind.HAS_ITEM.value == "has_item"
    assert GateKind.FLAG_SET.value == "flag_set"


def test_advance_rule_values():
    assert AdvanceRule.ALL_REQUIRED.value == "all_required"
    assert AdvanceRule.ANY.value == "any"
    assert AdvanceRule.M_OF_N.value == "m_of_n"


def test_objective_gate_construct():
    gate = ObjectiveGate(kind=GateKind.MIN_REVEALS, value=1)
    assert gate.kind == GateKind.MIN_REVEALS
    assert gate.value == 1


def test_beat_objective_defaults():
    obj = BeatObjective(
        id="talk_kaelen",
        kind=ObjectiveKind.TALK,
        target="Kaelen",
        description="Speak with Kaelen at the forge",
    )
    assert obj.required is True
    assert obj.fuzzy_threshold == 0.7
    assert obj.gate is None


def test_story_beat_new_fields_defaults():
    beat = StoryBeat(
        beat_number=1,
        title="The hook",
        description="Players meet the patron at the inn.",
        location_hint="The Inn of the Rusty Anchor",
        encounter_type="social",
    )
    assert beat.objectives == []
    assert beat.advance_rule == AdvanceRule.ALL_REQUIRED
    assert beat.advance_threshold is None
    assert beat.player_visible_hint is None
    assert beat.judge_rubric is None


def test_story_beat_with_objectives():
    objectives = [
        BeatObjective(
            id="talk_patron",
            kind=ObjectiveKind.TALK,
            target="patron",
            description="Speak with the patron",
        ),
        BeatObjective(
            id="accept_offer",
            kind=ObjectiveKind.FLAG,
            target="patron_offer_accepted",
            description="Accept the contract",
        ),
    ]
    beat = StoryBeat(
        beat_number=1,
        title="The hook",
        description="Players meet the patron at the inn.",
        location_hint="The Inn of the Rusty Anchor",
        encounter_type="social",
        objectives=objectives,
        advance_rule=AdvanceRule.ALL_REQUIRED,
        player_visible_hint="The patron seems eager to talk.",
        judge_rubric="Accept any creative way to commit to the contract.",
    )
    assert len(beat.objectives) == 2
    assert beat.player_visible_hint == "The patron seems eager to talk."


def _make_filler_beats(start: int, count: int) -> list[StoryBeat]:
    """Helper: build `count` minimal exploration beats starting at beat_number `start`."""
    return [
        StoryBeat(
            beat_number=start + i,
            title=f"Beat {start + i}",
            description="...",
            location_hint="...",
            encounter_type="exploration",
        )
        for i in range(count)
    ]


def test_legacy_completion_trigger_auto_migrated():
    """A beat with only completion_trigger should get one auto-generated BeatObjective."""
    legacy_beat = StoryBeat(
        beat_number=1,
        title="Talk to Kaelen",
        description="Find Kaelen at the forge.",
        location_hint="Forge",
        encounter_type="social",
        completion_trigger=CompletionTrigger(type="talk", target="Kaelen"),
    )
    arc = StoryArc(
        campaign_id="abc",
        theme="mystery",
        premise="Investigation in the docks district.",
        beats=[legacy_beat] + _make_filler_beats(2, 7),
        villain_name="The Strangler",
        villain_motivation="Revenge",
    )

    migrated = arc.beats[0]
    assert len(migrated.objectives) == 1
    obj = migrated.objectives[0]
    assert obj.kind == ObjectiveKind.TALK
    assert obj.target == "Kaelen"
    assert obj.id == "legacy_talk_Kaelen"
    assert obj.required is True
    # Original trigger is preserved for read-back compat:
    assert migrated.completion_trigger is not None
    # TALK gets an automatic MIN_REVEALS=2 gate so a single "hello" doesn't
    # advance the beat — the conversation must actually reveal something.
    from world.story_arc import GateKind
    assert obj.gate is not None
    assert obj.gate.kind == GateKind.MIN_REVEALS
    assert obj.gate.value == 2


def test_legacy_non_talk_kinds_have_no_default_gate() -> None:
    """defeat/arrive/pickup/etc. are self-validating — no gate by default."""
    beat = StoryBeat(
        beat_number=1, title="Defeat the wolf", description="...",
        location_hint="forest", encounter_type="combat",
        completion_trigger=CompletionTrigger(type="defeat", target="wolf"),
    )
    arc = StoryArc(
        campaign_id="abc", theme="t",
        premise="A long enough premise here.",
        beats=[beat] + _make_filler_beats(2, 7),
        villain_name="X", villain_motivation="Y",
    )
    obj = arc.beats[0].objectives[0]
    assert obj.kind == ObjectiveKind.DEFEAT
    assert obj.gate is None  # killing is self-validating


def test_legacy_talk_with_min_reveals_gate_blocks_until_substance() -> None:
    """REGRESSION (live log 2026-04-27): player said hello to the right NPC
    and the beat advanced after a single revealed_info. The new default gate
    requires at least 2 reveals so a substantive conversation is necessary."""
    from ai.models import InterpretedAction, MechanicsOutcome
    from engine.beat_progression import BeatHistory, BeatProgressionEngine
    from engine.validators import ActionType

    beat = StoryBeat(
        beat_number=1,
        title="Greet the Ambassador",
        description="...",
        location_hint="court",
        encounter_type="social",
        completion_trigger=CompletionTrigger(type="talk", target="Ambassadeur Vaelen"),
    )
    arc = StoryArc(
        campaign_id="abc", theme="t",
        premise="A long enough premise here.",
        beats=[beat] + _make_filler_beats(2, 7),
        villain_name="X", villain_motivation="Y",
    )

    engine = BeatProgressionEngine()

    # Single reveal from the NPC — must NOT advance (gate fails).
    one_reveal = engine.evaluate(
        arc=arc,
        interpreted=InterpretedAction(
            action_type=ActionType.TALK, actor_name="hero",
            target_name="Ambassadeur Vaelen",
            raw_input="je m'approche de l'ambassadeur",
        ),
        outcome=MechanicsOutcome(summary="Vaelen nods.", talk_reveals_count=1),
        location=None, history=BeatHistory(),
        world_flags={}, inventory=set(),
    )
    assert one_reveal.decision == "NEEDS_JUDGE"  # partial match, gate failed

    # Two reveals — gate passes, beat advances.
    two_reveals = engine.evaluate(
        arc=arc,
        interpreted=InterpretedAction(
            action_type=ActionType.TALK, actor_name="hero",
            target_name="Ambassadeur Vaelen",
            raw_input="je discute longuement avec l'ambassadeur",
        ),
        outcome=MechanicsOutcome(summary="Vaelen reveals two things.", talk_reveals_count=2),
        location=None, history=BeatHistory(),
        world_flags={}, inventory=set(),
    )
    assert two_reveals.decision == "ADVANCE"


def test_legacy_migration_skipped_when_objectives_present():
    """A beat with explicit objectives should NOT auto-migrate."""
    explicit_obj = BeatObjective(
        id="custom",
        kind=ObjectiveKind.TALK,
        target="Other",
        description="...",
    )
    beat = StoryBeat(
        beat_number=1,
        title="Beat 1",
        description="...",
        location_hint="...",
        encounter_type="social",
        objectives=[explicit_obj],
        completion_trigger=CompletionTrigger(type="talk", target="Kaelen"),
    )
    arc = StoryArc(
        campaign_id="abc",
        theme="mystery",
        premise="A long enough premise here.",
        beats=[beat] + _make_filler_beats(2, 7),
        villain_name="X",
        villain_motivation="Y",
    )
    # Only the explicit objective should be present.
    assert len(arc.beats[0].objectives) == 1
    assert arc.beats[0].objectives[0].id == "custom"


@pytest.mark.parametrize("legacy_type", ["interact", "defeat", "talk", "arrive", "search", "pickup"])
def test_all_legacy_trigger_types_migrate(legacy_type: str) -> None:
    """All 6 CompletionTrigger.type values should map to ObjectiveKind."""
    beat = StoryBeat(
        beat_number=1,
        title="X",
        description="...",
        location_hint="...",
        encounter_type="exploration",
        completion_trigger=CompletionTrigger(type=legacy_type, target="something"),
    )
    arc = StoryArc(
        campaign_id="abc",
        theme="t",
        premise="A long enough premise here.",
        beats=[beat] + _make_filler_beats(2, 7),
        villain_name="X",
        villain_motivation="Y",
    )
    assert len(arc.beats[0].objectives) == 1
    assert arc.beats[0].objectives[0].kind.value == legacy_type
    assert arc.beats[0].objectives[0].target == "something"
