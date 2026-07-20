"""Tests for the CLI snapshot builder (tests/simulation/__main__.py)."""

from __future__ import annotations

from types import SimpleNamespace

from tests.simulation.__main__ import _snapshot_from_session
from world.story_arc import LockedFact


def _fake_session(*, locked_facts: list[LockedFact] | None = None) -> SimpleNamespace:
    arc = SimpleNamespace(
        beats=[],
        villain_name="Malakar",
        locked_facts=locked_facts if locked_facts is not None else [],
    )
    return SimpleNamespace(
        campaign=SimpleNamespace(id="camp-1"),
        characters={},
        npcs={},
        inventories={},
        combat_state=None,
        current_location=None,
        story_arc=arc,
    )


class TestLockedFactsPlumbing:
    def test_locked_facts_surfaced_as_dicts(self) -> None:
        session = _fake_session(
            locked_facts=[LockedFact(id="npc_dead:Garm", text="Garm est mort(e).")]
        )
        snap = _snapshot_from_session(session)
        assert snap["locked_facts"] == [
            {"id": "npc_dead:Garm", "text": "Garm est mort(e)."}
        ]

    def test_empty_when_arc_has_no_facts(self) -> None:
        assert _snapshot_from_session(_fake_session())["locked_facts"] == []

    def test_empty_when_no_arc(self) -> None:
        session = _fake_session()
        session.story_arc = None
        assert _snapshot_from_session(session)["locked_facts"] == []
