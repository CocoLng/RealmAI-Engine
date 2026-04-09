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
        system_prompt = language_instruction(language) + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.8, think=True)
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
