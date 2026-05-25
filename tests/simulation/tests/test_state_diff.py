"""Tests for tests/simulation/state_diff.py — recursive dict diffing."""

from __future__ import annotations

from tests.simulation.state_diff import state_diff


class TestStateDiffPrimitives:
    def test_no_change_returns_empty(self) -> None:
        before = {"hp": 15, "location": "Cave"}
        after = {"hp": 15, "location": "Cave"}
        assert state_diff(before, after) == {}

    def test_primitive_change_detected(self) -> None:
        before = {"hp": 15}
        after = {"hp": 12}
        assert state_diff(before, after) == {"hp": [15, 12]}

    def test_string_change_detected(self) -> None:
        before = {"location": "Cave entrance"}
        after = {"location": "Cave deep"}
        assert state_diff(before, after) == {"location": ["Cave entrance", "Cave deep"]}


class TestStateDiffNested:
    def test_nested_dict_uses_dotted_path(self) -> None:
        before = {"character": {"hp": 15, "ac": 13}}
        after = {"character": {"hp": 12, "ac": 13}}
        assert state_diff(before, after) == {"character.hp": [15, 12]}

    def test_deeply_nested(self) -> None:
        before = {"npc": {"Garm": {"disposition": "friendly"}}}
        after = {"npc": {"Garm": {"disposition": "hostile"}}}
        assert state_diff(before, after) == {
            "npc.Garm.disposition": ["friendly", "hostile"]
        }


class TestStateDiffLists:
    def test_list_change_is_leaf(self) -> None:
        before = {"inventory": ["sword", "potion"]}
        after = {"inventory": ["sword", "potion", "scroll"]}
        # list compared as a whole value
        assert state_diff(before, after) == {
            "inventory": [["sword", "potion"], ["sword", "potion", "scroll"]]
        }


class TestStateDiffKeyChanges:
    def test_key_added(self) -> None:
        before = {"a": 1}
        after = {"a": 1, "b": 2}
        assert state_diff(before, after) == {"b": [None, 2]}

    def test_key_removed(self) -> None:
        before = {"a": 1, "b": 2}
        after = {"a": 1}
        assert state_diff(before, after) == {"b": [2, None]}


class TestStateDiffPrefix:
    def test_prefix_prepended(self) -> None:
        before = {"hp": 15}
        after = {"hp": 12}
        assert state_diff(before, after, prefix="player") == {"player.hp": [15, 12]}
