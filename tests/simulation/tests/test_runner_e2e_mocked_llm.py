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
