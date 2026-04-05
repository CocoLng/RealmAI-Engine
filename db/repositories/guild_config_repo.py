"""Persistence operations for GuildConfig entities."""

from sqlalchemy.orm import Session

from bot.config import GuildConfig
from db.mappers import guild_config_from_db, guild_config_to_db
from db.models import GuildConfigRow


class GuildConfigRepository:
    """CRUD operations for per-guild bot configuration."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, guild_id: int) -> GuildConfig | None:
        """Fetch config for a guild, or None if not found."""
        row = self._session.get(GuildConfigRow, guild_id)
        if row is None:
            return None
        return guild_config_from_db(row)

    def save(self, config: GuildConfig) -> None:
        """Insert a new guild config."""
        row = guild_config_to_db(config)
        self._session.add(row)

    def upsert(self, config: GuildConfig) -> None:
        """Insert or update a guild config."""
        row = guild_config_to_db(config)
        self._session.merge(row)

    def delete(self, guild_id: int) -> None:
        """Delete a guild config. No-op if not found."""
        row = self._session.get(GuildConfigRow, guild_id)
        if row is not None:
            self._session.delete(row)
