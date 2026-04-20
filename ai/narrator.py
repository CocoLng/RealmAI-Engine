"""Narrator — converts mechanical action results into immersive narrative."""

import logging
from pathlib import Path

from ai.client import LLMParseError, OllamaClient, OllamaUnavailableError
from ai.language import language_instruction
from ai.models import DirectorNote, NarrativeResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_narrator.txt").read_text()


class Narrator:
    """Narrates action results as immersive text using the 9B model.

    Receives the assembled context prompt and a mechanical summary of what
    happened (ActionResult). Describes it — never decides mechanics.
    """

    MODEL = "qwen3.5:9b"

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def narrate(
        self,
        action_result_text: str,
        context_prompt: str,
        language: str = "fr",
        player_intent: str = "",
        outcome_facts: str = "",
        has_npc_dialogue: bool = False,
        director_note: DirectorNote | None = None,
    ) -> NarrativeResult:
        """Generate an immersive narrative description of a resolved action.

        Three-tier fallback chain — never throws:
          1. Primary call with full prompt
          2. Retry with a simplified prompt (only action + context)
          3. Hardcoded template fallback (always succeeds)
        """
        logger.info("NARRATE input=%r intent=%r", action_result_text[:100], player_intent[:100])

        # --- Tier 1: primary call ---
        try:
            result = self._call_llm(
                action_result_text=action_result_text,
                context_prompt=context_prompt,
                language=language,
                player_intent=player_intent,
                outcome_facts=outcome_facts,
                has_npc_dialogue=has_npc_dialogue,
                simplified=False,
                director_note=director_note,
            )
            if len(result.narrative) >= 50:
                return result
            logger.warning(
                "Narrator primary returned short narrative (%d chars), retrying simplified",
                len(result.narrative),
            )
        except (LLMParseError, OllamaUnavailableError) as exc:
            logger.warning("Narrator primary call failed (%s), retrying simplified", exc)

        # --- Tier 2: simplified retry ---
        try:
            result = self._call_llm(
                action_result_text=action_result_text,
                context_prompt=context_prompt,
                language=language,
                player_intent="",
                outcome_facts="",
                has_npc_dialogue=False,
                simplified=True,
                director_note=director_note,
            )
            if len(result.narrative) >= 50:
                return result
            logger.error(
                "Narrator simplified retry returned short narrative (%d chars), using template",
                len(result.narrative),
            )
        except (LLMParseError, OllamaUnavailableError) as exc:
            logger.error("Narrator simplified retry failed (%s), using template", exc)

        # --- Tier 3: template fallback (never raises) ---
        return self._template_fallback(action_result_text, outcome_facts)

    def _call_llm(
        self,
        *,
        action_result_text: str,
        context_prompt: str,
        language: str,
        player_intent: str,
        outcome_facts: str,
        has_npc_dialogue: bool,
        simplified: bool,
        director_note: DirectorNote | None = None,
    ) -> NarrativeResult:
        """Issue one LLM call and parse the response. May raise.

        ``simplified=True`` strips optional sections from the user message —
        useful when the primary call failed and we suspect the prompt may
        have confused the model.
        """
        sections: list[str] = []
        if director_note is not None and (
            director_note.current_objective
            or director_note.next_beat_hint
            or director_note.required_mentions
            or director_note.forbidden_topics
        ):
            sections.append(self._format_direction_block(director_note))
        sections.extend([context_prompt, f"## What happened\n{action_result_text}"])
        if not simplified:
            if player_intent:
                sections.append(f"## Player framing\n{player_intent}")
            if outcome_facts:
                sections.append(f"## State changes\n{outcome_facts}")
            if has_npc_dialogue:
                sections.append(
                    "## Important\n"
                    "NPC dialogue will be displayed separately. "
                    "Describe ONLY atmosphere and body language around the "
                    "speech. Do NOT write any spoken words."
                )
        user_content = "\n\n".join(sections)
        system_prompt = language_instruction(language) + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.8)
        result = NarrativeResult(
            narrative=str(data.get("narrative", "")),
            tone=data.get("tone", "dramatic"),  # type: ignore[arg-type]
            scene_goal_touched=bool(data.get("scene_goal_touched", False)),
            beat_advanced=bool(data.get("beat_advanced", False)),
            npcs_mentioned=list(data.get("npcs_mentioned") or []),
            locked_facts_used=list(data.get("locked_facts_used") or []),
        )
        logger.info("NARRATE tone=%s output=%r", result.tone, result.narrative[:200])
        logger.debug("NARRATE full_output=%s", result.narrative)
        return result

    @staticmethod
    def _format_direction_block(note: DirectorNote) -> str:
        """Format the Story Director's direction fields into a prompt block."""
        lines = ["[STORY DIRECTION]"]
        if note.current_objective:
            lines.append(f"Current objective: {note.current_objective}")
        if note.next_beat_hint:
            lines.append(f"Next beat hint: {note.next_beat_hint}")
        if note.required_mentions:
            lines.append("Re-mention if natural: " + ", ".join(note.required_mentions))
        if note.forbidden_topics:
            lines.append("Do NOT re-reveal: " + ", ".join(note.forbidden_topics))
        return "\n".join(lines)

    _TEMPLATES: dict[str, list[str]] = {
        "attack": [
            "Le combat se poursuit dans la confusion. {action}.",
            "Les coups pleuvent autour de toi. {action}.",
            "L'affrontement reprend de plus belle. {action}.",
        ],
        "move": [
            "Le décor change autour de toi. {action}.",
            "Tes pas te portent ailleurs. {action}.",
        ],
        "talk": [
            "Les mots échangés résonnent encore dans l'air. {action}.",
            "La conversation suit son cours. {action}.",
        ],
        "search": [
            "Tu fouilles avec attention les environs. {action}.",
            "Tes mains parcourent l'endroit. {action}.",
        ],
        "default": [
            "Le MJ rassemble ses idées un instant. {action}.",
            "L'instant se prolonge avant la suite. {action}.",
        ],
    }

    def _template_fallback(
        self, action_result_text: str, outcome_facts: str
    ) -> NarrativeResult:
        """Return a hardcoded short narrative. Never raises.

        Used as the last-resort fallback when both the primary LLM call and
        the simplified retry have failed. The narrative is intentionally
        short and in-universe — its job is to keep the session alive, not to
        be invisible.
        """
        category = self._pick_template_category(action_result_text)
        variants = self._TEMPLATES.get(category, self._TEMPLATES["default"])
        # Deterministic-ish pick: use the length of action_result_text mod len(variants).
        # Avoids importing random for a 3-element list.
        template = variants[len(action_result_text) % len(variants)]
        narrative = template.format(action=action_result_text.rstrip("."))
        if outcome_facts:
            narrative = f"{narrative} {outcome_facts}"
        return NarrativeResult(narrative=narrative, tone="dramatic")

    @staticmethod
    def _pick_template_category(action_result_text: str) -> str:
        """Map the mechanical action verb to a template category."""
        lower = action_result_text.lower()
        if "attack" in lower or "attaque" in lower or "damage" in lower or "dégât" in lower:
            return "attack"
        if "move" in lower or "déplace" in lower or "go to" in lower:
            return "move"
        if "talk" in lower or "parle" in lower or "dialogue" in lower:
            return "talk"
        if "search" in lower or "fouille" in lower or "look" in lower:
            return "search"
        return "default"
