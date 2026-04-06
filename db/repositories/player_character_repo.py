"""Persistence operations for player character entities."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.character import Character
from engine.inventory import Inventory
from engine.spells import SpellcasterState
from db.mappers import player_character_from_db, player_character_to_db
from db.models import PlayerCharacterRow


class PlayerCharacterRepository:
    """CRUD operations for player characters within campaigns."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        user_id: int,
        campaign_id: str,
        character: Character,
        inventory: Inventory,
        spellcaster: SpellcasterState | None,
    ) -> None:
        """Insert a new player character."""
        row = player_character_to_db(user_id, campaign_id, character, inventory, spellcaster)
        self._session.add(row)

    def get(
        self, user_id: int, campaign_id: str,
    ) -> tuple[Character, Inventory, SpellcasterState | None] | None:
        """Fetch a player's character in a campaign, or None if not found."""
        row = self._session.get(PlayerCharacterRow, (user_id, campaign_id))
        if row is None:
            return None
        _, character, inventory, spellcaster = player_character_from_db(row)
        return character, inventory, spellcaster

    def get_all_for_campaign(
        self, campaign_id: str,
    ) -> list[tuple[int, Character, Inventory, SpellcasterState | None]]:
        """Fetch all player characters in a campaign."""
        stmt = select(PlayerCharacterRow).where(
            PlayerCharacterRow.campaign_id == campaign_id,
        )
        rows = self._session.execute(stmt).scalars().all()
        return [player_character_from_db(r) for r in rows]

    def update(
        self,
        user_id: int,
        campaign_id: str,
        character: Character,
        inventory: Inventory,
        spellcaster: SpellcasterState | None,
    ) -> None:
        """Update an existing player character."""
        row = self._session.get(PlayerCharacterRow, (user_id, campaign_id))
        if row is None:
            msg = f"Player character not found: user={user_id}, campaign={campaign_id}"
            raise ValueError(msg)
        row.character_json = character.model_dump_json()
        row.inventory_json = inventory.model_dump_json()
        row.spellcaster_json = spellcaster.model_dump_json() if spellcaster else None

    def delete(self, user_id: int, campaign_id: str) -> None:
        """Delete a player character. No-op if not found."""
        row = self._session.get(PlayerCharacterRow, (user_id, campaign_id))
        if row is not None:
            self._session.delete(row)
