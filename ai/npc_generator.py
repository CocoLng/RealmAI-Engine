"""NPC Generator — produces canon backstory sheets for newly-encountered NPCs."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ai.client import OllamaClient
from ai.language import language_instruction
from ai.models import NPCSheet
from engine.npc_archetypes import NPCArchetype, format_archetype_context

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

    An optional ``archetype`` (from :mod:`engine.npc_archetypes`) seeds the
    prompt with authored content — contradictory traits, narrative hook,
    dialogue pattern — and doubles as the fallback source when the LLM
    returns empty lists.
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
        archetype: NPCArchetype | None = None,
        campaign_id: str = "",
    ) -> NPCSheet:
        """Generate a backstory sheet for ``npc_name``.

        Args:
            npc_name: Canonical NPC name (used verbatim in the prompt).
            location_context: Where the NPC is encountered — name + ambiance.
            campaign_theme: Broader campaign theme so secrets can hook in.
            language: ISO 639-1 language code for output.
            archetype: Optional authored archetype (traits, hook, dialogue
                pattern) injected into the prompt and used as the fallback
                source instead of generic sentences.
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
        if archetype is not None:
            parts.append(f"\nNPC Archetype:\n{format_archetype_context(archetype)}")
        user_content = "\n".join(parts)

        system_prompt = language_instruction(language) + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.9)
        secrets = [str(s).strip() for s in data.get("secrets", []) if str(s).strip()]
        knowledge = [str(k).strip() for k in data.get("knowledge", []) if str(k).strip()]

        # NPCSheet requires min_length=1 for secrets and knowledge. With an
        # archetype, fall back to its authored content (the hook IS a
        # playable secret) — never to the generic sentences. The generic
        # path only survives for archetype-less callers (tests, tooling).
        location_line = location_context.split(chr(10))[0].strip()[:60]
        if not secrets:
            if archetype is not None:
                secrets = [archetype.hook]
            else:
                secrets = ["A un secret qu'il/elle ne révèle pas facilement"]
            logger.warning("NPCGEN name=%r: empty secrets from LLM, using fallback", npc_name)
        if not knowledge:
            if archetype is not None:
                knowledge = [f"Connaît bien {location_line} — {archetype.traits[0]}"]
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
