"""Combat zone domain model.

Abstract positional unit used by the combat system in place of a 5-ft grid.
A combat-enabled ``Location`` carries a list of ``Zone`` entries; each
combatant occupies exactly one zone at a time, and movement is validated
against an adjacency graph defined on the ``Location`` itself.

Pure data — no engine logic lives here.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class ZoneTag(StrEnum):
    """Optional environmental modifier applied to everyone standing in a zone."""

    COVER = "cover"
    """+2 AC versus ranged attacks targeting creatures in this zone."""

    DIFFICULT_TERRAIN = "difficult_terrain"
    """Movement into or through this zone costs double."""

    ELEVATED = "elevated"
    """Advantage on ranged attacks originating from this zone."""

    HAZARD = "hazard"
    """1d4 damage on entering or starting a turn in this zone."""

    OBSCURED = "obscured"
    """Disadvantage on attacks targeting creatures inside this zone."""


class Zone(BaseModel):
    """A named region within a combat-enabled Location.

    Zones form an undirected adjacency graph. The ``Location`` model owns
    graph-wide integrity validation; individual ``Zone`` instances are
    simple data holders.
    """

    name: str = Field(min_length=1)
    description: str = ""
    adjacent_zone_names: list[str] = Field(default_factory=list)
    tags: list[ZoneTag] = Field(default_factory=list)

    def has_tag(self, tag: ZoneTag) -> bool:
        """Return ``True`` if ``tag`` is present on this zone."""
        return tag in self.tags
