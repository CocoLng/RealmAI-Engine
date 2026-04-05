"""NPC repository — CRUD for NPCs."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.mappers import npc_from_db, npc_to_db
from db.models import NPCRow
from world.npc import NPC


class NPCRepository:
    """Persistence operations for NPC entities."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, npc: NPC, campaign_id: str) -> None:
        """Insert a new NPC."""
        row = npc_to_db(npc, campaign_id)
        self._session.add(row)

    def get_by_name(self, name: str, campaign_id: str) -> NPC | None:
        """Fetch an NPC by name within a campaign, or None if not found."""
        stmt = select(NPCRow).where(
            NPCRow.campaign_id == campaign_id,
            NPCRow.name == name,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return npc_from_db(row)

    def list_by_campaign(self, campaign_id: str) -> list[NPC]:
        """List all NPCs in a campaign."""
        stmt = select(NPCRow).where(NPCRow.campaign_id == campaign_id)
        rows = self._session.execute(stmt).scalars().all()
        return [npc_from_db(r) for r in rows]

    def list_by_location(self, location_name: str, campaign_id: str) -> list[NPC]:
        """List all NPCs at a specific location in a campaign."""
        stmt = select(NPCRow).where(
            NPCRow.campaign_id == campaign_id,
            NPCRow.location_name == location_name,
        )
        rows = self._session.execute(stmt).scalars().all()
        return [npc_from_db(r) for r in rows]

    def update(self, npc: NPC, campaign_id: str) -> None:
        """Update an existing NPC (looked up by campaign_id + name)."""
        stmt = select(NPCRow).where(
            NPCRow.campaign_id == campaign_id,
            NPCRow.name == npc.name,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            msg = f"NPC '{npc.name}' not found in campaign '{campaign_id}'"
            raise ValueError(msg)
        row.race = npc.race.value
        row.char_class = npc.char_class.value if npc.char_class else None
        row.level = npc.level
        row.ability_scores = npc.ability_scores.model_dump()  # type: ignore[assignment]
        row.hp = npc.hp
        row.max_hp = npc.max_hp
        row.ac = npc.ac
        row.disposition = npc.disposition.value
        row.is_alive = npc.is_alive
        row.description = npc.description
        row.personality = npc.personality
        row.location_name = npc.location_name

    def delete(self, name: str, campaign_id: str) -> None:
        """Delete an NPC by name within a campaign."""
        stmt = select(NPCRow).where(
            NPCRow.campaign_id == campaign_id,
            NPCRow.name == name,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is not None:
            self._session.delete(row)
