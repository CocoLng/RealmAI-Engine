"""Arc Generator --- creates campaign story arcs using the LLM."""

import logging
from pathlib import Path
from typing import Any

from ai.client import LLMParseError, OllamaClient
from ai.language import language_instruction
from world.story_arc import StoryArc

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_arc_generator.txt").read_text()
_BRAINSTORM_PROMPT = (Path(__file__).parent / "prompts" / "brainstorm_arc.txt").read_text()


class ArcGenerator:
    """Generates a complete story arc for a campaign.

    Output is a fully-formed StoryArc (world/story_arc.py).
    The caller is responsible for persisting the arc.

    Uses a 2-call chain:
      1. Brainstorm 2-3 arc concepts (think=True, low budget)
      2. Generate the full arc JSON from the best concept (think=False)
    """

    MODEL = "qwen3.5:9b"

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def generate(
        self,
        theme: str,
        player_count: int,
        language: str = "fr",
    ) -> StoryArc:
        """Generate a new story arc for the campaign.

        Args:
            theme: The campaign theme (e.g. "dark fantasy", "pirate adventure").
            player_count: Number of players in the campaign.
            language: ISO 639-1 language code for narrative output.

        Returns:
            A StoryArc ready to be saved.
        """
        user_content = self._build_user_message(theme, player_count)
        lang_prefix = language_instruction(language)

        # --- Call 1: Brainstorm (think=True, lower budget) ---
        brainstorm_context = self._brainstorm(user_content, lang_prefix)

        # --- Call 2: Generate (think=False) ---
        if brainstorm_context:
            generate_user = (
                f"{user_content}\n\n"
                f"## Brainstorm context\n{brainstorm_context}\n\n"
                f"Using the selected concept above, generate the full story arc."
            )
        else:
            generate_user = user_content

        system_prompt = lang_prefix + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": generate_user},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.8, think=False)
        arc = StoryArc.model_validate(data)
        logger.info(
            "ARC theme=%r beats=%d villain=%r",
            arc.theme, len(arc.beats), arc.villain_name,
        )
        return arc

    def _brainstorm(self, user_content: str, lang_prefix: str) -> str | None:
        """Run the brainstorm call (Call 1) and return the selected concept as context.

        Returns None if the brainstorm call fails, allowing graceful fallback.
        """
        system_prompt = lang_prefix + _BRAINSTORM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            data: dict[str, Any] = self._client.chat_json(
                self.MODEL, messages, temperature=0.8, think=True, thinking_budget=2048,
            )
            logger.info("ARC brainstorm returned %d options", len(data.get("options", [])))
            return self._format_brainstorm(data)
        except (LLMParseError, KeyError, ValueError) as exc:
            logger.warning("ARC brainstorm failed, falling back to single-call: %s", exc)
            return None

    @staticmethod
    def _format_brainstorm(data: dict[str, Any]) -> str:
        """Format brainstorm output into a concise context string."""
        options = data.get("options", [])
        parts: list[str] = []
        for opt in options:
            marker = "[SELECTED] " if opt.get("selected") else ""
            concept = opt.get("concept", "")
            elements = opt.get("key_elements", [])
            elements_str = "; ".join(str(e) for e in elements)
            parts.append(f"{marker}{concept} — {elements_str}")
        return "\n".join(parts)

    def _build_user_message(self, theme: str, player_count: int) -> str:
        """Build the user message for the LLM prompt.

        Args:
            theme: The campaign theme.
            player_count: Number of players.

        Returns:
            Formatted user message string.
        """
        return (
            f"Campaign theme: {theme}\n"
            f"Number of players: {player_count}\n\n"
            f"Generate a compelling story arc with 10-15 story beats."
        )
