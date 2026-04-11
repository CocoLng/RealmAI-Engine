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
