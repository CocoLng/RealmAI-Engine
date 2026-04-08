"""NPC Generator — produces canon backstory sheets for newly-encountered NPCs."""

import logging
from pathlib import Path

from ai.client import OllamaClient
from ai.language import language_instruction
from ai.models import NPCSheet

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "system_npc_generator.txt"
).read_text()


class NPCGenerator:
    """Lazily generate canon backstories for NPCs that have empty sheets.

    The output is persisted onto the NPC entity by the caller so this
    expensive LLM call only happens once per NPC.
    """

    MODEL = "qwen3.5:4b"

    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    def generate(
        self,
        npc_name: str,
        location_context: str,
        campaign_theme: str,
        language: str = "fr",
    ) -> NPCSheet:
        """Generate a backstory sheet for ``npc_name``.

        Args:
            npc_name: Canonical NPC name (used verbatim in the prompt).
            location_context: Where the NPC is encountered — name + ambiance.
            campaign_theme: Broader campaign theme so secrets can hook in.
            language: ISO 639-1 language code for output.

        Returns:
            An :class:`NPCSheet` with personality, description, secrets,
            and knowledge ready to persist on the NPC entity.
        """
        user_content = (
            f"NPC name: {npc_name}\n\n"
            f"Location context:\n{location_context}\n\n"
            f"Campaign theme: {campaign_theme}"
        )
        system_prompt = language_instruction(language) + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.8)
        sheet = NPCSheet(
            personality=str(data.get("personality", "")).strip(),
            description=str(data.get("description", "")).strip(),
            secrets=[str(s).strip() for s in data.get("secrets", []) if str(s).strip()],
            knowledge=[str(k).strip() for k in data.get("knowledge", []) if str(k).strip()],
        )
        logger.info(
            "NPCGEN name=%r secrets=%d knowledge=%d",
            npc_name, len(sheet.secrets), len(sheet.knowledge),
        )
        return sheet
