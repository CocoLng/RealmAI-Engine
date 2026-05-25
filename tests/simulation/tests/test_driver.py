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
