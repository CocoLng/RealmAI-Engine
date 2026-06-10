"""Tests for memory/context_assembler.py — full assembly integration."""

from unittest.mock import MagicMock

import chromadb
import pytest
from chromadb.config import Settings
from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.quest_repo import QuestRepository
from memory.context_assembler import ContextAssembler
from memory.models import (
    ContextBudget,
    ExchangeRole,
    NarrativeExchange,
    SemanticDocument,
    SemanticDocumentType,
)
from memory.semantic import SemanticMemory
from memory.token_utils import estimate_tokens
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC
from world.quest import Quest


@pytest.fixture()
def semantic_memory() -> SemanticMemory:  # type: ignore[misc]
    client = chromadb.EphemeralClient(
        settings=Settings(allow_reset=True),
    )
    client.reset()
    mem = SemanticMemory(client=client)
    yield mem  # type: ignore[misc]
    client.reset()


@pytest.fixture()
def mock_ollama_client() -> MagicMock:
    """Mock OllamaClient for summarizer."""
    client = MagicMock()
    client.chat_json.return_value = {"summary": "The party explored the area."}
    return client


@pytest.fixture()
def campaign_with_data(
    db_session: Session, sample_campaign: Campaign,
    sample_location: Location, sample_npc: NPC, sample_quest: Quest,
) -> Campaign:
    campaign = sample_campaign.model_copy(update={"current_location": "Neverwinter"})
    CampaignRepository(db_session).save(campaign)
    LocationRepository(db_session).save(sample_location, campaign.id)
    NPCRepository(db_session).save(sample_npc, campaign.id)
    QuestRepository(db_session).save(sample_quest, campaign.id)
    db_session.commit()
    return campaign


