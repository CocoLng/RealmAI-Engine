"""Tests for bot/persistence.py — session snapshot writes.

Uses a real in-memory SQLite session so the repository writes actually run.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.persistence import persist_session
from db.database import Base
from db.repositories import CampaignRepository, NPCRepository
from engine.character import AbilityScores, Race
from world.campaign import Campaign
from world.npc import NPC


@pytest.fixture()
def db_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _make_session(db_factory, *, npcs=None):
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
        npcs=dict(npcs or {}),
        story_arc=None,
        semantic_indexer=None,
    )


def _npc(name: str) -> NPC:
    return NPC(
        name=name,
        race=Race.HUMAN,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=8,
        max_hp=8,
        ac=10,
        location_name="Place",
    )


def test_persist_session_writes_campaign(db_factory) -> None:
    session = _make_session(db_factory)
    session.campaign.current_location = "Bastion"

    persist_session(db_factory, session)

    db = db_factory()
    try:
        stored = CampaignRepository(db).get_by_id(session.campaign.id)
    finally:
        db.close()
    assert stored is not None
    assert stored.current_location == "Bastion"


def test_persist_session_upserts_npcs(db_factory) -> None:
    npc = _npc("Jeanne")
    session = _make_session(db_factory, npcs={"Jeanne": npc})

    persist_session(db_factory, session)

    db = db_factory()
    try:
        stored = NPCRepository(db).get_by_name("Jeanne", session.campaign.id)
    finally:
        db.close()
    assert stored is not None
