"""AutonomousAgent — observes game state and chooses actions via the 4b LLM."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tests.simulation.records import AgentIntent

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    return (_PROMPTS_DIR / "agent_system.txt").read_text(encoding="utf-8")


def _load_few_shots() -> dict[str, list[dict]]:
    return json.loads((_PROMPTS_DIR / "few_shots.json").read_text(encoding="utf-8"))


class AutonomousAgent:
    """Calls the 4b LLM to decide a single AgentIntent per turn.

    On invalid JSON or invalid AgentIntent, retries up to ``max_retries`` times,
    then falls back to a safe default ('look' out of combat, 'defend' in combat).
    """

    def __init__(
        self,
        *,
        client: Any,
        model: str = "qwen3.5:4b",
        max_retries: int = 3,
        temperature: float = 0.3,
    ) -> None:
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self.temperature = temperature
        self._system_prompt = _load_system_prompt()
        self._few_shots = _load_few_shots()

    def _build_messages(
        self,
        observation: str,
        corrective_hint: str | None = None,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
        ]
        # One few-shot per context — exploration as a baseline; LLM generalizes.
        for ex in self._few_shots.get("exploration", [])[:1]:
            messages.append({"role": "user", "content": ex["observation"]})
            messages.append(
                {"role": "assistant", "content": json.dumps(ex["intent"])}
            )
        for ex in self._few_shots.get("combat", [])[:1]:
            messages.append({"role": "user", "content": ex["observation"]})
            messages.append(
                {"role": "assistant", "content": json.dumps(ex["intent"])}
            )
        user_content = observation
        if corrective_hint:
            user_content = f"{observation}\n\n[CORRECTION] {corrective_hint}"
        messages.append({"role": "user", "content": user_content})
        return messages

    def decide(self, observation: str) -> AgentIntent:
        """Return a valid AgentIntent, retrying or falling back as needed."""
        hint: str | None = None
        last_err: str | None = None
        for attempt in range(self.max_retries):
            messages = self._build_messages(observation, corrective_hint=hint)
            try:
                raw = self.client.chat_json(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM call failed attempt=%d: %s", attempt, e)
                hint = "Previous response could not be parsed. Return strict JSON."
                last_err = str(e)
                continue
            try:
                if isinstance(raw, str):
                    raw = json.loads(raw)
                intent = AgentIntent.model_validate(raw)
                return intent
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                last_err = str(e)
                hint = (
                    "Your previous response was invalid. Return EXACTLY one JSON "
                    "object matching the schema. Errors: " + last_err[:200]
                )
                continue
        logger.warning(
            "Agent exhausted %d retries (last_err=%s) — falling back",
            self.max_retries,
            last_err,
        )
        return self._safe_fallback(observation, reason=last_err or "exhausted_retries")

    @staticmethod
    def _safe_fallback(observation: str, *, reason: str) -> AgentIntent:
        in_combat = "IN COMBAT" in observation
        if in_combat:
            return AgentIntent(
                reasoning=f"fallback: {reason[:100]}",
                action="defend",
                args={},
                raw_text=None,
            )
        return AgentIntent(
            reasoning=f"fallback: {reason[:100]}",
            action="look",
            args={},
            raw_text=None,
        )


def build_observation(
    *,
    turn: int,
    session: Any,
    last_actions: list[str],
    last_narration: str,
) -> str:
    """Build a compact text observation for the agent prompt.

    Pulls from the session-like object: character, location, inventory, combat.
    """
    char = session.character
    loc = session.location
    lines: list[str] = []
    lines.append(f"TURN {turn}")
    lines.append(
        f"You play: {char.name} ({char.race}, {char.char_class}, lvl {char.level}, "
        f"HP {char.hp}/{char.max_hp}, AC {char.ac})"
    )
    exits_str = (
        ", ".join(f"{d} → {tgt}" for d, tgt in loc.exits.items()) if loc.exits else "none"
    )
    lines.append(f"Location: {loc.name}. Exits: {exits_str}")

    equipped = getattr(session, "equipped", {}) or {}
    if equipped:
        equipped_str = ", ".join(f"{slot}: {item}" for slot, item in equipped.items())
        lines.append(f"Equipped: {equipped_str}")

    inv = getattr(session, "inventory_items", []) or []
    if inv:
        lines.append("Inventory: " + ", ".join(inv[:15]))

    if session.combat_active and session.combat is not None:
        lines.append("Combat: IN COMBAT, your turn")
        for enemy in session.combat.enemies:
            ratio = enemy.hp / enemy.max_hp if enemy.max_hp else 1.0
            bloodied = " (BLOODIED)" if ratio < 0.5 else ""
            lines.append(
                f"  - {enemy.name}: HP {enemy.hp}/{enemy.max_hp} AC {enemy.ac} "
                f"zone \"{enemy.zone}\"{bloodied}"
            )
    else:
        lines.append("Combat: not in combat")

    npcs = getattr(session, "npcs_present", []) or []
    if npcs:
        lines.append("NPCs present: " + ", ".join(npcs))
    else:
        lines.append("NPCs present: none")

    if last_actions:
        lines.append("Last 3 turns: " + ", ".join(last_actions[-3:]))
    else:
        lines.append("Last 3 turns: -")

    if last_narration:
        snippet = last_narration.strip().replace("\n", " ")[:200]
        lines.append(f'Last narration: "{snippet}"')

    return "\n".join(lines)
