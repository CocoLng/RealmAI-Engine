"""SemanticIndexer — central helper for adding documents to ChromaDB.

Wraps :class:`memory.semantic.SemanticMemory` with one method per
``SemanticDocumentType`` and a deterministic ID strategy so re-indexing
the same source produces no duplicates.

ID format: ``"<doc_type>:<source_key>"`` — e.g. ``"npc_sheet:cmp_1:aldric"``,
``"past_event:beat_cmp_1_3"``. The source key is sluggified (lowercase,
spaces → underscores) so ChromaDB IDs stay consistent across re-runs.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING

from memory.models import SemanticDocument, SemanticDocumentType

if TYPE_CHECKING:
    from ai.models import NPCSheet
    from memory.semantic import SemanticMemory
    from world.location import Location
    from world.npc import NPC
    from world.story_arc import StoryBeat

logger = logging.getLogger(__name__)


def _slug(text: str) -> str:
    """Normalize a string into a stable slug for ChromaDB IDs."""
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "_"


def _hash(text: str) -> str:
    """Short hash for content where slugging would collide (e.g. raw lore)."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


class SemanticIndexer:
    """Centralized add-document helper for the semantic memory layer.

    Each method is idempotent on its source key — calling ``index_beat``
    twice with the same beat does not create two documents in ChromaDB.
    """

    def __init__(self, semantic: "SemanticMemory") -> None:
        self._semantic = semantic

    def index_beat(self, campaign_id: str, beat: "StoryBeat") -> None:
        """Index a story beat as a PAST_EVENT-tagged document."""
        content = (
            f"Beat {beat.beat_number} — {beat.title}\n"
            f"{beat.description}\n"
            f"Location hint: {beat.location_hint}"
        )
        if beat.npc_names:
            content += f"\nNPCs involved: {', '.join(beat.npc_names)}"
        doc = SemanticDocument(
            id=f"past_event:beat_{campaign_id}_{beat.beat_number}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.PAST_EVENT,
            content=content,
            metadata={"beat_number": str(beat.beat_number), "title": beat.title},
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX beat campaign=%s beat=%d", campaign_id, beat.beat_number)

    def index_npc(self, campaign_id: str, npc_name: str, sheet: "NPCSheet") -> None:
        """Index an NPC sheet as an NPC_SHEET-tagged document."""
        content_parts = [
            f"NPC: {npc_name}",
            f"Personality: {sheet.personality}",
            f"Description: {sheet.description}",
        ]
        if sheet.knowledge:
            content_parts.append("Knowledge: " + "; ".join(sheet.knowledge))
        if sheet.secrets:
            content_parts.append("Secrets: " + "; ".join(sheet.secrets))
        content = "\n".join(content_parts)
        doc = SemanticDocument(
            id=f"npc_sheet:{campaign_id}:{_slug(npc_name)}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.NPC_SHEET,
            content=content,
            metadata={"npc_name": npc_name},
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX npc campaign=%s name=%s", campaign_id, npc_name)

    def index_location(self, campaign_id: str, location: "Location") -> None:
        """Index a location as a LOCATION_DETAIL-tagged document."""
        content = f"Location: {location.name}\nDescription: {location.description or '(no description)'}"
        if location.npcs_present:
            content += f"\nNPCs present: {', '.join(location.npcs_present)}"
        if location.items_available:
            content += f"\nItems: {', '.join(location.items_available)}"
        doc = SemanticDocument(
            id=f"location_detail:{campaign_id}:{_slug(location.name)}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.LOCATION_DETAIL,
            content=content,
            metadata={"location_name": location.name},
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX location campaign=%s name=%s", campaign_id, location.name)

    def index_npc_entity(self, campaign_id: str, npc: "NPC") -> None:
        """Index a hydrated world NPC directly (backfill path).

        Same document ID as :meth:`index_npc`, so a backfilled NPC and a
        generation-time sheet dedupe onto one document. Unlike
        :class:`ai.models.NPCSheet` (min_length=1), empty secrets/knowledge
        are tolerated — old saves may hold partial sheets. No-op when the
        NPC is unhydrated (no personality and no description).
        """
        if not (npc.personality or npc.description):
            return
        content_parts = [f"NPC: {npc.name}"]
        if npc.personality:
            content_parts.append(f"Personality: {npc.personality}")
        if npc.description:
            content_parts.append(f"Description: {npc.description}")
        if npc.knowledge:
            content_parts.append("Knowledge: " + "; ".join(npc.knowledge))
        if npc.secrets:
            content_parts.append("Secrets: " + "; ".join(npc.secrets))
        doc = SemanticDocument(
            id=f"npc_sheet:{campaign_id}:{_slug(npc.name)}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.NPC_SHEET,
            content="\n".join(content_parts),
            metadata={"npc_name": npc.name},
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX npc_entity campaign=%s name=%s", campaign_id, npc.name)

    def index_lore(self, campaign_id: str, *, content: str, metadata: dict[str, str]) -> None:
        """Index world lore as a WORLD_LORE-tagged document. No-op if content is empty."""
        if not content.strip():
            return
        doc = SemanticDocument(
            id=f"world_lore:{campaign_id}:{_hash(content)}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.WORLD_LORE,
            content=content,
            metadata=dict(metadata),
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX lore campaign=%s len=%d", campaign_id, len(content))

    def index_revealed_fact(self, campaign_id: str, *, fact: str) -> None:
        """Index a newly-revealed fact as a PAST_EVENT-tagged document. No-op on empty/whitespace."""
        if not fact.strip():
            return
        doc = SemanticDocument(
            id=f"past_event:fact_{campaign_id}_{_hash(fact)}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.PAST_EVENT,
            content=fact,
            metadata={"source": "beat_completion"},
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX fact campaign=%s len=%d", campaign_id, len(fact))
