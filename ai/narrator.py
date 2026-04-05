"""Narrator — converts mechanical action results into immersive narrative."""

import logging
from pathlib import Path

from ai.client import OllamaClient
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
    ) -> NarrativeResult:
        """Generate an immersive narrative description of a resolved action.

        Args:
            action_result_text: Mechanical summary of what happened
                (e.g. "Thorin attacks Goblin. Hit! 8 damage dealt.").
            context_prompt: Full assembled memory context from ContextAssembler.

        Returns:
            NarrativeResult with narrative text and tone classification.
        """
        user_content = f"{context_prompt}\n\n## What happened\n{action_result_text}"
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.8)
        return NarrativeResult(
            narrative=str(data.get("narrative", "")),
            tone=data.get("tone", "dramatic"),  # type: ignore[arg-type]
        )
