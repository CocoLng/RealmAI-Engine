"""Location domain model.

Represents places in the game world that players can visit.
"""

from pydantic import BaseModel


class Location(BaseModel):
    """A location in the game world."""

    name: str
    description: str = ""
    connections: list[str] = []
    npcs_present: list[str] = []
    items_available: list[str] = []
