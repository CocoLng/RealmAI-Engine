"""World Generator — creates dynamic locations using the LLM."""

import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.language import language_instruction
from world.location import Location

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_world_generator.txt").read_text()


class WorldGenerator:
    """Generates dynamic locations based on campaign context.

    Output is a fully-formed Location (world/location.py).
    The caller is responsible for persisting the location via LocationRepository.
    """

    MODEL = "qwen3.5:9b"

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def generate(
        self,
        campaign_context: str,
        location_type: str,
        location_name: str | None = None,
        language: str = "fr",
        location_hints: list[str] | None = None,
    ) -> Location:
        """Generate a new location for the campaign.

        Args:
            campaign_context: Assembled context describing the campaign state.
            location_type: Type of location to generate (e.g. "tavern", "dungeon").
            location_name: Optional specific name for the location.
            language: ISO 639-1 language code for narrative output.
            location_hints: Optional list of canonical location names from the
                story arc. When provided, the LLM is instructed to reuse these
                exact names for any locations it references (connections, name).

        Returns:
            A Location ready to be saved.
        """
        user_content = self._build_user_message(
            campaign_context, location_type, location_name, location_hints,
        )
        system_prompt = language_instruction(language) + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.8, think=True)
        items_available = list(data.get("items_available", []))
        raw_descriptions = data.get("item_descriptions") or {}
        # Keep only descriptions whose key actually appears in items_available,
        # so a stray LLM hallucination cannot leak past canon.
        item_descriptions = {
            str(name): str(desc).strip()
            for name, desc in raw_descriptions.items()
            if name in items_available and str(desc).strip()
        }
        location = Location(
            name=str(data["name"]),
            description=str(data["description"]),
            connections=list(data.get("connections", [])),
            npcs_present=list(data.get("npcs_present", [])),
            items_available=items_available,
            item_descriptions=item_descriptions,
        )
        logger.info(
            "WORLD name=%r type=%s connections=%d",
            location.name, location_type, len(location.connections),
        )
        return location

    def _build_user_message(
        self,
        campaign_context: str,
        location_type: str,
        location_name: str | None,
        location_hints: list[str] | None = None,
    ) -> str:
        """Build the user message for the LLM prompt.

        Args:
            campaign_context: Assembled context describing the campaign state.
            location_type: Type of location to generate.
            location_name: Optional specific name for the location.
            location_hints: Optional canonical location names from the story arc.

        Returns:
            Formatted user message string.
        """
        parts = [campaign_context, f"Location type: {location_type}"]
        if location_name:
            parts.append(f"Suggested name: {location_name}")
        if location_hints:
            hint_list = ", ".join(location_hints)
            parts.append(
                f"Canonical location names from the story arc: {hint_list}\n"
                "You MUST reuse these exact names when they match the location "
                "you are generating or when listing connections. Do NOT invent "
                "alternative names for locations that already appear in this list."
            )
        return "\n\n".join(parts)
