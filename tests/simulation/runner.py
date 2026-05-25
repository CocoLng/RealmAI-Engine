"""SimulationRunner — orchestrates the autonomous playthrough loop."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.simulation.records import AgentIntent, TurnOutcome, TurnRecord
from tests.simulation.recorder import Recorder
from tests.simulation.state_diff import state_diff as _compute_diff


def _state_view(snapshot: dict[str, Any], turn: int) -> SimpleNamespace:
    """Wrap a session_snapshot dict in an object that satisfies rule attribute access.

    The rules in tests/simulation/rules/ duck-type the state — they expect an object
    with `.npcs`, `.combat_active`, etc. The snapshot is a flat JSON-friendly dict
    (so state_diff can compare two of them); this bridge reconstructs the few
    object-shaped accessors the rules expect (`current_location.name`,
    `combat_state.zones`, `inventory.items`, `npc.status`).
    """
    hp = snapshot.get("character_hp")
    max_hp = snapshot.get("character_max_hp")
    ratio = (hp / max_hp) if (isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)) and max_hp) else 1.0

    # NPCs come from snapshot as {name: {status, hp, disposition}} dicts.
    # Rules duck-type attribute access (npc.name, npc.status, npc.hp), so wrap each.
    raw_npcs = snapshot.get("npcs", {}) or {}
    npcs_view: dict[str, SimpleNamespace] = {}
    for name, data in raw_npcs.items():
        if isinstance(data, dict):
            npcs_view[name] = SimpleNamespace(
                name=name,
                status=data.get("status", "alive"),
                hp=data.get("hp", 10),
                disposition=data.get("disposition", "neutral"),
            )
        else:
            # Already an object — keep as is.
            npcs_view[name] = data

    # Current location wrapper — rules read `.name` only.
    current_loc_name = snapshot.get("location")
    current_location_view = (
        SimpleNamespace(name=current_loc_name) if current_loc_name else None
    )

    # Combat state wrapper — rules read `.zones` (list of strings).
    combat_active = bool(snapshot.get("combat_active", False))
    combat_state_view: SimpleNamespace | None = None
    if combat_active:
        combat_state_view = SimpleNamespace(
            zones=list(snapshot.get("combat_zones", []) or []),
        )

    # Inventory wrapper — rules read `.items` (list of strings).
    inventory_view = SimpleNamespace(
        items=list(snapshot.get("inventory_items", []) or []),
    )

    return SimpleNamespace(
        npcs=npcs_view,
        current_location=current_location_view,
        combat_active=combat_active,
        combat_state=combat_state_view,
        inventory=inventory_view,
        player_names=snapshot.get("player_names", []) or [],
        player_hp=hp if hp is not None else 0,
        player_max_hp=max_hp if max_hp is not None else 0,
        player_hp_ratio=ratio,
        locations_known=snapshot.get("locations_known", []) or [],
        factions_known=snapshot.get("factions_known", []) or [],
        current_turn=turn,
    )

logger = logging.getLogger(__name__)


@dataclass
class SimulationConfig:
    seed: int
    max_turns: int = 30
    run_dir: Path = field(default_factory=lambda: Path("tests/simulation/runs/default"))
    max_wall_time_s: int = 600
    alert_budget: int = 5
    policy: str = "balanced"


class SimulationRunner:
    """Drives the loop: agent.decide → driver.execute → checker.check → recorder.append."""

    def __init__(
        self,
        *,
        config: SimulationConfig,
        agent: Any,
        driver: Any,
        checker: Any,
        session_snapshot: Callable[[], dict],
    ) -> None:
        self.config = config
        self.agent = agent
        self.driver = driver
        self.checker = checker
        self.session_snapshot = session_snapshot
        self.recorder = Recorder(run_dir=config.run_dir)
        self._history: list[dict[str, Any]] = []
        self._wait_streak = 0
        self._hard_alert_count = 0

    async def run(self) -> str:
        """Run the loop until a stop criterion fires. Returns the outcome status."""
        start_wall = time.perf_counter()
        outcome_status: str | None = None
        for turn in range(1, self.config.max_turns + 1):
            if time.perf_counter() - start_wall > self.config.max_wall_time_s:
                outcome_status = "wall_time_exceeded"
                break

            observation = self._build_observation(turn)
            intent: AgentIntent = (
                self.agent.decide(observation, history=self._history)
                if self._agent_accepts_history()
                else self.agent.decide(observation)
            )

            state_before = self.session_snapshot()
            outcome: TurnOutcome = await self.driver.execute(intent)
            state_after = self.session_snapshot()
            diff: dict[str, list[Any]] = _compute_diff(state_before, state_after)
            state_view = _state_view(state_after, turn=turn)
            alerts = self.checker.check(
                outcome.narration, state_view, diff=diff, history=self._history
            )
            self._hard_alert_count += sum(1 for a in alerts if a.severity == "hard")

            record = TurnRecord(
                turn=turn,
                ts=datetime.now(timezone.utc).isoformat(),
                observation=observation,
                intent=intent,
                outcome=outcome,
                diff=diff,
                alerts=alerts,
                agent_retries=0,
            )
            self.recorder.append(record)
            self._history.append(
                {
                    "intent_action": intent.action,
                    "intent_args": intent.args,
                    "narration": outcome.narration,
                }
            )

            if outcome.error is not None:
                outcome_status = "pipeline_error"
                break

            if intent.action == "wait":
                self._wait_streak += 1
                if self._wait_streak >= 3:
                    outcome_status = "agent_stuck"
                    break
            else:
                self._wait_streak = 0

            if self._hard_alert_count >= self.config.alert_budget:
                outcome_status = "alert_budget_exceeded"
                break

        if outcome_status is None:
            outcome_status = "max_turns_reached"

        wall_s = time.perf_counter() - start_wall
        self.recorder.finalize(
            outcome_status=outcome_status,
            wall_time_s=wall_s,
            config={
                "seed": self.config.seed,
                "max_turns": self.config.max_turns,
                "policy": self.config.policy,
                "alert_budget": self.config.alert_budget,
                "max_wall_time_s": self.config.max_wall_time_s,
            },
            final_state=self.session_snapshot(),
        )
        return outcome_status

    def _agent_accepts_history(self) -> bool:
        """True iff agent.decide accepts a history kwarg (anti-deadlock)."""
        import inspect

        try:
            sig = inspect.signature(self.agent.decide)
            return "history" in sig.parameters
        except (TypeError, ValueError):
            return False

    def _build_observation(self, turn: int) -> str:
        """Default observation builder — subclasses or callers can override.

        For the E2E mocked test we don't need a real one; the agent is mocked.
        """
        return f"TURN {turn}\n(observation placeholder — wire build_observation in CLI)"
