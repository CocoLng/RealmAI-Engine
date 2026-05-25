"""Tests for tests/simulation/rules/hard.py — R1.* deterministic checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.simulation.rules.hard import check_npc_status


@dataclass
class FakeNPC:
    name: str
    status: str = "alive"
    hp: int = 10


@dataclass
class FakeState:
    """Minimal stand-in for the bits of GameSession the rules need."""

    npcs: dict[str, FakeNPC] = field(default_factory=dict)
    current_location: Any = None
    combat_active: bool = False
    combat_state: Any = None
    inventory: Any = None
    player_hp_ratio: float = 1.0
    player_max_hp: int = 15
    player_hp: int = 15


class TestR1NpcStatus:
    def test_dead_npc_speaks_triggers(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm", status="dead", hp=0)})
        narration = "Garm sourit et tend la main vers le héros."
        alerts = check_npc_status(narration, state, diff={}, history=[])
        assert len(alerts) == 1
        a = alerts[0]
        assert a.rule == "R1.npc_status"
        assert a.severity == "hard"
        assert "Garm" in a.narration_snippet

    def test_alive_npc_speaks_does_not_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm", status="alive", hp=10)})
        narration = "Garm sourit et tend la main vers le héros."
        alerts = check_npc_status(narration, state, diff={}, history=[])
        assert alerts == []

    def test_dead_npc_not_mentioned_does_not_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm", status="dead", hp=0)})
        narration = "Le vent souffle dans les arbres."
        alerts = check_npc_status(narration, state, diff={}, history=[])
        assert alerts == []

    def test_dead_npc_mentioned_but_passive_no_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm", status="dead", hp=0)})
        narration = "Le corps de Garm gît au sol, sans vie."
        alerts = check_npc_status(narration, state, diff={}, history=[])
        assert alerts == []

    def test_hp_zero_treated_as_dead(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm", status="alive", hp=0)})
        narration = "Garm attaque !"
        alerts = check_npc_status(narration, state, diff={}, history=[])
        assert len(alerts) == 1
