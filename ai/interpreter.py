"""Interpreter — converts player free text to structured InterpretedAction."""

import json
import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.models import InterpretedAction
from engine.validators import ActionType

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_interpreter.txt").read_text()


class Interpreter:
    """Interprets player free text as a structured game action.

    Uses Qwen 3.5 4B for fast, deterministic JSON classification.
    Never decides game mechanics — only classifies player intent.
    """

    MODEL = "qwen3.5:4b"

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def interpret(
        self,
        player_text: str,
        actor_name: str,
        available_actions: list[str],
        combat_context: str = "",
    ) -> InterpretedAction:
        """Classify player text as a structured action.

        Args:
            player_text: The raw player input.
            actor_name: Name of the acting character.
            available_actions: List of valid action types for current context.
            combat_context: Optional summary of current combat state.

        Returns:
            InterpretedAction with action_type, targets, and confidence.
            Returns Defend with confidence=0.0 if LLM response is unparseable.
        """
        user_content = self._build_user_message(
            player_text, actor_name, available_actions, combat_context
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            data = self._client.chat_json(self.MODEL, messages, temperature=0.3)
        except json.JSONDecodeError:
            logger.warning("Interpreter: LLM returned non-JSON for input: %r", player_text)
            return self._fallback(player_text)

        try:
            return InterpretedAction(
                action_type=ActionType(data.get("action_type", "Defend")),
                actor_name=actor_name,
                target_name=data.get("target_name"),
                weapon_name=data.get("weapon_name"),
                spell_name=data.get("spell_name"),
                item_name=data.get("item_name"),
                raw_input=player_text,
                confidence=float(data.get("confidence", 1.0)),
            )
        except (ValueError, KeyError) as exc:
            logger.warning("Interpreter: Failed to parse action data: %s", exc)
            return self._fallback(player_text)

    def _fallback(self, player_text: str) -> InterpretedAction:
        """Return a safe fallback action when parsing fails."""
        return InterpretedAction(
            action_type=ActionType.DEFEND,
            actor_name="unknown",
            raw_input=player_text,
            confidence=0.0,
        )

    def _build_user_message(
        self,
        player_text: str,
        actor_name: str,
        available_actions: list[str],
        combat_context: str,
    ) -> str:
        """Build the user message for the LLM prompt."""
        parts = [
            f"Character name: {actor_name}",
            f"Available actions: {', '.join(available_actions)}",
            f"Player input: {player_text}",
        ]
        if combat_context:
            parts.insert(0, f"Combat context: {combat_context}")
        return "\n".join(parts)
