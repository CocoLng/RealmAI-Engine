"""Quest Generator — creates dynamic quests using the LLM."""

import logging
from pathlib import Path
from typing import Any

from ai.client import LLMParseError, OllamaClient
from ai.language import language_instruction
from world.quest import Quest, QuestObjective, QuestStatus

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_quest_generator.txt").read_text()
_BRAINSTORM_PROMPT = (Path(__file__).parent / "prompts" / "brainstorm_quest.txt").read_text()


class QuestGenerator:
    """Generates dynamic quests based on campaign context and NPCs.

    Output is a fully-formed Quest (world/quest.py) with AVAILABLE status.
    The caller is responsible for persisting the quest via QuestRepository.

    Uses a 2-call chain:
      1. Brainstorm 2-3 quest hooks (think=True, low budget)
      2. Generate the full quest JSON from the best concept (think=False)
    """

    MODEL = "qwen3.5:9b"

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def generate(
        self,
        campaign_context: str,
        location_name: str,
        available_npcs: list[str],
        language: str = "fr",
    ) -> Quest:
        """Generate a new quest for the current situation.

        Args:
            campaign_context: Assembled context describing the campaign state.
            location_name: Name of the current location.
            available_npcs: Names of NPCs available in the current location.
            language: ISO 639-1 language code for narrative output.

        Returns:
            A Quest with status=AVAILABLE, ready to be saved.
        """
        user_content = self._build_user_message(campaign_context, location_name, available_npcs)
        lang_prefix = language_instruction(language)

        # --- Call 1: Brainstorm (think=True, lower budget) ---
        brainstorm_context = self._brainstorm(user_content, lang_prefix)

        # --- Call 2: Generate (think=False) ---
        if brainstorm_context:
            generate_user = (
                f"{user_content}\n\n"
                f"## Brainstorm context\n{brainstorm_context}\n\n"
                f"Using the selected concept above, generate the full quest."
            )
        else:
            generate_user = user_content

        system_prompt = lang_prefix + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": generate_user},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.8, think=False)
        objectives = [
            QuestObjective(
                description=obj["description"],
                is_complete=False,
            )
            for obj in data.get("objectives", [])
        ]
        quest = Quest(
            title=str(data["title"]),
            description=str(data["description"]),
            status=QuestStatus.AVAILABLE,
            objectives=objectives,
            reward_xp=int(data.get("reward_xp", 100)),
            reward_gold=int(data.get("reward_gold", 0)),
            giver_npc=data.get("giver_npc") or None,
        )
        logger.info(
            "QUEST title=%r location=%s reward_xp=%d giver=%s",
            quest.title, location_name, quest.reward_xp, quest.giver_npc,
        )
        return quest

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
            logger.info("QUEST brainstorm returned %d options", len(data.get("options", [])))
            return self._format_brainstorm(data)
        except (LLMParseError, KeyError, ValueError) as exc:
            logger.warning("QUEST brainstorm failed, falling back to single-call: %s", exc)
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
        self, campaign_context: str, location_name: str, available_npcs: list[str]
    ) -> str:
        """Build the user message for the LLM prompt.

        Args:
            campaign_context: Assembled context describing the campaign state.
            location_name: Name of the current location.
            available_npcs: Names of NPCs available in the current location.

        Returns:
            Formatted user message string.
        """
        npc_list = ", ".join(available_npcs) if available_npcs else "None"
        return (
            f"{campaign_context}\n\n"
            f"Current location: {location_name}\n"
            f"Available NPCs: {npc_list}"
        )
