"""Tests for tests/simulation/agent.py — AutonomousAgent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.simulation.agent import build_observation


@dataclass
class FakeCharacter:
    name: str
    race: str
    char_class: str
    level: int
    hp: int
    max_hp: int
    ac: int


@dataclass
class FakeLocation:
    name: str
    exits: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeCombatant:
    name: str
    hp: int
    max_hp: int
    ac: int
    zone: str = "front"


@dataclass
class FakeCombatState:
    is_active: bool = True
    enemies: list[FakeCombatant] = field(default_factory=list)


@dataclass
class FakeSessionLike:
    character: FakeCharacter
    location: FakeLocation
    inventory_items: list[str]
    equipped: dict[str, str]
    combat_active: bool = False
    combat: FakeCombatState | None = None
    npcs_present: list[str] = field(default_factory=list)


class TestBuildObservation:
    def test_exploration_observation(self) -> None:
        sess = FakeSessionLike(
            character=FakeCharacter("Aria", "Elf", "Wizard", 1, 15, 15, 13),
            location=FakeLocation("Cave entrance", {"north": "Cave deep"}),
            inventory_items=["Healing potion (x2)", "Old tome"],
            equipped={"main_hand": "Quarterstaff"},
        )
        obs = build_observation(
            turn=3,
            session=sess,
            last_actions=["look", "move(north)", "look"],
            last_narration="L'air est froid.",
        )
        assert "TURN 3" in obs
        assert "Aria" in obs
        assert "HP 15/15" in obs
        assert "Cave entrance" in obs
        assert "north" in obs
        assert "Quarterstaff" in obs
        assert "Healing potion" in obs
        assert "not in combat" in obs.lower()
        assert "look, move(north), look" in obs

    def test_combat_observation_shows_enemies(self) -> None:
        sess = FakeSessionLike(
            character=FakeCharacter("Aria", "Elf", "Wizard", 1, 12, 15, 13),
            location=FakeLocation("Cave", {}),
            inventory_items=[],
            equipped={},
            combat_active=True,
            combat=FakeCombatState(
                is_active=True,
                enemies=[
                    FakeCombatant("Goblin_1", 4, 4, 12),
                    FakeCombatant("Goblin_2", 1, 4, 12),
                ],
            ),
        )
        obs = build_observation(
            turn=5,
            session=sess,
            last_actions=["attack(Goblin_2)", "attack(Goblin_1)", "look"],
            last_narration="Le gobelin chancelle.",
        )
        assert "IN COMBAT" in obs
        assert "Goblin_1" in obs and "HP 4/4" in obs
        assert "Goblin_2" in obs and "HP 1/4" in obs
        assert "BLOODIED" in obs  # Goblin_2 is below 50% HP

    def test_observation_under_token_budget(self) -> None:
        sess = FakeSessionLike(
            character=FakeCharacter("Aria", "Elf", "Wizard", 1, 15, 15, 13),
            location=FakeLocation("Cave", {"north": "deep", "south": "exit"}),
            inventory_items=[f"Item_{i}" for i in range(20)],
            equipped={},
        )
        obs = build_observation(
            turn=1,
            session=sess,
            last_actions=[],
            last_narration="",
        )
        assert len(obs.split()) < 600


from unittest.mock import MagicMock

from tests.simulation.agent import AutonomousAgent


class TestAutonomousAgentDecide:
    def test_valid_intent_parsed(self) -> None:
        # Mock the LLM client to return a valid intent JSON.
        client = MagicMock()
        client.chat_json.return_value = {
            "reasoning": "look around first",
            "action": "look",
            "args": {},
            "raw_text": None,
        }
        agent = AutonomousAgent(client=client, model="qwen3.5:4b")
        intent = agent.decide(observation="TURN 1\n...")
        assert intent.action == "look"
        assert intent.reasoning == "look around first"
        client.chat_json.assert_called_once()

    def test_invalid_intent_retries_then_returns(self) -> None:
        client = MagicMock()
        # First two calls return garbage, third returns valid.
        client.chat_json.side_effect = [
            {"reasoning": "x", "action": "dance"},  # invalid action
            {"reasoning": "y", "action": "free_form"},  # missing raw_text
            {"reasoning": "z", "action": "look", "args": {}, "raw_text": None},
        ]
        agent = AutonomousAgent(client=client, model="qwen3.5:4b", max_retries=3)
        intent = agent.decide(observation="TURN 1\n...")
        assert intent.action == "look"
        assert client.chat_json.call_count == 3

    def test_exhausted_retries_falls_back_to_safe_default(self) -> None:
        client = MagicMock()
        client.chat_json.return_value = {"action": "dance"}  # always invalid
        agent = AutonomousAgent(client=client, model="qwen3.5:4b", max_retries=2)
        intent = agent.decide(observation="TURN 1\nCombat: not in combat")
        assert intent.action == "look"  # safe default out of combat
        assert intent.reasoning.startswith("fallback")

    def test_exhausted_retries_in_combat_falls_back_to_defend(self) -> None:
        client = MagicMock()
        client.chat_json.return_value = {"action": "dance"}
        agent = AutonomousAgent(client=client, model="qwen3.5:4b", max_retries=2)
        intent = agent.decide(observation="TURN 1\nCombat: IN COMBAT")
        assert intent.action == "defend"
