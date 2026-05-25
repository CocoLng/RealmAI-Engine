"""Tests for tests/simulation/rules/hard.py — R1.* deterministic checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.simulation.rules.hard import check_hp_mismatch, check_item_use_without_owning, check_npc_status, check_phantom_npc


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
    player_names: list[str] = field(default_factory=list)
    player_hp_ratio: float = 1.0
    player_max_hp: int = 15
    player_hp: int = 15
    current_turn: int = 0


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


class TestR1PhantomNpc:
    def test_unknown_proper_noun_triggers(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm")})
        narration = "Le héros rencontre Khaalim, un sorcier inconnu."
        alerts = check_phantom_npc(narration, state, diff={}, history=[])
        assert any(a.rule == "R1.phantom_npc" and "Khaalim" in a.expected for a in alerts)

    def test_known_npc_does_not_trigger(self) -> None:
        state = FakeState(npcs={"Garm": FakeNPC(name="Garm")})
        narration = "Garm s'avance."
        alerts = check_phantom_npc(narration, state, diff={}, history=[])
        assert alerts == []

    def test_whitelist_words_ignored(self) -> None:
        # "Dieu", "Roi" etc. are common nouns capitalized; must not trigger
        state = FakeState(npcs={})
        narration = "Le Roi a parlé. Que les Dieux nous protègent."
        alerts = check_phantom_npc(narration, state, diff={}, history=[])
        assert alerts == []

    def test_player_name_not_phantom(self) -> None:
        state = FakeState(npcs={}, player_names=["Aria"])
        narration = "Aria avance prudemment."
        alerts = check_phantom_npc(narration, state, diff={}, history=[])
        assert alerts == []


@dataclass
class FakeInventory:
    items: list[str] = field(default_factory=list)


class TestR1ItemUseWithoutOwning:
    def test_uses_item_not_in_inventory_triggers(self) -> None:
        state = FakeState(inventory=FakeInventory(items=["Épée longue"]))
        narration = "Le héros boit la Potion de soin."
        alerts = check_item_use_without_owning(narration, state, diff={}, history=[])
        assert len(alerts) == 1
        assert alerts[0].rule == "R1.item_use_without_owning"
        assert "Potion de soin" in alerts[0].expected

    def test_uses_item_in_inventory_no_trigger(self) -> None:
        state = FakeState(inventory=FakeInventory(items=["Potion de soin"]))
        narration = "Le héros boit la Potion de soin."
        alerts = check_item_use_without_owning(narration, state, diff={}, history=[])
        assert alerts == []

    def test_no_inventory_no_trigger(self) -> None:
        state = FakeState(inventory=None)
        narration = "Le héros boit la Potion de soin."
        alerts = check_item_use_without_owning(narration, state, diff={}, history=[])
        assert alerts == []

    def test_passive_mention_no_trigger(self) -> None:
        state = FakeState(inventory=FakeInventory(items=["Épée longue"]))
        narration = "Une potion de soin trône sur l'étagère."
        alerts = check_item_use_without_owning(narration, state, diff={}, history=[])
        assert alerts == []


class TestR1HpMismatch:
    def test_wounded_narration_full_hp_triggers(self) -> None:
        state = FakeState(player_hp=15, player_max_hp=15, player_hp_ratio=1.0)
        narration = "Aria agonise au sol, grièvement blessée."
        alerts = check_hp_mismatch(narration, state, diff={}, history=[])
        assert len(alerts) == 1
        assert alerts[0].rule == "R1.hp_mismatch"

    def test_wounded_narration_low_hp_no_trigger(self) -> None:
        state = FakeState(player_hp=2, player_max_hp=15, player_hp_ratio=0.13)
        narration = "Aria agonise au sol."
        alerts = check_hp_mismatch(narration, state, diff={}, history=[])
        assert alerts == []

    def test_neutral_narration_no_trigger(self) -> None:
        state = FakeState(player_hp=15, player_max_hp=15, player_hp_ratio=1.0)
        narration = "Aria avance prudemment dans la grotte."
        alerts = check_hp_mismatch(narration, state, diff={}, history=[])
        assert alerts == []
