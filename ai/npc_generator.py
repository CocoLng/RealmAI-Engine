"""NPC Generator — produces canon backstory sheets for newly-encountered NPCs."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ai.client import OllamaClient
from ai.language import language_instruction
from ai.models import NPCSheet

if TYPE_CHECKING:
    from memory.indexer import SemanticIndexer

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "system_npc_generator.txt"
).read_text()


class NPCGenerator:
    """Lazily generate canon backstories for NPCs that have empty sheets.

    The output is persisted onto the NPC entity by the caller so this
    expensive LLM call only happens once per NPC.

    An optional ``archetype_context`` parameter allows the caller to inject
    archetype information (personality traits, narrative hooks) to produce
    richer, more thematic backstories.
    """

    MODEL = "qwen3.5:4b"

    def __init__(
        self,
        client: OllamaClient,
        indexer: "SemanticIndexer | None" = None,
    ) -> None:
        self._client = client
        self._indexer = indexer

    def generate(
        self,
        npc_name: str,
        location_context: str,
        campaign_theme: str,
        language: str = "fr",
        archetype_context: str | None = None,
        campaign_id: str = "",
    ) -> NPCSheet:
        """Generate a backstory sheet for ``npc_name``.

        Args:
            npc_name: Canonical NPC name (used verbatim in the prompt).
            location_context: Where the NPC is encountered — name + ambiance.
            campaign_theme: Broader campaign theme so secrets can hook in.
            language: ISO 639-1 language code for output.
            archetype_context: Optional archetype info (personality traits,
                narrative hooks) to produce richer backstories.
            campaign_id: Campaign identifier forwarded to the SemanticIndexer
                when one is provided.  Defaults to ``""`` so existing callers
                that omit it continue to work unchanged.

        Returns:
            An :class:`NPCSheet` with personality, description, secrets,
            and knowledge ready to persist on the NPC entity.
        """
        parts = [
            f"NPC name: {npc_name}",
            f"\nLocation context:\n{location_context}",
            f"\nCampaign theme: {campaign_theme}",
        ]
        if archetype_context:
            parts.append(f"\nNPC Archetype:\n{archetype_context}")
        user_content = "\n".join(parts)

        system_prompt = language_instruction(language) + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.9)
        secrets = [str(s).strip() for s in data.get("secrets", []) if str(s).strip()]
        knowledge = [str(k).strip() for k in data.get("knowledge", []) if str(k).strip()]

        # NPCSheet requires min_length=1 for secrets and knowledge.
        # Use archetype-aware fallbacks when the LLM returns empty lists.
        if not secrets:
            if archetype_context:
                secrets = [f"{npc_name} cache un secret lié à son passé"]
            else:
                secrets = ["A un secret qu'il/elle ne révèle pas facilement"]
            logger.warning("NPCGEN name=%r: empty secrets from LLM, using fallback", npc_name)
        if not knowledge:
            if archetype_context:
                knowledge = [f"Connaît bien {location_context.split(chr(10))[0].strip()[:60]}"]
            else:
                knowledge = ["Connaît bien les environs"]
            logger.warning("NPCGEN name=%r: empty knowledge from LLM, using fallback", npc_name)

        sheet = NPCSheet(
            personality=str(data.get("personality", "")).strip(),
            description=str(data.get("description", "")).strip(),
            secrets=secrets,
            knowledge=knowledge,
        )
        logger.info(
            "NPCGEN name=%r secrets=%d knowledge=%d",
            npc_name, len(sheet.secrets), len(sheet.knowledge),
        )

        if self._indexer is not None and campaign_id:
            self._indexer.index_npc(campaign_id, npc_name, sheet)

        return sheet
