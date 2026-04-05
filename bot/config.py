"""Per-guild bot configuration model."""

from pydantic import BaseModel, Field


class GuildConfig(BaseModel):
    """Per-guild bot configuration."""

    guild_id: int
    category_name: str = Field(
        default="RealmAI Sessions",
        min_length=1,
        max_length=100,
    )
