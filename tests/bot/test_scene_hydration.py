"""Lot G — tests for bot/scene_hydration.py.

Covers the launch-time NPC hydration helper and the PICKUP transfer helper.
Uses an in-memory SQLite session via the `db` package's real factories so we
exercise actual ``NPCRepository`` / ``LocationRepository`` writes (mocking
those would defeat the purpose of these tests).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.scene_hydration import hydrate_scene, take_scene_item
from db.database import Base
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.inventory import Inventory
from world.campaign import Campaign
from world.location import Location


@pytest.fixture()
def db_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session


def _make_session(*, location: Location | None = None, characters=None):
    campaign = Campaign(name="Test")
    return SimpleNamespace(
        campaign=campaign,
        current_location=location,
        npcs={},
        characters=characters or {},
        inventories={},
        spellcasters={},
    )


def _persist_campaign_and_location(db_factory, session):
    """Persist the campaign + current location so FK constraints hold."""
    from db.mappers import campaign_to_db
    from db.repositories.location_repo import LocationRepository

    db = db_factory()
    try:
        db.add(campaign_to_db(session.campaign))
        if session.current_location is not None:
            LocationRepository(db).save(
                session.current_location, session.campaign.id,
            )
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# hydrate_scene
# ---------------------------------------------------------------------------


def test_hydrate_creates_missing_npcs(db_factory) -> None:
    location = Location(
        name="Place",
        npcs_present=["Jeanne", "Père Thomas"],
    )
    session = _make_session(location=location)
    _persist_campaign_and_location(db_factory, session)

    hydrate_scene(session, db_factory=db_factory)

    assert set(session.npcs.keys()) == {"Jeanne", "Père Thomas"}
    db = db_factory()
    try:
        rows = NPCRepository(db).list_by_location("Place", session.campaign.id)
    finally:
        db.close()
    assert {r.name for r in rows} == {"Jeanne", "Père Thomas"}


def test_hydrate_is_idempotent(db_factory) -> None:
    location = Location(name="Place", npcs_present=["Jeanne"])
    session = _make_session(location=location)
    _persist_campaign_and_location(db_factory, session)

    hydrate_scene(session, db_factory=db_factory)
    hydrate_scene(session, db_factory=db_factory)

    db = db_factory()
    try:
        rows = NPCRepository(db).list_by_location("Place", session.campaign.id)
    finally:
        db.close()
    assert len(rows) == 1


def test_hydrate_empty_npcs_list(db_factory) -> None:
    location = Location(name="Bridge", npcs_present=[])
    session = _make_session(location=location)
    _persist_campaign_and_location(db_factory, session)

    hydrate_scene(session, db_factory=db_factory)
    assert session.npcs == {}


def test_hydrate_no_location_is_noop(db_factory) -> None:
    session = _make_session(location=None)
    hydrate_scene(session, db_factory=db_factory)  # must not raise
    assert session.npcs == {}


# ---------------------------------------------------------------------------
# take_scene_item
# ---------------------------------------------------------------------------


def _make_session_with_player(db_factory) -> SimpleNamespace:
    from db.mappers import campaign_to_db, player_character_to_db

    location = Location(
        name="Place",
        items_available=["Clé de Fer", "Lanterne"],
    )
    scores = AbilityScores(STR=12, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    char = create_character("Hero", Race.HUMAN, CharacterClass.FIGHTER, scores)
    inv = Inventory()
    session = _make_session(location=location, characters={42: char})
    session.inventories[42] = inv

    db = db_factory()
    try:
        db.add(campaign_to_db(session.campaign))
        LocationRepository(db).save(location, session.campaign.id)
        db.add(
            player_character_to_db(42, session.campaign.id, char, inv, None),
        )
        db.commit()
    finally:
        db.close()
    return session


def test_take_scene_item_moves_item(db_factory) -> None:
    session = _make_session_with_player(db_factory)

    item = take_scene_item(
        session, item_name="Clé de Fer", user_id=42, db_factory=db_factory,
    )

    assert item is not None
    assert item.name == "Clé de Fer"
    assert "Clé de Fer" not in session.current_location.items_available
    assert any(i.name == "Clé de Fer" for i in session.inventories[42].items)

    # Persisted: location row updated.
    db = db_factory()
    try:
        loc = LocationRepository(db).get_by_name("Place", session.campaign.id)
    finally:
        db.close()
    assert loc is not None
    assert "Clé de Fer" not in loc.items_available


def test_take_scene_item_unknown_returns_none(db_factory) -> None:
    session = _make_session_with_player(db_factory)
    item = take_scene_item(
        session, item_name="Bâton", user_id=42, db_factory=db_factory,
    )
    assert item is None


# ---------------------------------------------------------------------------
# PICKUP entity resolver
# ---------------------------------------------------------------------------


def test_pickup_resolver_matches_scene_item() -> None:
    from ai.entity_resolver import _resolve_pickup
    from ai.models import InterpretedAction
    from engine.validators import ActionType

    loc = Location(name="Place", items_available=["Clé de Fer", "Lanterne"])
    action = InterpretedAction(
        action_type=ActionType.PICKUP,
        actor_name="Hero",
        target_name="clé de fer",
        raw_input="je ramasse la clé de fer",
        confidence=0.9,
    )
    result = _resolve_pickup(action, loc)
    assert result.status == "resolved"
    assert result.resolved_entity == "Clé de Fer"


def test_pickup_resolver_unknown_when_absent() -> None:
    from ai.entity_resolver import _resolve_pickup
    from ai.models import InterpretedAction
    from engine.validators import ActionType

    loc = Location(name="Place", items_available=["Clé de Fer"])
    action = InterpretedAction(
        action_type=ActionType.PICKUP,
        actor_name="Hero",
        target_name="dragon",
        raw_input="je ramasse le dragon",
        confidence=0.9,
    )
    result = _resolve_pickup(action, loc)
    assert result.status == "unknown"


def test_pickup_validator_accepts_target_or_item() -> None:
    from engine.validators import (
        Action,
        ActionType,
        validate_exploration_action,
    )

    a = Action(actor_name="Hero", action_type=ActionType.PICKUP, target_name="Clé")
    assert validate_exploration_action(a).is_valid

    b = Action(actor_name="Hero", action_type=ActionType.PICKUP, item_name="Clé")
    assert validate_exploration_action(b).is_valid

    c = Action(actor_name="Hero", action_type=ActionType.PICKUP)
    assert not validate_exploration_action(c).is_valid
