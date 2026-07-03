"""Tests for bot/prefetch_gate.py — background-generation coordination (H8)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot.prefetch_gate import (
    generation_gate,
    reset_generation_gate,
    wait_player_idle,
)


@pytest.fixture(autouse=True)
def _fresh_gate():
    reset_generation_gate()
    yield
    reset_generation_gate()


async def test_generation_gate_is_a_singleton() -> None:
    assert generation_gate() is generation_gate()


async def test_reset_generation_gate_drops_the_singleton() -> None:
    first = generation_gate()
    reset_generation_gate()
    assert generation_gate() is not first


async def test_gate_serializes_two_holders() -> None:
    order: list[str] = []

    async def hold(tag: str, delay: float) -> None:
        async with generation_gate():
            order.append(f"{tag}:in")
            await asyncio.sleep(delay)
            order.append(f"{tag}:out")

    await asyncio.wait_for(
        asyncio.gather(hold("a", 0.01), hold("b", 0.0)), timeout=5,
    )
    assert order == ["a:in", "a:out", "b:in", "b:out"]


async def test_wait_player_idle_without_action_lock_is_immediate() -> None:
    await asyncio.wait_for(wait_player_idle(SimpleNamespace()), timeout=1)
    await asyncio.wait_for(
        wait_player_idle(SimpleNamespace(action_lock=None)), timeout=1,
    )


async def test_wait_player_idle_waits_for_action_to_finish() -> None:
    session = SimpleNamespace(action_lock=asyncio.Lock())
    await session.action_lock.acquire()
    waiter = asyncio.create_task(wait_player_idle(session))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not waiter.done()
    session.action_lock.release()
    await asyncio.wait_for(waiter, timeout=5)


async def test_wait_player_idle_releases_the_lock_after_itself() -> None:
    session = SimpleNamespace(action_lock=asyncio.Lock())
    await asyncio.wait_for(wait_player_idle(session), timeout=1)
    assert not session.action_lock.locked()
