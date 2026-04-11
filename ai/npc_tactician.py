"""NPC Tactician — LLM-driven tactical brain for boss NPCs (task 52).

Uses Qwen 3.5 4B (the fast model) to decide a boss NPC's action on its
turn. The LLM gets the full stat block, the combat state, the party
context, and the last few mechanical events. It outputs a structured
:class:`~ai.models.TacticalDecision` JSON — the engine validates the
decision against the actual state and rolls every die.

The tactician never touches dice, never applies damage, never mutates
state. It is a pure intent classifier with a richer input than the
scripted elite brain.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ai.client import LLMParseError, OllamaClient
from ai.language import language_instruction
from ai.models import TacticalDecision
from engine.combat import Combatant, CombatState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "system_npc_tactician.txt"
).read_text()


class NPCTactician:
    """Decide a boss NPC's turn via a single LLM call.

    The engine remains the sole arbiter of randomness. This class only
    produces an intent (attack X, use signature Y, move to Z) that the
    engine validates and resolves.
    """

    MODEL = "qwen3.5:4b"

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def decide(
        self,
        boss: Combatant,
        state: CombatState,
        party_context: str,
        recent_events: list[str],
        language: str = "fr",
    ) -> TacticalDecision:
        """Ask the LLM what the boss should do on its current turn.

        Args:
            boss: The boss combatant whose turn is active.
            state: The full combat state (for target enumeration).
            party_context: A short narrative string about the party
                and location (supplied by the caller).
            recent_events: The last few mechanical events (summaries),
                used to let the boss react to what just happened.
            language: ISO 639-1 language code for the reasoning style.

        Returns:
            A validated :class:`~ai.models.TacticalDecision`.

        Raises:
            ValueError: If the LLM output cannot be parsed or references
                entities that do not exist in the provided state / stat
                block. The caller (``decide_boss_action``) uses this to
                drive retries and the scripted fallback.
        """
        user_content = self._build_context(
            boss, state, party_context, recent_events,
        )
        messages = [
            {
                "role": "system",
                "content": language_instruction(language) + _SYSTEM_PROMPT,
            },
            {"role": "user", "content": user_content},
        ]

        try:
            data = self._client.chat_json(
                self.MODEL, messages, temperature=0.7, think=False,
            )
        except (json.JSONDecodeError, LLMParseError) as exc:
            raise ValueError(f"Tactician LLM returned invalid JSON: {exc}") from exc

        try:
            decision = TacticalDecision.model_validate(data)
        except Exception as exc:  # pydantic.ValidationError subclasses Exception
            raise ValueError(f"Tactician output failed schema validation: {exc}") from exc

        self._validate_references(decision, boss, state)
        return decision

    # ------------------------------------------------------------------
    # Post-validation
    # ------------------------------------------------------------------

    def _validate_references(
        self,
        decision: TacticalDecision,
        boss: Combatant,
        state: CombatState,
    ) -> None:
        """Ensure the decision references real combatants, weapons, zones.

        Raises ``ValueError`` on any dangling reference — the caller
        uses it to trigger a retry or fallback.
        """
        if decision.target_name is not None:
            known = {c.name for c in state.combatants}
            if decision.target_name not in known:
                raise ValueError(
                    f"Tactician targeted unknown combatant {decision.target_name!r}"
                )

        if decision.signature_name is not None:
            if boss.stat_block is None:
                raise ValueError(
                    "Tactician referenced a signature but boss has no stat block"
                )
            sig_names = {s.name for s in boss.stat_block.signature_abilities}
            if decision.signature_name not in sig_names:
                raise ValueError(
                    f"Tactician referenced unknown signature "
                    f"{decision.signature_name!r}"
                )

        if decision.weapon_name is not None:
            if boss.stat_block is None:
                raise ValueError(
                    "Tactician referenced a weapon but boss has no stat block"
                )
            atk_names = {a.name for a in boss.stat_block.attacks}
            if decision.weapon_name not in atk_names:
                raise ValueError(
                    f"Tactician referenced unknown attack {decision.weapon_name!r}"
                )

    # ------------------------------------------------------------------
    # Prompt context
    # ------------------------------------------------------------------

    def _build_context(
        self,
        boss: Combatant,
        state: CombatState,
        party_context: str,
        recent_events: list[str],
    ) -> str:
        """Assemble the user-message payload for the tactician prompt."""
        sb = boss.stat_block
        lines: list[str] = [
            f"# You are {boss.name}",
            f"HP: {boss.character.hp}/{boss.character.max_hp} | AC: {boss.character.ac}",
            f"Current zone: {boss.current_zone or 'unzoned'}",
        ]

        if sb is not None:
            lines.append("")
            lines.append("## Your attacks")
            for atk in sb.attacks:
                lines.append(
                    f"- {atk.name} ({atk.damage_dice} {atk.damage_type.value}, "
                    f"{atk.range_type}, +{atk.to_hit_bonus} to hit)"
                )
            lines.append("")
            lines.append("## Your signature abilities")
            if not sb.signature_abilities:
                lines.append("- (none)")
            for sig in sb.signature_abilities:
                if sig.usage == "at_will":
                    budget = "(at will)"
                elif sig.uses_remaining is None:
                    budget = "(unlimited)"
                else:
                    budget = f"(uses left: {sig.uses_remaining})"
                lines.append(f"- {sig.name} {budget}: {sig.description}")

        lines.append("")
        lines.append("## Current combat state")
        for c in state.combatants:
            if c.name == boss.name:
                continue
            if not c.is_alive:
                status = "dead"
            elif c.fled:
                status = "fled"
            else:
                status = f"{c.character.hp}/{c.character.max_hp} HP (AC {c.character.ac})"
            side = "ENEMY" if c.side != boss.side else "ALLY"
            zone = c.current_zone or "unzoned"
            lines.append(f"- {side} {c.name}: {status}, zone={zone}")

        lines.append("")
        lines.append(f"## Round {state.round_number}")

        if party_context:
            lines.append("")
            lines.append("## Party context")
            lines.append(party_context)

        if recent_events:
            lines.append("")
            lines.append("## Recent events")
            for ev in recent_events[-3:]:
                lines.append(f"- {ev}")

        lines.append("")
        lines.append("## Your job")
        lines.append(
            "Decide your next action. Return ONLY a JSON object matching "
            "the TacticalDecision schema. Do not roll dice — the engine "
            "handles that. Just pick what you want to do and why."
        )
        return "\n".join(lines)
