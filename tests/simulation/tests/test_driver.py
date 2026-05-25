"""Tests for tests/simulation/driver.py — GameDriver."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.simulation.driver import GameDriver
from tests.simulation.records import AgentIntent


@pytest.mark.asyncio
async def test_driver_routes_look_to_runner(scenario_ai) -> None:
    await scenario_ai.start_campaign(theme="Test", players=1)
    scenario_ai.look = AsyncMock(return_value=None)
    driver = GameDriver(scenario_runner=scenario_ai)
    intent = AgentIntent(reasoning="r", action="look", args={}, raw_text=None)
    outcome = await driver.execute(intent)
    scenario_ai.look.assert_awaited_once()
    assert outcome.error is None


@pytest.mark.asyncio
async def test_driver_routes_attack(scenario_ai) -> None:
    await scenario_ai.start_campaign(theme="Test", players=1)
    scenario_ai.attack = AsyncMock(return_value=None)
    driver = GameDriver(scenario_runner=scenario_ai)
    intent = AgentIntent(
        reasoning="r", action="attack", args={"target": "Gobelin"}, raw_text=None
    )
    outcome = await driver.execute(intent)
    scenario_ai.attack.assert_awaited_once_with(target="Gobelin", player_idx=0)
    assert outcome.error is None


@pytest.mark.asyncio
async def test_driver_captures_error(scenario_ai) -> None:
    await scenario_ai.start_campaign(theme="Test", players=1)
    scenario_ai.look = AsyncMock(side_effect=RuntimeError("boom"))
    driver = GameDriver(scenario_runner=scenario_ai)
    intent = AgentIntent(reasoning="r", action="look", args={}, raw_text=None)
    outcome = await driver.execute(intent)
    assert outcome.error is not None
    assert "boom" in outcome.error


@pytest.mark.asyncio
async def test_driver_records_timing(scenario_ai) -> None:
    await scenario_ai.start_campaign(theme="Test", players=1)
    scenario_ai.look = AsyncMock(return_value=None)
    driver = GameDriver(scenario_runner=scenario_ai)
    intent = AgentIntent(reasoning="r", action="look", args={}, raw_text=None)
    outcome = await driver.execute(intent)
    assert outcome.timing_ms.engine >= 0


@pytest.mark.asyncio
async def test_driver_routes_free_form_through_action_pipeline(scenario_ai) -> None:
    """The free_form action should route through ScenarioRunner.free_form_action
    which in turn invokes the ActionPipeline (interpreter → engine → narrator)."""
    await scenario_ai.start_campaign(theme="Test", players=1)
    # Wire a fake free_form_action method — the test only cares that the dispatch
    # path reaches it. Once the real method exists, this monkey-patch is unneeded
    # to test the routing; but the test below also verifies the real method
    # runs without raising.
    scenario_ai.free_form_action = AsyncMock(return_value=None)

    driver = GameDriver(scenario_runner=scenario_ai)
    intent = AgentIntent(
        reasoning="r",
        action="free_form",
        args={},
        raw_text="je fouille le coffre",
    )
    outcome = await driver.execute(intent)
    scenario_ai.free_form_action.assert_awaited_once()
    call_kwargs = scenario_ai.free_form_action.call_args.kwargs
    # The driver should pass the raw_text and player_idx
    assert call_kwargs.get("text") == "je fouille le coffre"
    assert call_kwargs.get("player_idx") == 0
    assert outcome.error is None


@pytest.mark.asyncio
async def test_scenario_runner_has_free_form_action_method(scenario_ai) -> None:
    """The method must exist on ScenarioRunner (not just on instances mocked in tests)."""
    assert hasattr(scenario_ai, "free_form_action")
    assert callable(scenario_ai.free_form_action)


@pytest.mark.asyncio
async def test_free_form_action_invokes_action_pipeline(scenario_ai) -> None:
    """When called, free_form_action builds an ActionPipeline and runs process()."""
    from unittest.mock import MagicMock, patch

    await scenario_ai.start_campaign(theme="Test", players=1)
    await scenario_ai.add_player("Aria", race="Elf", class_="Wizard", player_idx=0)

    # Patch ActionPipeline so we don't actually need a working interpreter/narrator
    fake_pipeline = MagicMock()
    fake_pipeline.process = AsyncMock(return_value=MagicMock(spec=[]))
    with patch(
        "tests.scenarios.scenario_runner.ActionPipeline", return_value=fake_pipeline
    ) as ctor_mock:
        await scenario_ai.free_form_action(text="je fouille le coffre", player_idx=0)

    # Constructor was called with kwargs including interpreter/narrator
    ctor_mock.assert_called_once()
    kwargs = ctor_mock.call_args.kwargs
    assert "interpreter" in kwargs
    assert "narrator" in kwargs
    assert kwargs["actor_name"] == "Aria"
    # process() was awaited with the raw text
    fake_pipeline.process.assert_awaited_once()
    args, kwargs2 = fake_pipeline.process.call_args
    assert (args and args[0] == "je fouille le coffre") or kwargs2.get(
        "player_text"
    ) == "je fouille le coffre"
