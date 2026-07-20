"""End-to-end test of SimulationRunner with a fully mocked LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.simulation.records import AgentIntent
from tests.simulation.runner import SimulationConfig, SimulationRunner


@pytest.fixture()
def mock_agent() -> Any:
    """An agent that always returns 'look'."""
    agent = MagicMock()
    agent.decide.return_value = AgentIntent(
        reasoning="test",
        action="look",
        args={},
        raw_text=None,
    )
    return agent


@pytest.fixture()
def mock_checker() -> Any:
    checker = MagicMock()
    checker.check.return_value = []
    return checker


@pytest.fixture()
def stub_driver() -> Any:
    """A driver whose execute returns a TurnOutcome with no error."""
    from tests.simulation.records import LLMTimings, TurnOutcome

    driver = MagicMock()
    driver.execute = AsyncMock(
        return_value=TurnOutcome(
            narration="Vous voyez une grotte.",
            action_resolved={"type": "look"},
            error=None,
            timing_ms=LLMTimings(agent=10, interpreter=20, engine=5, narrator=30),
        )
    )
    return driver


@pytest.mark.asyncio
async def test_runner_executes_max_turns(
    tmp_path: Path, mock_agent, mock_checker, stub_driver
) -> None:
    config = SimulationConfig(
        seed=42, max_turns=3, run_dir=tmp_path, max_wall_time_s=60
    )
    runner = SimulationRunner(
        config=config,
        agent=mock_agent,
        driver=stub_driver,
        checker=mock_checker,
        session_snapshot=lambda: {"hp": 15, "location": "Cave"},
    )
    status = await runner.run()
    assert status == "max_turns_reached"
    assert stub_driver.execute.await_count == 3
    transcript = (tmp_path / "transcript.jsonl").read_text().splitlines()
    assert len(transcript) == 3
    assert (tmp_path / "report.md").exists()


@pytest.mark.asyncio
async def test_runner_stops_on_pipeline_error(
    tmp_path: Path, mock_agent, mock_checker
) -> None:
    from tests.simulation.records import LLMTimings, TurnOutcome

    driver = MagicMock()
    driver.execute = AsyncMock(
        return_value=TurnOutcome(
            narration="",
            action_resolved={},
            error="RuntimeError: boom",
            timing_ms=LLMTimings(agent=0, interpreter=0, engine=0, narrator=0),
        )
    )
    config = SimulationConfig(seed=1, max_turns=10, run_dir=tmp_path, max_wall_time_s=60)
    runner = SimulationRunner(
        config=config,
        agent=mock_agent,
        driver=driver,
        checker=mock_checker,
        session_snapshot=lambda: {},
    )
    status = await runner.run()
    assert status == "pipeline_error"
    assert driver.execute.await_count == 1


@pytest.mark.asyncio
async def test_runner_stops_on_alert_budget(
    tmp_path: Path, mock_agent, stub_driver
) -> None:
    from tests.simulation.records import IncoherenceAlert

    checker = MagicMock()
    alert = IncoherenceAlert(
        severity="hard",
        category="x",
        turn=0,
        rule="R1.test",
        narration_snippet="s",
        expected="e",
    )
    checker.check.return_value = [alert] * 5  # 5 alerts on the first turn

    config = SimulationConfig(
        seed=1, max_turns=10, run_dir=tmp_path, alert_budget=5, max_wall_time_s=60
    )
    runner = SimulationRunner(
        config=config,
        agent=mock_agent,
        driver=stub_driver,
        checker=checker,
        session_snapshot=lambda: {},
    )
    status = await runner.run()
    assert status == "alert_budget_exceeded"


@pytest.mark.asyncio
async def test_runner_stops_on_agent_stuck(
    tmp_path: Path, mock_checker, stub_driver
) -> None:
    agent = MagicMock()
    agent.decide.return_value = AgentIntent(
        reasoning="stuck", action="wait", args={}, raw_text=None
    )
    config = SimulationConfig(
        seed=1, max_turns=10, run_dir=tmp_path, max_wall_time_s=60
    )
    runner = SimulationRunner(
        config=config,
        agent=agent,
        driver=stub_driver,
        checker=mock_checker,
        session_snapshot=lambda: {},
    )
    status = await runner.run()
    assert status == "agent_stuck"
    assert stub_driver.execute.await_count == 3  # 3 waits then exit


@pytest.mark.asyncio
async def test_runner_stops_on_character_death(
    tmp_path: Path, mock_agent, mock_checker, stub_driver
) -> None:
    """A run must stop the moment the character drops to 0 HP (spec §5)."""
    snapshots = [
        {"character_hp": 5, "character_max_hp": 15},   # before turn 1
        {"character_hp": 0, "character_max_hp": 15},   # after turn 1 — dead
        {"character_hp": 0, "character_max_hp": 15},   # finalize
    ]
    snapshot_iter = iter(snapshots)

    config = SimulationConfig(
        seed=1, max_turns=10, run_dir=tmp_path, max_wall_time_s=60
    )
    runner = SimulationRunner(
        config=config,
        agent=mock_agent,
        driver=stub_driver,
        checker=mock_checker,
        session_snapshot=lambda: next(snapshot_iter),
    )
    status = await runner.run()
    assert status == "character_death"
    assert stub_driver.execute.await_count == 1
    assert "character_death" in (tmp_path / "report.md").read_text()
    assert '"character_hp": 0' in (tmp_path / "final_state.json").read_text()


@pytest.mark.asyncio
async def test_runner_does_not_stop_when_character_alive(
    tmp_path: Path, mock_agent, mock_checker, stub_driver
) -> None:
    """Positive HP — and snapshots without any HP field — must not stop the run."""
    config = SimulationConfig(
        seed=1, max_turns=2, run_dir=tmp_path, max_wall_time_s=60
    )
    runner = SimulationRunner(
        config=config,
        agent=mock_agent,
        driver=stub_driver,
        checker=mock_checker,
        session_snapshot=lambda: {"character_hp": 1, "location": "Cave"},
    )
    assert await runner.run() == "max_turns_reached"


@pytest.mark.asyncio
async def test_runner_plumbs_locked_facts_into_history(
    tmp_path: Path, mock_agent, mock_checker, stub_driver
) -> None:
    """Locked facts from the snapshot must reach the history the rules read."""
    facts = [{"id": "npc_dead:Garm", "text": "Garm est mort(e)."}]
    config = SimulationConfig(
        seed=1, max_turns=2, run_dir=tmp_path, max_wall_time_s=60
    )
    runner = SimulationRunner(
        config=config,
        agent=mock_agent,
        driver=stub_driver,
        checker=mock_checker,
        session_snapshot=lambda: {"character_hp": 10, "locked_facts": facts},
    )
    await runner.run()
    assert runner._history[-1]["locked_facts"] == facts
    # Turn 2's check must see turn 1's facts.
    history_seen = mock_checker.check.call_args_list[1].kwargs["history"]
    assert history_seen[-1]["locked_facts"] == facts


@pytest.mark.asyncio
async def test_runner_records_agent_retries(
    tmp_path: Path, mock_checker, stub_driver
) -> None:
    """The transcript must carry the agent's real retry count, not a hardcoded 0."""

    class _RetryingAgent:
        last_retries = 0

        def decide(self, observation: str, history=None) -> AgentIntent:
            self.last_retries = 2
            return AgentIntent(
                reasoning="test", action="look", args={}, raw_text=None
            )

    config = SimulationConfig(
        seed=1, max_turns=1, run_dir=tmp_path, max_wall_time_s=60
    )
    runner = SimulationRunner(
        config=config,
        agent=_RetryingAgent(),
        driver=stub_driver,
        checker=mock_checker,
        session_snapshot=lambda: {"character_hp": 10},
    )
    await runner.run()
    assert runner.recorder.records[0].agent_retries == 2


