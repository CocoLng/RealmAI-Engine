"""Interpreter — converts player free text to structured InterpretedAction."""

import json
import logging
from pathlib import Path

from ai.client import LLMParseError, OllamaClient
from ai.language import language_instruction
from ai.models import InterpretedAction
from ai.scene_context import SceneContext
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
        scene_context: SceneContext,
        language: str = "fr",
    ) -> InterpretedAction:
        """Classify player text as a structured action.

        Args:
            player_text: The raw player input.
            actor_name: Name of the acting character.
            scene_context: What the actor currently perceives.
            language: ISO 639-1 language code (used for instructions only;
                the JSON output is language-agnostic).

        Returns:
            InterpretedAction with action_type, targets, and confidence.
            On parse failure, returns a low-confidence fallback action:
            DEFEND in combat, IMPROVISE outside combat.
        """
        user_content = self._build_user_message(
            player_text, actor_name, scene_context,
        )
        messages = [
            {
                "role": "system",
                "content": language_instruction(language) + _SYSTEM_PROMPT,
            },
            {"role": "user", "content": user_content},
        ]

        logger.info("INTERPRET player=%s input=%r", actor_name, player_text[:100])

        try:
            data = self._client.chat_json(self.MODEL, messages, temperature=0.3)
        except (json.JSONDecodeError, LLMParseError):
            logger.warning("Interpreter: LLM returned non-JSON for input: %r", player_text)
            return self._fallback(player_text, actor_name, scene_context)

        try:
            action_type = ActionType(data.get("action_type", ""))
        except (ValueError, KeyError) as exc:
            logger.warning("Interpreter: invalid action_type: %s", exc)
            return self._fallback(player_text, actor_name, scene_context)

        try:
            action = InterpretedAction(
                action_type=action_type,
                actor_name=actor_name,
                target_name=data.get("target_name"),
                weapon_name=data.get("weapon_name"),
                spell_name=data.get("spell_name"),
                item_name=data.get("item_name"),
                talk_topic=data.get("talk_topic"),
                search_detail=data.get("search_detail"),
                improvise_description=data.get("improvise_description"),
                raw_input=player_text,
                confidence=float(data.get("confidence", 1.0)),
                is_lethal_intent=bool(data.get("is_lethal_intent", False)),
            )
        except (ValueError, KeyError) as exc:
            logger.warning("Interpreter: failed to build InterpretedAction: %s", exc)
            return self._fallback(player_text, actor_name, scene_context)

        # Safety net — the 4B model sometimes classifies Move correctly but
        # drops the target_name. Recover by matching the raw input against
        # the visible exits (and their aliases, passed through SceneContext
        # via the resolver later). Falling through with target_name=None
        # would trigger an in-character refusal and leave the player stuck.
        if action.action_type == ActionType.MOVE and not action.target_name:
            recovered = _recover_move_target(player_text, scene_context)
            if recovered is not None:
                logger.info(
                    "INTERPRET move target recovered raw=%r → %r",
                    player_text, recovered,
                )
                action = action.model_copy(update={"target_name": recovered})

        logger.info(
            "INTERPRET result action=%s target=%s confidence=%.2f",
            action.action_type.value, action.target_name, action.confidence,
        )
        return action

    def disambiguate_entity(
        self,
        raw_reference: str,
        candidates: list[tuple[str, list[str]]],
        language: str = "fr",
    ) -> str | None:
        """Ask the 4B model which candidate the player meant.

        Used by the entity resolver as a last-resort fallback when the
        deterministic Python matcher returns nothing. Never raises — returns
        ``None`` on any failure (LLM error, no JSON, no valid pick).

        Args:
            raw_reference: The raw player wording (e.g. "le villageois").
            candidates: List of ``(canonical_name, aliases)`` tuples.
            language: ISO 639-1 language code for the prompt.

        Returns:
            One of the canonical names from ``candidates``, or ``None``.
        """
        if not raw_reference or not candidates:
            return None

        names = {name for name, _ in candidates}
        if language == "fr":
            sys_msg = (
                "Tu aides à désambiguïser une référence libre du joueur "
                "vers une entité existante. Réponds UNIQUEMENT en JSON."
            )
            cand_lines = "\n".join(
                f'- "{name}" (alias: {", ".join(aliases) if aliases else "aucun"})'
                for name, aliases in candidates
            )
            user_msg = (
                f'Le joueur a fait référence à "{raw_reference}". '
                f"Voici les entités disponibles dans la scène :\n"
                f"{cand_lines}\n\n"
                f'À laquelle de ces entités fait référence "{raw_reference}" ? '
                f"Retourne UNIQUEMENT le nom EXACT d'une de ces entités, "
                f'ou null si aucune ne correspond.\n\n'
                'Réponse JSON: {"match": "..." ou null}'
            )
        else:
            sys_msg = (
                "Help disambiguate a player's free-text reference to an "
                "in-scene entity. Reply ONLY in JSON."
            )
            cand_lines = "\n".join(
                f'- "{name}" (aliases: {", ".join(aliases) if aliases else "none"})'
                for name, aliases in candidates
            )
            user_msg = (
                f'The player referred to "{raw_reference}". '
                f"Available entities in the scene:\n{cand_lines}\n\n"
                f'Which of these does "{raw_reference}" refer to? '
                f"Return the EXACT name of one of these entities, "
                f'or null if none match.\n\n'
                'JSON response: {"match": "..." or null}'
            )

        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ]
        try:
            data = self._client.chat_json(
                self.MODEL, messages, temperature=0.1, num_predict=64,
            )
        except Exception:  # noqa: BLE001 — must never break gameplay
            logger.warning("disambiguate_entity: LLM call failed", exc_info=True)
            return None

        match = data.get("match") if isinstance(data, dict) else None
        if not isinstance(match, str):
            return None
        if match in names:
            logger.info(
                "DISAMBIGUATE raw=%r → %r", raw_reference, match,
            )
            return match
        # Loose match: tolerate trivial wrapping like quoting/case.
        for name in names:
            if name.lower() == match.lower():
                return name
        return None

    def _fallback(
        self,
        player_text: str,
        actor_name: str,
        scene_context: SceneContext,
    ) -> InterpretedAction:
        """Return a safe fallback action when parsing fails.

        - In combat → DEFEND (preserves the existing combat-loop behaviour).
        - Outside combat → IMPROVISE with the raw text echoed back so the
          narrator can gently refuse / ask for clarification in-character.
        """
        if scene_context.in_combat:
            return InterpretedAction(
                action_type=ActionType.DEFEND,
                actor_name=actor_name,
                raw_input=player_text,
                confidence=0.0,
            )
        return InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=actor_name,
            raw_input=player_text,
            improvise_description=player_text,
            confidence=0.0,
        )

    def _build_user_message(
        self,
        player_text: str,
        actor_name: str,
        scene_context: SceneContext,
    ) -> str:
        """Build the user message for the LLM prompt."""
        lines: list[str] = ["## Scene context"]
        lines.append(f"Location: {scene_context.location_name or '(unknown)'}")
        if scene_context.location_description:
            lines.append(f"Description: {scene_context.location_description}")
        lines.append(
            "In combat: " + ("yes" if scene_context.in_combat else "no"),
        )
        if scene_context.in_combat and scene_context.combat_summary:
            lines.append(f"Combat: {scene_context.combat_summary}")
        if scene_context.enemies_visible:
            lines.append(
                "Enemies visible: " + ", ".join(scene_context.enemies_visible),
            )
        if scene_context.visible_npcs:
            lines.append("NPCs present: " + ", ".join(scene_context.visible_npcs))
        if scene_context.visible_exits:
            lines.append("Exits: " + ", ".join(scene_context.visible_exits))
        if scene_context.visible_objects:
            lines.append("Objects: " + ", ".join(scene_context.visible_objects))

        lines.append("")
        lines.append("## Actor")
        lines.append(f"Character name: {actor_name}")
        lines.append("")
        lines.append("## Player input")
        lines.append(player_text)

        return "\n".join(lines)


def _recover_move_target(
    player_text: str, scene_context: SceneContext,
) -> str | None:
    """Recover a Move destination when the LLM returned action=Move but
    target_name=None.

    Matches the raw player text against ``scene_context.visible_exits`` with
    the same alias-aware matcher used by the entity resolver, so this works
    even if the player said "dehors" while the canonical exit is
    "L'extérieur des remparts…". Returns the canonical exit name on a
    unique match, otherwise returns the raw player text so the downstream
    resolver can produce its normal ambiguity / unknown flow.
    """
    if not scene_context.visible_exits:
        return None

    # Lazy import to avoid a circular dependency with entity_resolver.
    from ai.entity_resolver import _match_candidates_v2

    text = (player_text or "").strip()
    if not text:
        return None

    matches = _match_candidates_v2(text, list(scene_context.visible_exits))
    if len(matches) == 1:
        return matches[0]
    # Multi-match or no match — let the downstream resolver handle it with
    # a proper disambiguation or unknown-entity flow. We only return the
    # raw text so MOVE gets a chance at the entity resolver instead of
    # short-circuiting as an unknown entity with an empty target.
    return text
