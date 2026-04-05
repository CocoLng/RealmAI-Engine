"""Tests for memory/semantic.py — Layer 4 semantic RAG."""

import chromadb
import pytest
from chromadb.config import Settings

from memory.models import SemanticDocument, SemanticDocumentType
from memory.semantic import SemanticMemory
from memory.token_utils import estimate_tokens


@pytest.fixture()
def ephemeral_chromadb() -> chromadb.ClientAPI:
    """Fresh ephemeral ChromaDB client per test (isolated via unique tenant)."""
    client = chromadb.EphemeralClient(
        settings=Settings(allow_reset=True),
    )
    client.reset()
    return client


@pytest.fixture()
def semantic_memory(ephemeral_chromadb: chromadb.ClientAPI) -> SemanticMemory:
    return SemanticMemory(client=ephemeral_chromadb)


class TestSemanticMemory:
    def test_add_and_query(self, semantic_memory: SemanticMemory) -> None:
        doc = SemanticDocument(
            campaign_id="c1",
            doc_type=SemanticDocumentType.NPC_SHEET,
            content="Gundren Rockseeker is a dwarf prospector who discovered Wave Echo Cave.",
            metadata={"npc_name": "Gundren"},
        )
        semantic_memory.add_document(doc)
        results = semantic_memory.query("c1", "Who is Gundren?", n_results=1)
        assert len(results) == 1
        assert "Gundren" in results[0].content

    def test_query_returns_relevant_results(
        self, semantic_memory: SemanticMemory
    ) -> None:
        docs = [
            SemanticDocument(
                campaign_id="c1",
                doc_type=SemanticDocumentType.WORLD_LORE,
                content="Neverwinter is a bustling port city on the Sword Coast.",
            ),
            SemanticDocument(
                campaign_id="c1",
                doc_type=SemanticDocumentType.NPC_SHEET,
                content="The Black Spider is a drow mage seeking Wave Echo Cave.",
            ),
            SemanticDocument(
                campaign_id="c1",
                doc_type=SemanticDocumentType.LOCATION_DETAIL,
                content="Cragmaw Hideout is a goblin cave near the Triboar Trail.",
            ),
        ]
        semantic_memory.add_documents(docs)
        results = semantic_memory.query("c1", "Tell me about the goblins", n_results=1)
        assert len(results) == 1
        assert "goblin" in results[0].content.lower()

    def test_query_with_doc_type_filter(
        self, semantic_memory: SemanticMemory
    ) -> None:
        docs = [
            SemanticDocument(
                campaign_id="c1",
                doc_type=SemanticDocumentType.NPC_SHEET,
                content="Gundren is a dwarf.",
            ),
            SemanticDocument(
                campaign_id="c1",
                doc_type=SemanticDocumentType.WORLD_LORE,
                content="Dwarves are a sturdy folk.",
            ),
        ]
        semantic_memory.add_documents(docs)
        results = semantic_memory.query(
            "c1",
            "dwarf",
            n_results=5,
            doc_type=SemanticDocumentType.NPC_SHEET,
        )
        assert all(r.doc_type == SemanticDocumentType.NPC_SHEET for r in results)

    def test_query_empty_collection(self, semantic_memory: SemanticMemory) -> None:
        results = semantic_memory.query("nonexistent", "anything")
        assert results == []

    def test_campaign_scoping(self, semantic_memory: SemanticMemory) -> None:
        semantic_memory.add_document(
            SemanticDocument(
                campaign_id="c1",
                doc_type=SemanticDocumentType.WORLD_LORE,
                content="Lore for campaign 1.",
            )
        )
        semantic_memory.add_document(
            SemanticDocument(
                campaign_id="c2",
                doc_type=SemanticDocumentType.WORLD_LORE,
                content="Lore for campaign 2.",
            )
        )
        results_c1 = semantic_memory.query("c1", "lore", n_results=5)
        results_c2 = semantic_memory.query("c2", "lore", n_results=5)
        assert len(results_c1) == 1
        assert "campaign 1" in results_c1[0].content
        assert len(results_c2) == 1
        assert "campaign 2" in results_c2[0].content

    def test_delete_campaign(self, semantic_memory: SemanticMemory) -> None:
        semantic_memory.add_document(
            SemanticDocument(
                campaign_id="c1",
                doc_type=SemanticDocumentType.WORLD_LORE,
                content="Some lore.",
            )
        )
        semantic_memory.delete_campaign("c1")
        results = semantic_memory.query("c1", "lore")
        assert results == []

    def test_render(self, semantic_memory: SemanticMemory) -> None:
        docs = [
            SemanticDocument(
                campaign_id="c1",
                doc_type=SemanticDocumentType.NPC_SHEET,
                content="Gundren is a dwarf prospector.",
            ),
            SemanticDocument(
                campaign_id="c1",
                doc_type=SemanticDocumentType.WORLD_LORE,
                content="Neverwinter is a port city.",
            ),
        ]
        text = semantic_memory.render(docs)
        assert "[RELEVANT LORE]" in text
        assert "Gundren is a dwarf prospector." in text
        assert "Neverwinter is a port city." in text

    def test_render_within_budget(self, semantic_memory: SemanticMemory) -> None:
        docs = [
            SemanticDocument(
                campaign_id="c1",
                doc_type=SemanticDocumentType.WORLD_LORE,
                content="A very long piece of world lore that goes on and on " * 20,
            ),
        ]
        text = semantic_memory.render(docs, max_tokens=30)
        assert estimate_tokens(text) <= 30

    def test_render_empty(self, semantic_memory: SemanticMemory) -> None:
        assert semantic_memory.render([]) == ""
