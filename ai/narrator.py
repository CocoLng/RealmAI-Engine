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
    ) -> NarrativeResult:
        """Generate an immersive narrative description of a resolved action.

        Args:
            action_result_text: Mechanical summary of what happened
                (e.g. "Thorin attacks Goblin. Hit! 8 damage dealt.").
            context_prompt: Full assembled memory context from ContextAssembler.
            language: ISO 639-1 language code for narrative output.

        Returns:
            NarrativeResult with narrative text and tone classification.
        """
        logger.info("NARRATE input=%r", action_result_text[:100])
        user_content = f"{context_prompt}\n\n## What happened\n{action_result_text}"
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
