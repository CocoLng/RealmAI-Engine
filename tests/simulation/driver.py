"""GameDriver — translates AgentIntent into ScenarioRunner cog calls.

Captures narration, action_resolved, and timing into a TurnOutcome.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from tests.simulation.records import AgentIntent, LLMTimings, TurnOutcome

logger = logging.getLogger(__name__)


class GameDriver:
    def __init__(self, *, scenario_runner: Any, player_idx: int = 0) -> None:
        self.runner = scenario_runner
        self.player_idx = player_idx

    async def execute(self, intent: AgentIntent) -> TurnOutcome:
        """Dispatch the intent to the appropriate ScenarioRunner method.

        Captures errors and timing; never re-raises (the SimulationRunner
        decides whether to bail).
        """
        start = time.perf_counter()
        error: str | None = None
        narration: str = ""
        action_resolved: dict[str, Any] = {"action": intent.action, "args": intent.args}
        try:
            await self._dispatch(intent)
            narration = self._extract_narration()
        except Exception as e:  # noqa: BLE001
            logger.exception("GameDriver dispatch failed: %s", e)
            error = f"{type(e).__name__}: {e}"
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return TurnOutcome(
            narration=narration,
            action_resolved=action_resolved,
            error=error,
            timing_ms=LLMTimings(
                agent=0,  # filled by SimulationRunner
                interpreter=0,
                engine=elapsed_ms,
                narrator=0,
            ),
        )

    async def _dispatch(self, intent: AgentIntent) -> None:
        action = intent.action
        args = intent.args
        idx = self.player_idx
        r = self.runner
        if action == "look":
            await r.look()
        elif action == "attack":
            await r.attack(target=args["target"], player_idx=idx)
        elif action == "cast_spell":
            await r.cast_spell(spell=args["spell"], target=args.get("target", ""), player_idx=idx)
        elif action == "defend":
            await r.defend(player_idx=idx)
        elif action == "flee":
            await r.flee(player_idx=idx)
        elif action == "move":
            await r.move(direction=args["direction"])
        elif action == "talk":
            await r.talk(npc=args["npc"])
        elif action == "search":
            await r.search(target=args.get("target", ""))
        elif action == "equip":
            await r.equip(item=args["item"], slot=args.get("slot", "main_hand"), player_idx=idx)
        elif action == "unequip":
            await r.unequip(slot=args["slot"], player_idx=idx)
        elif action == "use_item":
            await r.use_item(item=args["item"], player_idx=idx)
        elif action == "free_form":
            free_form = getattr(r, "free_form_action", None)
            if free_form is None:
                raise NotImplementedError(
                    "ScenarioRunner.free_form_action is not implemented yet"
                )
            await free_form(text=intent.raw_text or "", player_idx=idx)
        elif action == "wait":
            return  # no-op
        else:
            raise ValueError(f"Unknown action {action!r}")

    def _extract_narration(self) -> str:
        """Pull the narration text from the last captured embed/message."""
        last = self.runner.last_response
        if last is None:
            return ""
        if last.embed is not None and last.embed.description:
            return str(last.embed.description)
        if last.content:
            return str(last.content)
        return ""
