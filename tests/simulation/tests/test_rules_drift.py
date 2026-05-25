"""Tests for tests/simulation/rules/drift.py — R3.* informational drifts."""

from __future__ import annotations

from dataclasses import dataclass

from tests.simulation.rules.drift import (
    check_condition_phantom,
    check_disposition_silent_change,
    check_quest_silent_progress,
)


@dataclass
class FakeState:
    current_turn: int = 0


class TestR3DispositionSilentChange:
    def test_disposition_change_no_intent_triggers(self) -> None:
        diff = {"npc.Garm.disposition": ["friendly", "hostile"]}
        intent_action = "look"
        alerts = check_disposition_silent_change(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": intent_action}]
        )
        assert len(alerts) == 1
        assert alerts[0].rule == "R3.disposition_silent_change"

    def test_disposition_change_with_talk_no_trigger(self) -> None:
        diff = {"npc.Garm.disposition": ["friendly", "hostile"]}
        alerts = check_disposition_silent_change(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": "talk"}]
        )
        assert alerts == []


class TestR3QuestSilentProgress:
    def test_quest_progress_no_action_triggers(self) -> None:
        diff = {"quests.main.completed_objectives": [0, 1]}
        alerts = check_quest_silent_progress(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": "wait"}]
        )
        assert len(alerts) == 1
        assert alerts[0].rule == "R3.quest_silent_progress"

    def test_quest_progress_with_relevant_action_no_trigger(self) -> None:
        diff = {"quests.main.completed_objectives": [0, 1]}
        alerts = check_quest_silent_progress(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": "talk"}]
        )
        assert alerts == []


class TestR3ConditionPhantom:
    def test_condition_added_no_action_triggers(self) -> None:
        diff = {"character.conditions": [[], ["poisoned"]]}
        alerts = check_condition_phantom(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": "wait"}]
        )
        assert len(alerts) == 1
        assert alerts[0].rule == "R3.condition_phantom"

    def test_condition_added_with_action_no_trigger(self) -> None:
        diff = {"character.conditions": [[], ["poisoned"]]}
        alerts = check_condition_phantom(
            narration="", state=FakeState(), diff=diff, history=[{"intent_action": "attack"}]
        )
        assert alerts == []
