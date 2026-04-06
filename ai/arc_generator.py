"""Arc Generator --- creates campaign story arcs using the LLM."""

import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.language import language_instruction
from world.story_arc import StoryArc

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_arc_generator.txt").read_text()


class ArcGenerator:
    """Generates a complete story arc for a campaign.

    Output is a fully-formed StoryArc (world/story_arc.py).
    The caller is responsible for persisting the arc.
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
        system_prompt = language_instruction(language) + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.8, think=True, num_predict=800)
        arc = StoryArc.model_validate(data)
        logger.info(
            "ARC theme=%r beats=%d villain=%r",
            arc.theme, len(arc.beats), arc.villain_name,
        )
        return arc

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
