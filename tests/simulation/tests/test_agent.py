"""Tests for tests/simulation/agent.py — AutonomousAgent."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

from tests.simulation.agent import AutonomousAgent, build_observation, is_legal
from tests.simulation.records import AgentIntent


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


class FakeStateForLegality:
    def __init__(self, **kwargs) -> None:
        self.combat_active = kwargs.get("combat_active", False)
        self.living_enemies = kwargs.get("living_enemies", [])
        self.location_exits = kwargs.get("location_exits", [])
        self.inventory_items = kwargs.get("inventory_items", [])
        self.consumable_items = kwargs.get("consumable_items", [])
        self.spellbook = kwargs.get("spellbook", [])
        self.mana = kwargs.get("mana", 10)


class TestIsLegal:
    def test_attack_legal_in_combat_with_valid_target(self) -> None:
        state = FakeStateForLegality(combat_active=True, living_enemies=["Goblin_1"])
        intent = AgentIntent(
            reasoning="r", action="attack", args={"target": "Goblin_1"}, raw_text=None
        )
        legal, reason = is_legal(intent, state)
        assert legal is True

    def test_attack_illegal_out_of_combat(self) -> None:
        state = FakeStateForLegality(combat_active=False)
        intent = AgentIntent(
            reasoning="r", action="attack", args={"target": "Goblin_1"}, raw_text=None
        )
        legal, reason = is_legal(intent, state)
        assert legal is False
        assert reason and "combat" in reason.lower()

    def test_move_illegal_in_combat(self) -> None:
        state = FakeStateForLegality(combat_active=True, location_exits=["north"])
        intent = AgentIntent(
            reasoning="r", action="move", args={"direction": "north"}, raw_text=None
        )
        legal, reason = is_legal(intent, state)
        assert legal is False

    def test_move_legal_with_valid_direction(self) -> None:
        state = FakeStateForLegality(combat_active=False, location_exits=["north"])
        intent = AgentIntent(
            reasoning="r", action="move", args={"direction": "north"}, raw_text=None
        )
        legal, reason = is_legal(intent, state)
        assert legal is True

    def test_use_item_requires_consumable(self) -> None:
        state = FakeStateForLegality(
            inventory_items=["Quarterstaff"], consumable_items=[]
        )
        intent = AgentIntent(
            reasoning="r",
            action="use_item",
            args={"item": "Quarterstaff"},
            raw_text=None,
        )
        legal, reason = is_legal(intent, state)
        assert legal is False

    def test_use_item_legal_consumable(self) -> None:
        state = FakeStateForLegality(
            inventory_items=["Healing potion"], consumable_items=["Healing potion"]
        )
        intent = AgentIntent(
            reasoning="r",
            action="use_item",
            args={"item": "Healing potion"},
            raw_text=None,
        )
        legal, reason = is_legal(intent, state)
        assert legal is True

    def test_free_form_legal_with_raw_text(self) -> None:
        state = FakeStateForLegality()
        intent = AgentIntent(
            reasoning="r", action="free_form", args={}, raw_text="je fouille le coffre"
        )
        legal, _ = is_legal(intent, state)
        assert legal is True

    def test_look_always_legal(self) -> None:
        state = FakeStateForLegality()
        intent = AgentIntent(reasoning="r", action="look", args={}, raw_text=None)
        legal, _ = is_legal(intent, state)
        assert legal is True


class TestPolicyAddendum:
    def test_balanced_policy_default(self) -> None:
        client = MagicMock()
        client.chat_json.return_value = {
            "reasoning": "r", "action": "look", "args": {}, "raw_text": None
        }
        agent = AutonomousAgent(client=client, model="qwen3.5:4b", policy="balanced")
        agent.decide(observation="TURN 1\n...")
        system_msg = client.chat_json.call_args.kwargs["messages"][0]["content"]
        # balanced is the default — its addendum should mention "balanced" or "mix"
        assert "balanced" in system_msg.lower() or "mix" in system_msg.lower()

    def test_combat_focused_policy_addendum(self) -> None:
        client = MagicMock()
        client.chat_json.return_value = {
            "reasoning": "r", "action": "look", "args": {}, "raw_text": None
        }
        agent = AutonomousAgent(
            client=client, model="qwen3.5:4b", policy="combat_focused"
        )
        agent.decide(observation="TURN 1\n...")
        system_msg = client.chat_json.call_args.kwargs["messages"][0]["content"]
        assert "combat" in system_msg.lower() and "engage" in system_msg.lower()

    def test_story_focused_policy_addendum(self) -> None:
        client = MagicMock()
        client.chat_json.return_value = {
            "reasoning": "r", "action": "look", "args": {}, "raw_text": None
        }
        agent = AutonomousAgent(
            client=client, model="qwen3.5:4b", policy="story_focused"
        )
        agent.decide(observation="TURN 1\n...")
        system_msg = client.chat_json.call_args.kwargs["messages"][0]["content"]
        assert "talk" in system_msg.lower() or "dialogue" in system_msg.lower() \
            or "narrative" in system_msg.lower()

    def test_unknown_policy_falls_back_to_balanced(self) -> None:
        client = MagicMock()
        client.chat_json.return_value = {
            "reasoning": "r", "action": "look", "args": {}, "raw_text": None
        }
        # Should not raise — falls back to balanced
        agent = AutonomousAgent(client=client, model="qwen3.5:4b", policy="garbage")
        agent.decide(observation="TURN 1\n...")
        # Still emits a valid system prompt
        system_msg = client.chat_json.call_args.kwargs["messages"][0]["content"]
        assert "JSON" in system_msg


class TestAntiDeadlockHint:
    def test_repeated_action_triggers_hint(self) -> None:
        client = MagicMock()
        client.chat_json.return_value = {
            "reasoning": "r",
            "action": "look",
            "args": {},
            "raw_text": None,
        }
        agent = AutonomousAgent(client=client, model="qwen3.5:4b")
        # Simulate 4 prior identical look intents → hint should be injected
        history = [{"intent_action": "look", "intent_args": {}}] * 4
        agent.decide(observation="TURN 5\n...", history=history)
        # Check that the messages included the corrective hint
        call_args = client.chat_json.call_args
        messages = (
            call_args.kwargs.get("messages")
            if call_args.kwargs
            else call_args.args[1]
        )
        last = messages[-1]["content"]
        assert (
            "repeating" in last.lower()
            or "vary" in last.lower()
            or "differ" in last.lower()
        )
