"""World Generator — creates dynamic locations using the LLM."""

import logging
from pathlib import Path
from typing import Any

from ai.client import LLMParseError, OllamaClient
from ai.language import language_instruction
from world.location import Location

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_world_generator.txt").read_text()
_BRAINSTORM_PROMPT = (Path(__file__).parent / "prompts" / "brainstorm_world.txt").read_text()


class WorldGenerator:
    """Generates dynamic locations based on campaign context.

    Output is a fully-formed Location (world/location.py).
    The caller is responsible for persisting the location via LocationRepository.

    Uses a 2-call chain:
      1. Brainstorm 2-3 location concepts (think=True, low budget)
      2. Generate the full location JSON from the best concept (think=False)
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
        lang_prefix = language_instruction(language)

        # --- Call 1: Brainstorm (think=True, lower budget) ---
        brainstorm_context = self._brainstorm(user_content, lang_prefix)

        # --- Call 2: Generate (think=False) ---
        if brainstorm_context:
            generate_user = (
                f"{user_content}\n\n"
                f"## Brainstorm context\n{brainstorm_context}\n\n"
                f"Using the selected concept above, generate the full location."
            )
        else:
            generate_user = user_content

        system_prompt = lang_prefix + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": generate_user},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.8, think=False)
        items_available = list(data.get("items_available", []))
        raw_descriptions = data.get("item_descriptions") or {}
        # Keep only descriptions whose key actually appears in items_available,
        # so a stray LLM hallucination cannot leak past canon.
        item_descriptions = {
            str(name): str(desc).strip()
            for name, desc in raw_descriptions.items()
            if name in items_available and str(desc).strip()
        }
        filtered_keys = set(raw_descriptions.keys()) - set(item_descriptions.keys())
        if filtered_keys:
            logger.warning(
                "Filtered %d item descriptions not in items_available: %s",
                len(filtered_keys),
                filtered_keys,
            )
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
            logger.info("WORLD brainstorm returned %d options", len(data.get("options", [])))
            return self._format_brainstorm(data)
        except (LLMParseError, KeyError, ValueError) as exc:
            logger.warning("WORLD brainstorm failed, falling back to single-call: %s", exc)
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
