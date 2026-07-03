"""Coordination primitives for background LLM generation (chantier I / H8).

Two prefetchers generate content in the background (``bot/npc_prefetch.py``
for NPC sheets, ``bot/location_prefetch.py`` for neighbor locations). Ollama
serves one request at a time on the 18 GB budget, so uncoordinated
background jobs would stack in its queue in front of player actions. This
module enforces two invariants:

- **one background generation in flight process-wide** — a player action
  queued behind background work waits for at most a single LLM call
  (~60 s worst case);
- **background jobs never start while a player action is in flight** —
  :func:`wait_player_idle` parks the job until ``session.action_lock`` is
  free (event-driven acquire/release, no polling).
"""

from __future__ import annotations

import asyncio
from typing import Any

_gate: asyncio.Lock | None = None


def generation_gate() -> asyncio.Lock:
    """Process-wide background-generation gate (lazy singleton).

    Lazy so the lock binds to the running event loop on first use; tests
    (one fresh loop per test) call :func:`reset_generation_gate` between
    runs.
    """
    global _gate
    if _gate is None:
        _gate = asyncio.Lock()
    return _gate


def reset_generation_gate() -> None:
    """Test hook — drop the singleton so the next caller gets a fresh lock."""
    global _gate
    _gate = None


async def wait_player_idle(session: Any) -> None:
    """Return once no player action is in flight on ``session``.

    Acquire/release of ``session.action_lock``: event-driven, held for a
    no-op instant so any player queued behind us pays nothing. Sessions
    without an ``action_lock`` (test doubles, pre-launch contexts) are
    considered idle.
    """
    lock = getattr(session, "action_lock", None)
    if lock is None:
        return
    async with lock:
        pass