@pytest.mark.asyncio
async def test_runner_computes_real_diff(
    tmp_path: Path, mock_agent, mock_checker
) -> None:
    """The runner must call session_snapshot before AND after each turn
    and pass the computed diff to checker.check."""
    from tests.simulation.records import LLMTimings, TurnOutcome

    driver = MagicMock()
    driver.execute = AsyncMock(
        return_value=TurnOutcome(
            narration="ok",
            action_resolved={},
            error=None,
            timing_ms=LLMTimings(agent=1, interpreter=1, engine=1, narrator=1),
        )
    )

    # session_snapshot returns different state across calls
    snapshots = [
        {"character": {"hp": 15}},  # before turn 1
        {"character": {"hp": 12}},  # after turn 1
        {"character": {"hp": 12}},  # before turn 2 (== after turn 1)
        {"character": {"hp": 10}},  # after turn 2
        {"character": {"hp": 10}},  # final snapshot for finalize
    ]
    snapshot_iter = iter(snapshots)
    def snapshot():
        return next(snapshot_iter)

    config = SimulationConfig(seed=1, max_turns=2, run_dir=tmp_path, max_wall_time_s=60)
    runner = SimulationRunner(
        config=config,
        agent=mock_agent,
        driver=driver,
        checker=mock_checker,
        session_snapshot=snapshot,
    )
    await runner.run()
    # checker.check was called twice — once per turn — with a non-empty diff
    assert mock_checker.check.call_count == 2
    first_diff = mock_checker.check.call_args_list[0].kwargs.get("diff", {})
    assert first_diff == {"character.hp": [15, 12]}, (
        f"expected non-empty diff for turn 1, got {first_diff}"
    )
