"""Quest Generator — creates dynamic quests using the LLM."""

import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.language import language_instruction
from world.quest import Quest, QuestObjective, QuestStatus

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_quest_generator.txt").read_text()


class QuestGenerator:
    """Generates dynamic quests based on campaign context and NPCs.

    Output is a fully-formed Quest (world/quest.py) with AVAILABLE status.
    The caller is responsible for persisting the quest via QuestRepository.

    Uses a single LLM call with high temperature for creative variety.
    An optional ``quest_hint`` parameter allows the caller to inject
    enriched context (e.g. from the Story Director) to steer generation.
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
        quest_hint: str | None = None,
    ) -> Quest:
        """Generate a new quest for the current situation.

        Args:
            campaign_context: Assembled context describing the campaign state.
            location_name: Name of the current location.
            available_npcs: Names of NPCs available in the current location.
            language: ISO 639-1 language code for narrative output.
            quest_hint: Optional enriched context to steer quest generation
                (e.g. from Story Director hooks or player backstory threads).

        Returns:
            A Quest with status=AVAILABLE, ready to be saved.
        """
        user_content = self._build_user_message(
            campaign_context, location_name, available_npcs, quest_hint,
        )
        lang_prefix = language_instruction(language)
        system_prompt = lang_prefix + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.9, think=False)
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

    def _build_user_message(
        self,
        campaign_context: str,
        location_name: str,
        available_npcs: list[str],
        quest_hint: str | None = None,
    ) -> str:
        """Build the user message for the LLM prompt.

        Args:
            campaign_context: Assembled context describing the campaign state.
            location_name: Name of the current location.
            available_npcs: Names of NPCs available in the current location.
            quest_hint: Optional enriched context to include in the prompt.

        Returns:
            Formatted user message string.
        """
        npc_list = ", ".join(available_npcs) if available_npcs else "None"
        parts = [
            f"{campaign_context}\n\nCurrent location: {location_name}\nAvailable NPCs: {npc_list}",
        ]
        if quest_hint:
            parts.append(f"\nQuest context hint: {quest_hint}")
        return "\n".join(parts)
