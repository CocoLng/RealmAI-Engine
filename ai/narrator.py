"""Narrator — converts mechanical action results into immersive narrative."""

import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.language import language_instruction
from ai.models import NarrativeResult

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
    ) -> NarrativeResult:
        """Generate an immersive narrative description of a resolved action.

        Args:
            action_result_text: Mechanical summary (e.g. "Thorin attacks
                Goblin. Hit! 8 damage dealt.").
            context_prompt: Assembled scene context from
                ``describe_scene_for_narrator`` (location, items, NPCs,
                exits) plus the acting character.
            language: ISO 639-1 language code for narrative output.
            player_intent: How the player framed the action (raw input
                plus interpreter-extracted detail). Empty string when no
                framing is available.
            outcome_facts: What mechanically changed in engine state.
                Empty string when no mutation occurred.
            has_npc_dialogue: When ``True``, NPC spoken words will be
                displayed separately on Discord.  The narrator should
                describe only framing (body language, atmosphere).

        Returns:
            NarrativeResult with narrative text and tone classification.
        """
        logger.info("NARRATE input=%r intent=%r", action_result_text[:100], player_intent[:100])

        sections = [context_prompt, f"## What happened\n{action_result_text}"]
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
        )
        logger.info("NARRATE tone=%s output=%r", result.tone, result.narrative[:200])
        logger.debug("NARRATE full_output=%s", result.narrative)
        return result

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
