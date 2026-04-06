"""Campaign domain model.

Groups players, NPCs, locations, and quests into a single game session.
"""

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class Campaign(BaseModel):
    """A campaign (game session) grouping all world state."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    created_at: datetime = Field(default_factory=datetime.now)
    player_names: list[str] = []
    current_location: str | None = None
    interaction_count: int = 0
    combat_state_json: str | None = None
