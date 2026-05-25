"""Tests for tests/simulation/checker.py — IncoherenceChecker aggregator."""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.simulation.checker import IncoherenceChecker
from tests.simulation.records import IncoherenceAlert
from tests.simulation.rules.hard import check_npc_status


@dataclass
class FakeNPC:
    name: str
    status: str = "alive"
    hp: int = 10


@dataclass
class FakeState:
    npcs: dict[str, FakeNPC] = field(default_factory=dict)
    current_location: object = None
    combat_active: bool = False
    combat_state: object = None
    inventory: object = None
    player_names: list[str] = field(default_factory=list)
    player_hp_ratio: float = 1.0
    player_max_hp: int = 15
    player_hp: int = 15
    current_turn: int = 0
    locations_known: list[str] = field(default_factory=list)
    factions_known: list[str] = field(default_factory=list)


class TestIncoherenceChecker:
    def test_aggregates_alerts_from_all_rules(self) -> None:
        checker = IncoherenceChecker()
        state = FakeState(npcs={"Garm": FakeNPC("Garm", status="dead", hp=0)})
        narration = "Garm sourit malicieusement."
        alerts = checker.check(narration, state, diff={}, history=[])
        assert any(a.rule == "R1.npc_status" for a in alerts)

    def test_no_alerts_for_clean_narration(self) -> None:
        checker = IncoherenceChecker()
        state = FakeState()
        narration = "Le héros avance prudemment."
        alerts = checker.check(narration, state, diff={}, history=[])
        # May still fire R2.unknown_proper_noun on a word; filter to hard only.
        hard = [a for a in alerts if a.severity == "hard"]
        assert hard == []

    def test_subset_of_rules(self) -> None:
        # Allow injecting a custom rule list (for tests).
        checker = IncoherenceChecker(rules=[check_npc_status])
        state = FakeState(npcs={"Garm": FakeNPC("Garm", status="dead", hp=0)})
        narration = "Garm sourit."
        alerts = checker.check(narration, state, diff={}, history=[])
        assert len(alerts) == 1
        assert alerts[0].rule == "R1.npc_status"

    def test_returns_typed_alerts(self) -> None:
        checker = IncoherenceChecker()
        state = FakeState()
        alerts = checker.check("test", state, diff={}, history=[])
        for a in alerts:
            assert isinstance(a, IncoherenceAlert)