class TestContextAssembler:
    def test_assemble_produces_all_sections(
        self, db_session: Session, campaign_with_data: Campaign,
        semantic_memory: SemanticMemory, mock_ollama_client: MagicMock,
    ) -> None:
        campaign = campaign_with_data
        exchange_repo = ExchangeRepository(db_session)
        for i in range(1, 4):
            exchange_repo.save(NarrativeExchange(
                campaign_id=campaign.id, role=ExchangeRole.PLAYER,
                content=f"Player action {i}", interaction_number=i,
            ))
        db_session.commit()

        semantic_memory.add_document(SemanticDocument(
            campaign_id=campaign.id,
            doc_type=SemanticDocumentType.WORLD_LORE,
            content="The Sword Coast is a dangerous region.",
        ))

        assembler = ContextAssembler(db_session, semantic_memory, mock_ollama_client)
        result = assembler.assemble(campaign.id, "I look around")

        assert "[GAME STATE]" in result
        assert "[RECENT NARRATIVE]" in result
        assert "[RELEVANT LORE]" in result
        assert campaign.name in result

    def test_record_exchange(
        self, db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory, mock_ollama_client: MagicMock,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        assembler = ContextAssembler(db_session, semantic_memory, mock_ollama_client)
        exchange = assembler.record_exchange(
            sample_campaign.id, ExchangeRole.PLAYER, "Hello", 1,
        )
        db_session.commit()

        assert exchange.role == ExchangeRole.PLAYER
        assert exchange.content == "Hello"

        result = assembler.assemble(sample_campaign.id, "test")
        assert "Hello" in result

    def test_auto_summarization_triggered(
        self, db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory, mock_ollama_client: MagicMock,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        exchange_repo = ExchangeRepository(db_session)
        for i in range(1, 26):
            exchange_repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id, role=ExchangeRole.PLAYER,
                content=f"Action {i}", interaction_number=i,
            ))
        db_session.commit()

        assembler = ContextAssembler(db_session, semantic_memory, mock_ollama_client)
        result = assembler.assemble(sample_campaign.id, "test")

        assert "[SESSION HISTORY]" in result
        assert "explored" in result

    def test_respects_total_budget(
        self, db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory, mock_ollama_client: MagicMock,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        budget = ContextBudget(
            layer1_max=100, layer2_max=100,
            layer3_max=100, layer4_max=100, total_max=300,
        )
        assembler = ContextAssembler(
            db_session, semantic_memory, mock_ollama_client, budget=budget,
        )
        result = assembler.assemble(sample_campaign.id, "test")

        assert estimate_tokens(result) <= 300

    def test_assemble_without_semantic_results(
        self, db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory, mock_ollama_client: MagicMock,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        assembler = ContextAssembler(db_session, semantic_memory, mock_ollama_client)
        result = assembler.assemble(sample_campaign.id, "test")

        assert "[GAME STATE]" in result
        assert "[RELEVANT LORE]" not in result

    def test_truncation_clamp_enforces_budget(
        self, db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory, mock_ollama_client: MagicMock,
    ) -> None:
        """Verify the final clamp prevents off-by-rounding overflows."""
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        # Add many verbose exchanges and lore to push all layers over budget
        exchange_repo = ExchangeRepository(db_session)
        long_text = "The adventurers marched through the dark forest encountering many dangers. " * 5
        for i in range(1, 13):
            exchange_repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id, role=ExchangeRole.NARRATOR,
                content=long_text, interaction_number=i,
            ))
        db_session.commit()

        for _ in range(5):
            semantic_memory.add_document(SemanticDocument(
                campaign_id=sample_campaign.id,
                doc_type=SemanticDocumentType.WORLD_LORE,
                content=long_text,
            ))

        budget = ContextBudget(
            layer1_max=80, layer2_max=80,
            layer3_max=80, layer4_max=80, total_max=250,
        )
        assembler = ContextAssembler(
            db_session, semantic_memory, mock_ollama_client, budget=budget,
        )
        result = assembler.assemble(sample_campaign.id, "forest dangers")

        assert estimate_tokens(result) <= 250

    def test_budget_truncation_keeps_newest_exchanges(
        self, db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory, mock_ollama_client: MagicMock,
    ) -> None:
        """When the total budget forces layer 2 truncation, the most
        RECENT exchanges survive — not the oldest ones."""
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        exchange_repo = ExchangeRepository(db_session)
        for i in range(1, 13):
            exchange_repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id, role=ExchangeRole.NARRATOR,
                content=(
                    f"Narration update number {i} where the heroes pressed "
                    "onward through danger and shadow without rest."
                ),
                interaction_number=i,
            ))
        db_session.commit()

        budget = ContextBudget(
            layer1_max=80, layer2_max=400,
            layer3_max=80, layer4_max=80, total_max=200,
        )
        assembler = ContextAssembler(
            db_session, semantic_memory, mock_ollama_client, budget=budget,
        )
        result = assembler.assemble(sample_campaign.id, "onward")

        assert estimate_tokens(result) <= 200
        assert "number 12" in result
        assert "number 1 " not in result


class TestRagQueryUsesRollingWindow:
    """The RAG query must include the last 2-3 narrative exchanges, not just the current input."""

    def test_build_rag_query_combines_recent_window_and_current_input(self) -> None:
        """The static helper directly produces the combined query string."""
        recent = [
            NarrativeExchange(
                campaign_id="cmp_1",
                role=ExchangeRole.PLAYER,
                content="Je fouille la pièce.",
                interaction_number=1,
            ),
            NarrativeExchange(
                campaign_id="cmp_1",
                role=ExchangeRole.NARRATOR,
                content="Tu trouves un coffre verrouillé sous le lit.",
                interaction_number=2,
            ),
        ]
        query = ContextAssembler._build_rag_query("Je crochette le coffre.", recent)
        assert "Je fouille" in query
        assert "coffre verrouillé" in query
        assert "crochette" in query

    def test_build_rag_query_with_empty_window_uses_only_input(self) -> None:
        query = ContextAssembler._build_rag_query("Quelque chose.", [])
        assert "Quelque chose." in query

    def test_build_rag_query_uses_last_three_exchanges_only(self) -> None:
        many = [
            NarrativeExchange(
                campaign_id="cmp_1",
                role=ExchangeRole.PLAYER,
                content=f"Exchange number {i}",
                interaction_number=i,
            )
            for i in range(10)
        ]
        query = ContextAssembler._build_rag_query("current", many)
        # Last 3 should be present
        assert "Exchange number 7" in query
        assert "Exchange number 8" in query
        assert "Exchange number 9" in query
        # Earlier should NOT
        assert "Exchange number 0" not in query
        assert "Exchange number 6" not in query
