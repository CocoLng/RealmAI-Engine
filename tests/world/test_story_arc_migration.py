"""Tests for new objective primitives and legacy migration."""

from world.story_arc import (
    AdvanceRule,
    BeatObjective,
    GateKind,
    ObjectiveGate,
    ObjectiveKind,
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
