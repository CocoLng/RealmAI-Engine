"""Location domain model.

Represents places in the game world that players can visit.
"""

from pydantic import BaseModel, Field


class Location(BaseModel):
    """A location in the game world."""

    name: str
    description: str = ""
    connections: list[str] = Field(default_factory=list)
    exit_aliases: dict[str, list[str]] = Field(default_factory=dict)
    npcs_present: list[str] = Field(default_factory=list)
    items_available: list[str] = Field(default_factory=list)
    item_descriptions: dict[str, str] = Field(default_factory=dict)
    state_flags: dict[str, bool] = Field(default_factory=dict)
    unlocked_exits: list[str] = Field(default_factory=list)
    generated: bool = True
    """False for lightweight stubs awaiting first-visit hydration."""
