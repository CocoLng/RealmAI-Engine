"""Shared test fixtures for RealmAI-Engine."""

import pytest
from sqlalchemy.orm import Session

from db.database import Base, get_engine, get_session_factory
from engine.character import AbilityScores, Character, CharacterClass, Race, create_character
from engine.inventory import (
    DamageType,
    Inventory,
    ItemType,
    Rarity,
    Weapon,
    WeaponCategory,
    add_item,
    create_inventory,
)
from memory.models import CompressedSummary, ExchangeRole, NarrativeExchange
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC, NPCDisposition
from world.quest import Quest, QuestObjective, QuestStatus


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_engine():
    """In-memory SQLite engine for tests."""
    engine = get_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine) -> Session:  # type: ignore[type-arg]
    """Fresh DB session, rolled back after each test."""
    factory = get_session_factory(db_engine)
    session = factory()
    yield session  # type: ignore[misc]
    session.rollback()
    session.close()


# ---------------------------------------------------------------------------
# Sample domain objects
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_ability_scores() -> AbilityScores:
    """Standard ability scores for testing."""
    return AbilityScores(STR=15, DEX=14, CON=13, INT=12, WIS=10, CHA=8)


@pytest.fixture()
def sample_campaign() -> Campaign:
    """A test campaign with a fixed ID."""
    return Campaign(id="test-campaign-001", name="Lost Mines of Phandelver")


@pytest.fixture()
def sample_npc(sample_ability_scores: AbilityScores) -> NPC:
    """A test NPC (friendly dwarf fighter)."""
    return NPC(
        name="Gundren Rockseeker",
        race=Race.DWARF,
        char_class=CharacterClass.FIGHTER,
        level=5,
        ability_scores=sample_ability_scores,
        hp=35,
        max_hp=40,
        ac=16,
        disposition=NPCDisposition.FRIENDLY,
        description="A stout dwarf with a braided beard",
        personality="Eager and optimistic",
        location_name="Neverwinter",
    )


@pytest.fixture()
def sample_location() -> Location:
    """A test location with connections and NPCs."""
    return Location(
        name="Neverwinter",
        description="A bustling coastal city",
        connections=["Phandalin", "Triboar"],
        npcs_present=["Gundren Rockseeker"],
        items_available=["Healing Potion"],
    )


@pytest.fixture()
def sample_quest() -> Quest:
    """A test quest with objectives."""
    return Quest(
        title="Find the Lost Mine",
        description="Locate Wave Echo Cave",
        status=QuestStatus.ACTIVE,
        objectives=[
            QuestObjective(description="Talk to Gundren"),
            QuestObjective(description="Travel to Phandalin", is_complete=True),
        ],
        reward_xp=500,
        reward_gold=100,
        giver_npc="Gundren Rockseeker",
    )


@pytest.fixture()
def sample_character() -> Character:
    """A test character (Dwarf Fighter level 5)."""
    return create_character(
        name="Thorin",
        race=Race.DWARF,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(STR=16, DEX=12, CON=14, INT=10, WIS=13, CHA=8),
    )


@pytest.fixture()
def sample_inventory() -> Inventory:
    """A test inventory with a weapon."""
    inv = create_inventory()
    sword = Weapon(
        name="Longsword",
        item_type=ItemType.WEAPON,
        weight=3.0,
        value_gp=15,
        rarity=Rarity.COMMON,
        description="A standard longsword",
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        properties=[],
    )
    inv = add_item(inv, sword)
    return inv


@pytest.fixture()
def sample_exchange(sample_campaign: Campaign) -> NarrativeExchange:
    """A test narrative exchange (player action)."""
    return NarrativeExchange(
        campaign_id=sample_campaign.id,
        role=ExchangeRole.PLAYER,
        content="I attack the goblin with my longsword.",
        interaction_number=1,
    )


@pytest.fixture()
def sample_summary(sample_campaign: Campaign) -> CompressedSummary:
    """A test compressed summary."""
    return CompressedSummary(
        campaign_id=sample_campaign.id,
        summary_text="The party arrived at Phandalin and met Gundren at the inn.",
        start_interaction=1,
        end_interaction=20,
    )
