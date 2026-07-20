"""Tests for bot/persistence.py — session snapshot writes + quest indexing.

Uses a real in-memory SQLite session so the repository writes actually run;
only the semantic indexer is faked (ChromaDB is out of scope here).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.persistence import persist_session
from db.database import Base
from db.repositories import CampaignRepository, QuestRepository
from memory.indexer import SemanticIndexer
from world.campaign import Campaign
from world.quest import Quest, QuestObjective, QuestStatus


@pytest.fixture()
def db_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _make_session(db_factory, *, quests=None, indexer=None):
    """Build a minimal session snapshot with its campaign already persisted."""
    campaign = Campaign(name="Test")
    db = db_factory()
    try:
        CampaignRepository(db).save(campaign)
        db.commit()
    finally:
        db.close()
    return SimpleNamespace(
        campaign=campaign,
        combat_state=None,
        current_location=None,
        characters={},
        inventories={},
        spellcasters={},
        npcs={},
        quests=list(quests or []),
        story_arc=None,
        semantic_indexer=indexer,
    )


def _quest() -> Quest:
    return Quest(
        title="La Carte Perdue",
        description="Retrouver la carte d'Eldoria volée par les contrebandiers.",
        status=QuestStatus.ACTIVE,
        objectives=[QuestObjective(description="Interroger le passeur")],
    )


def test_persist_session_indexes_quests(db_factory) -> None:
    """QUEST_DETAIL must be populated when quests are written (audit 2026-07-20)."""
    indexer = MagicMock(spec=SemanticIndexer)
    quest = _quest()
    session = _make_session(db_factory, quests=[quest], indexer=indexer)

    persist_session(db_factory, session)

    indexer.index_quest.assert_called_once_with(session.campaign.id, quest)


def test_persist_session_without_indexer_still_writes(db_factory) -> None:
    """Degraded mode (no ChromaDB): persistence works, nothing is indexed."""
    session = _make_session(db_factory, quests=[_quest()], indexer=None)

    persist_session(db_factory, session)

    db = db_factory()
    try:
        assert QuestRepository(db).get_by_title(
            "La Carte Perdue", session.campaign.id,
        ) is not None
    finally:
        db.close()


def test_indexing_failure_never_breaks_persistence(db_factory) -> None:
    """A ChromaDB blow-up must not lose the turn's DB writes."""
    indexer = MagicMock(spec=SemanticIndexer)
    indexer.index_quest.side_effect = RuntimeError("chroma down")
    session = _make_session(db_factory, quests=[_quest()], indexer=indexer)

    persist_session(db_factory, session)

    db = db_factory()
    try:
        assert QuestRepository(db).get_by_title(
            "La Carte Perdue", session.campaign.id,
        ) is not None
    finally:
        db.close()
