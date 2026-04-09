"""Layer 4 — Semantic RAG using ChromaDB.

Stores world lore, NPC sheets, past events for retrieval by
semantic similarity. One ChromaDB collection per campaign.
Uses default all-MiniLM-L6-v2 embedding model.
"""

import logging

import chromadb

from memory.models import SemanticDocument, SemanticDocumentType
from memory.token_utils import truncate_to_tokens

logger = logging.getLogger(__name__)


class SemanticMemory:
    """Semantic RAG memory using ChromaDB (Layer 4)."""

    def __init__(
        self,
        persist_directory: str = "data/chromadb",
        client: chromadb.ClientAPI | None = None,
    ) -> None:
        self._client = client or chromadb.PersistentClient(path=persist_directory)

    def _get_collection(self, campaign_id: str) -> chromadb.Collection:  # type: ignore[type-arg]
        """Get or create the collection for a campaign."""
        return self._client.get_or_create_collection(
            name=f"campaign_{campaign_id}",
            metadata={"hnsw:space": "cosine"},
        )

    def add_document(self, document: SemanticDocument) -> None:
        """Add a single document to the campaign's collection."""
        collection = self._get_collection(document.campaign_id)
        collection.add(
            ids=[document.id],
            documents=[document.content],
            metadatas=[{"doc_type": document.doc_type.value, **document.metadata}],
        )

    def add_documents(self, documents: list[SemanticDocument]) -> None:
        """Batch add documents. All must belong to the same campaign."""
        if not documents:
            return
        campaign_ids = {d.campaign_id for d in documents}
        if len(campaign_ids) > 1:
            raise ValueError(
                f"add_documents requires all documents to share the same campaign_id, "
                f"got: {campaign_ids}"
            )
        campaign_id = documents[0].campaign_id
        collection = self._get_collection(campaign_id)
        collection.add(
            ids=[d.id for d in documents],
            documents=[d.content for d in documents],
            metadatas=[
                {"doc_type": d.doc_type.value, **d.metadata} for d in documents
            ],
        )

    def query(
        self,
        campaign_id: str,
        query_text: str,
        n_results: int = 3,
        doc_type: SemanticDocumentType | None = None,
    ) -> list[SemanticDocument]:
        """Query by semantic similarity. Optionally filter by doc_type."""
        try:
            collection = self._client.get_collection(f"campaign_{campaign_id}")
        except Exception:
            logger.warning(
                "Collection campaign_%s not found or ChromaDB error",
                campaign_id,
                exc_info=True,
            )
            return []

        where_filter = {"doc_type": doc_type.value} if doc_type else None

        count = collection.count()
        if count == 0:
            return []
        actual_n = min(n_results, count)

        results = collection.query(
            query_texts=[query_text],
            n_results=actual_n,
            where=where_filter,  # type: ignore[arg-type]
        )

        documents: list[SemanticDocument] = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                meta = dict(results["metadatas"][0][i]) if results["metadatas"] else {}
                doc_type_val = meta.pop("doc_type", "world_lore")
                documents.append(
                    SemanticDocument(
                        id=doc_id,
                        campaign_id=campaign_id,
                        doc_type=SemanticDocumentType(doc_type_val),  # type: ignore[arg-type]
                        content=results["documents"][0][i],  # type: ignore[index]
                        metadata=dict(meta),  # type: ignore[arg-type]
                    )
                )

        return documents

    def render(
        self, documents: list[SemanticDocument], max_tokens: int = 350
    ) -> str:
        """Render retrieved documents into a text block for the prompt."""
        if not documents:
            return ""
        lines = ["[RELEVANT LORE]"]
        for doc in documents:
            lines.append(f"- {doc.content}")
        text = "\n".join(lines)
        return truncate_to_tokens(text, max_tokens)

    def delete_campaign(self, campaign_id: str) -> None:
        """Delete the entire collection for a campaign."""
        try:
            self._client.delete_collection(f"campaign_{campaign_id}")
        except Exception:
            logger.warning(
                "Collection campaign_%s not found for deletion",
                campaign_id,
                exc_info=True,
            )
