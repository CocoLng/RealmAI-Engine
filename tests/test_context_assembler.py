"""Tests for memory/context_assembler.py — full assembly integration."""

import json
from unittest.mock import MagicMock, patch

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
    @patch("memory.summarizer.OpenAI")
    def test_assemble_produces_all_sections(
        self, mock_openai_cls: MagicMock,
        db_session: Session, campaign_with_data: Campaign,
        semantic_memory: SemanticMemory,
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

        assembler = ContextAssembler(db_session, semantic_memory)
        result = assembler.assemble(campaign.id, "I look around")

        assert "[GAME STATE]" in result
        assert "[RECENT NARRATIVE]" in result
        assert "[RELEVANT LORE]" in result
        assert campaign.name in result

    @patch("memory.summarizer.OpenAI")
    def test_record_exchange(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        assembler = ContextAssembler(db_session, semantic_memory)
        exchange = assembler.record_exchange(
            sample_campaign.id, ExchangeRole.PLAYER, "Hello", 1,
        )
        db_session.commit()

        assert exchange.role == ExchangeRole.PLAYER
        assert exchange.content == "Hello"

        result = assembler.assemble(sample_campaign.id, "test")
        assert "Hello" in result

    @patch("memory.summarizer.OpenAI")
    def test_auto_summarization_triggered(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {"summary": "The party explored the area."}
        )
        mock_client.chat.completions.create.return_value = mock_response

        exchange_repo = ExchangeRepository(db_session)
        for i in range(1, 26):
            exchange_repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id, role=ExchangeRole.PLAYER,
                content=f"Action {i}", interaction_number=i,
            ))
        db_session.commit()

        assembler = ContextAssembler(db_session, semantic_memory)
        result = assembler.assemble(sample_campaign.id, "test")

        assert "[SESSION HISTORY]" in result
        assert "explored" in result

    @patch("memory.summarizer.OpenAI")
    def test_respects_total_budget(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        budget = ContextBudget(
            layer1_max=100, layer2_max=100,
            layer3_max=100, layer4_max=100, total_max=300,
        )
        assembler = ContextAssembler(db_session, semantic_memory, budget=budget)
        result = assembler.assemble(sample_campaign.id, "test")

        assert estimate_tokens(result) <= 300

    @patch("memory.summarizer.OpenAI")
    def test_assemble_without_semantic_results(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        assembler = ContextAssembler(db_session, semantic_memory)
        result = assembler.assemble(sample_campaign.id, "test")

        assert "[GAME STATE]" in result
        assert "[RELEVANT LORE]" not in result
