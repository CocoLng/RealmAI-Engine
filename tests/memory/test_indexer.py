"""Unit tests for SemanticIndexer."""

import pytest
from unittest.mock import MagicMock

from ai.models import NPCSheet
from memory.indexer import SemanticIndexer
from memory.models import SemanticDocument, SemanticDocumentType
from memory.semantic import SemanticMemory
from world.quest import Quest, QuestObjective, QuestStatus
from world.story_arc import StoryBeat


@pytest.fixture
def fake_semantic() -> MagicMock:
    return MagicMock(spec=SemanticMemory)


@pytest.fixture
def indexer(fake_semantic: MagicMock) -> SemanticIndexer:
    return SemanticIndexer(fake_semantic)


class TestIndexBeat:
    def test_index_beat_adds_past_event_document(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        beat = StoryBeat(
            beat_number=1,
            title="Le Mur qui Soupire",
            description="The party finds an ancient breathing wall.",
            location_hint="Old Ruins",
            npc_names=["Aldric"],
            encounter_type="puzzle",
        )
        indexer.index_beat("cmp_1", beat)
        fake_semantic.add_document.assert_called_once()
        doc = fake_semantic.add_document.call_args.args[0]
        assert isinstance(doc, SemanticDocument)
        assert doc.campaign_id == "cmp_1"
        assert doc.doc_type == SemanticDocumentType.PAST_EVENT
        assert "Le Mur qui Soupire" in doc.content
        assert "breathing wall" in doc.content

    def test_index_beat_id_is_idempotent(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        """Same campaign + beat_number → same document ID."""
        beat = StoryBeat(
            beat_number=3,
            title="Test Beat",
            description="A description.",
            location_hint="Somewhere",
            encounter_type="exploration",
        )
        indexer.index_beat("cmp_1", beat)
        first_id = fake_semantic.add_document.call_args.args[0].id
        fake_semantic.reset_mock()
        indexer.index_beat("cmp_1", beat)
        second_id = fake_semantic.add_document.call_args.args[0].id
        assert first_id == second_id


class TestIndexNPC:
    def test_index_npc_adds_npc_sheet_document(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        sheet = NPCSheet(
            personality="Stoic and watchful",
            description="An old elven mage with silver hair.",
            secrets=["Knows the location of the lost tome."],
            knowledge=["Has lived in this region for centuries."],
        )
        indexer.index_npc("cmp_1", "Aldric", sheet)
        fake_semantic.add_document.assert_called_once()
        doc = fake_semantic.add_document.call_args.args[0]
        assert doc.doc_type == SemanticDocumentType.NPC_SHEET
        assert "Aldric" in doc.content
        assert "Stoic" in doc.content
        assert doc.metadata.get("npc_name") == "Aldric"

    def test_index_npc_id_is_idempotent(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        sheet = NPCSheet(
            personality="P", description="D",
            secrets=["S"], knowledge=["K"],
        )
        indexer.index_npc("cmp_1", "Aldric", sheet)
        first_id = fake_semantic.add_document.call_args.args[0].id
        fake_semantic.reset_mock()
        indexer.index_npc("cmp_1", "Aldric", sheet)
        second_id = fake_semantic.add_document.call_args.args[0].id
        assert first_id == second_id


class TestIndexLocation:
    def test_index_location_adds_location_detail_document(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        from world.location import Location
        loc = Location(
            name="Goblin Cave",
            description="A dank cave with dripping water.",
        )
        indexer.index_location("cmp_1", loc)
        fake_semantic.add_document.assert_called_once()
        doc = fake_semantic.add_document.call_args.args[0]
        assert doc.doc_type == SemanticDocumentType.LOCATION_DETAIL
        assert "Goblin Cave" in doc.content
        assert "dank" in doc.content


class TestIndexLore:
    def test_index_lore_adds_world_lore_document(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        indexer.index_lore(
            "cmp_1",
            content="The kingdom of Eldoria fell three centuries ago.",
            metadata={"topic": "history"},
        )
        fake_semantic.add_document.assert_called_once()
        doc = fake_semantic.add_document.call_args.args[0]
        assert doc.doc_type == SemanticDocumentType.WORLD_LORE
        assert "Eldoria" in doc.content
        assert doc.metadata.get("topic") == "history"


class TestIndexRevealedFact:
    def test_index_revealed_fact_adds_past_event(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        indexer.index_revealed_fact(
            "cmp_1", fact="The wall breaks open, revealing a passage east.",
        )
        fake_semantic.add_document.assert_called_once()
        doc = fake_semantic.add_document.call_args.args[0]
        assert doc.doc_type == SemanticDocumentType.PAST_EVENT
        assert "wall breaks" in doc.content


class TestIndexerHandlesEmpty:
    def test_indexing_empty_lore_string_is_a_no_op(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        indexer.index_lore("cmp_1", content="", metadata={})
        fake_semantic.add_document.assert_not_called()

    def test_indexing_empty_revealed_fact_is_a_no_op(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        indexer.index_revealed_fact("cmp_1", fact="   ")
        fake_semantic.add_document.assert_not_called()


class TestIndexQuest:
    def test_index_quest_adds_quest_detail_document(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        quest = Quest(
            title="Retrieve the lost map",
            description="Retrieve the lost map of Eldoria.",
            status=QuestStatus.ACTIVE,
            objectives=[
                QuestObjective(description="Find the smuggler", is_complete=True),
                QuestObjective(description="Recover the map"),
            ],
        )
        indexer.index_quest("cmp_1", quest)
        fake_semantic.add_document.assert_called_once()
        doc = fake_semantic.add_document.call_args.args[0]
        assert doc.doc_type == SemanticDocumentType.QUEST_DETAIL
        assert "lost map" in doc.content
        assert "Recover the map" in doc.content
        assert doc.metadata.get("quest_id") == "Retrieve the lost map"
        assert doc.metadata.get("status") == "active"
        assert doc.id == "quest_detail:cmp_1:retrieve_the_lost_map"

    def test_index_quest_without_content_is_a_no_op(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        indexer.index_quest("cmp_1", Quest(title="Placeholder"))
        fake_semantic.add_document.assert_not_called()
