"""Tests for PlayerCharacterRepository — CRUD for player characters."""

import pytest
from sqlalchemy.orm import Session

from db.repositories.player_character_repo import PlayerCharacterRepository
from engine.character import Character, create_character, AbilityScores, Race, CharacterClass
from engine.inventory import Inventory, create_inventory
from engine.spells import SpellcasterState, create_spellcaster_state
from world.campaign import Campaign
from db.repositories.campaign_repo import CampaignRepository


@pytest.fixture()
def campaign(db_session: Session) -> Campaign:
    """Create and persist a campaign for FK references."""
    c = Campaign(id="camp-1", name="Test Campaign", player_names=["Alice"])
    repo = CampaignRepository(db_session)
    repo.save(c)
    db_session.flush()
    return c


@pytest.fixture()
def character() -> Character:
    """A sample fighter character."""
    scores = AbilityScores(STR=16, DEX=14, CON=13, INT=10, WIS=12, CHA=8)
    return create_character("Thorin", Race.DWARF, CharacterClass.FIGHTER, scores)


@pytest.fixture()
def wizard_character() -> Character:
    """A sample wizard character."""
    scores = AbilityScores(STR=8, DEX=14, CON=12, INT=16, WIS=13, CHA=10)
    return create_character("Elara", Race.ELF, CharacterClass.WIZARD, scores)


@pytest.fixture()
def inventory() -> Inventory:
    """An empty inventory."""
    return create_inventory()


@pytest.fixture()
def spellcaster() -> SpellcasterState | None:
    """Spellcaster state for a wizard."""
    return create_spellcaster_state(CharacterClass.WIZARD, 1)


USER_ID = 123456789


class TestPlayerCharacterRepositorySave:
    """Test save operations."""

    def test_save_and_get(
        self, db_session: Session, campaign: Campaign,
        character: Character, inventory: Inventory,
    ) -> None:
        repo = PlayerCharacterRepository(db_session)
        repo.save(USER_ID, campaign.id, character, inventory, None)
        db_session.flush()

        result = repo.get(USER_ID, campaign.id)
        assert result is not None
        loaded_char, loaded_inv, loaded_spell = result
        assert loaded_char.name == "Thorin"
        assert loaded_char.race == Race.DWARF
        assert loaded_inv.gold == 0
        assert loaded_spell is None

    def test_save_with_spellcaster(
        self, db_session: Session, campaign: Campaign,
        wizard_character: Character, inventory: Inventory,
        spellcaster: SpellcasterState | None,
    ) -> None:
        repo = PlayerCharacterRepository(db_session)
        repo.save(USER_ID, campaign.id, wizard_character, inventory, spellcaster)
        db_session.flush()

        result = repo.get(USER_ID, campaign.id)
        assert result is not None
        _, _, loaded_spell = result
        assert loaded_spell is not None
        assert loaded_spell.spellcasting_ability.value == "INT"


class TestPlayerCharacterRepositoryGet:
    """Test get operations."""

    def test_get_nonexistent(self, db_session: Session) -> None:
        repo = PlayerCharacterRepository(db_session)
        result = repo.get(999, "nonexistent")
        assert result is None

    def test_get_all_for_campaign(
        self, db_session: Session, campaign: Campaign,
        character: Character, wizard_character: Character,
        inventory: Inventory, spellcaster: SpellcasterState | None,
    ) -> None:
        repo = PlayerCharacterRepository(db_session)
        repo.save(USER_ID, campaign.id, character, inventory, None)
        repo.save(USER_ID + 1, campaign.id, wizard_character, inventory, spellcaster)
        db_session.flush()

        results = repo.get_all_for_campaign(campaign.id)
        assert len(results) == 2
        user_ids = {r[0] for r in results}
        assert user_ids == {USER_ID, USER_ID + 1}

    def test_get_all_empty_campaign(self, db_session: Session) -> None:
        repo = PlayerCharacterRepository(db_session)
        results = repo.get_all_for_campaign("nonexistent")
        assert results == []


class TestPlayerCharacterRepositoryUpdate:
    """Test update operations."""

    def test_update_character(
        self, db_session: Session, campaign: Campaign,
        character: Character, inventory: Inventory,
    ) -> None:
        repo = PlayerCharacterRepository(db_session)
        repo.save(USER_ID, campaign.id, character, inventory, None)
        db_session.flush()

        # Modify character HP
        character.hp = 5
        repo.update(USER_ID, campaign.id, character, inventory, None)
        db_session.flush()

        result = repo.get(USER_ID, campaign.id)
        assert result is not None
        assert result[0].hp == 5

    def test_update_nonexistent_raises(
        self, db_session: Session, character: Character, inventory: Inventory,
    ) -> None:
        repo = PlayerCharacterRepository(db_session)
        with pytest.raises(ValueError, match="Player character not found"):
            repo.update(999, "nonexistent", character, inventory, None)


class TestPlayerCharacterRepositoryDelete:
    """Test delete operations."""

    def test_delete_existing(
        self, db_session: Session, campaign: Campaign,
        character: Character, inventory: Inventory,
    ) -> None:
        repo = PlayerCharacterRepository(db_session)
        repo.save(USER_ID, campaign.id, character, inventory, None)
        db_session.flush()

        repo.delete(USER_ID, campaign.id)
        db_session.flush()

        assert repo.get(USER_ID, campaign.id) is None

    def test_delete_nonexistent_noop(self, db_session: Session) -> None:
        repo = PlayerCharacterRepository(db_session)
        repo.delete(999, "nonexistent")  # Should not raise
